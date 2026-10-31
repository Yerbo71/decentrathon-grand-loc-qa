"""LLM review layer: batches strings by @@comment section, asks the model for findings, validates its answer.

The transport is pluggable: `OpenRouterBackend` for real runs, `FileBackend` to dump requests to disk and read
answers back (used to dry-run the pipeline with a simulated model, and to replay recorded answers).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from . import icu
from .arb import ArbFile
from .model import ISSUE_TYPES, SEVERITIES, Finding

PROMPTS = Path(__file__).parent / "prompts"


@dataclass
class Batch:
    id: str
    section: str | None
    keys: list[str]


@dataclass
class ParseReport:
    batch_id: str
    ok: bool
    error: str | None = None
    dropped: list[dict] = field(default_factory=list)  # model findings rejected by validation, with reason
    source_notes: list[dict] = field(default_factory=list)  # problems the model saw in the source language


def make_batches(source: ArbFile, keys: list[str] | None = None, size: int = 40) -> list[Batch]:
    keys = keys if keys is not None else list(source.strings)
    by_sec: dict[str | None, list[str]] = {}
    for k in keys:
        by_sec.setdefault(source.section_of(k), []).append(k)
    out = []
    for sec, ks in by_sec.items():
        n = max(1, -(-len(ks) // size))
        step = -(-len(ks) // n)  # equal-sized chunks instead of one tiny tail batch
        for i in range(n):
            chunk = ks[i * step:(i + 1) * step]
            if chunk:
                slug = re.sub(r"\W+", "_", (sec or "none")).strip("_").lower()
                out.append(Batch(f"{slug}_{i + 1:02d}", sec, chunk))
    return out


def load_prompt(version: str) -> tuple[str, str]:
    """Prompt file = system part, a line '=== USER ===', user template with {section} {items_json} {glossary} {known}."""
    text = (PROMPTS / f"judge_{version}.md").read_text(encoding="utf-8")
    system, user = text.split("=== USER ===", 1)
    return system.strip(), user.strip()


def build_messages(batch: Batch, version: str, source: ArbFile, target: ArbFile, refs: dict[str, ArbFile],
                   glossary_text: str = "", known: list[Finding] | None = None, hints: list | None = None) -> list[dict]:
    system, user = load_prompt(version)
    found: dict[str, set] = {}
    for f in known or []:
        found.setdefault(f.key, set()).add(f.issue_type)
    tips: dict[str, list] = {}
    for h in hints or []:
        tips.setdefault(h.key, []).append(h.text)
    items = []
    for k in batch.keys:
        item = {"key": k, "ru": source.strings.get(k), "kk": target.strings.get(k)}
        for code, ref in refs.items():
            if ref.strings.get(k) is not None:
                item[code] = ref.strings[k]
        if k in found:
            item["already_found"] = sorted(found[k])
        if k in tips:
            item["tool_hints"] = tips[k]
        items.append(item)
    known_lines = "\n".join(f"- {f.key}: {f.issue_type}" for f in (known or []) if f.key in set(batch.keys)) or "(нет)"
    user = (user.replace("{section}", batch.section or "—")
                .replace("{items_json}", json.dumps(items, ensure_ascii=False, indent=1))
                .replace("{glossary}", glossary_text or "(нет)")
                .replace("{known}", known_lines))
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def parse_answer(text: str, batch: Batch, source: ArbFile, target: ArbFile,
                 allowed_types: tuple[str, ...] = ISSUE_TYPES) -> tuple[list[Finding], ParseReport]:
    rep = ParseReport(batch.id, ok=True)
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        rep.ok, rep.error = False, "в ответе нет JSON-объекта"
        return [], rep
    try:
        data = json.loads(m.group(0))
    except json.JSONDecodeError as e:
        rep.ok, rep.error = False, f"битый JSON: {e}"
        return [], rep
    raw = data.get("findings")
    if not isinstance(raw, list):
        rep.ok, rep.error = False, "нет массива findings"
        return [], rep
    rep.source_notes = [n for n in data.get("source_notes") or [] if isinstance(n, dict) and n.get("key") in set(batch.keys)]
    keys = set(batch.keys)
    out = []
    for f in raw:
        reason = _invalid(f, keys, source, allowed_types)
        if reason:
            rep.dropped.append({"finding": f, "reason": reason})
            continue
        sug = f.get("suggestion") or None
        if isinstance(sug, str) and sug.strip().lower() in ("none", "null"):
            sug = None
        if sug and icu.tokens(sug) != icu.tokens(source.strings[f["key"]]):
            rep.dropped.append({"finding": f, "reason": "suggestion ломает плейсхолдеры (suggestion обнулён)"})
            sug = None
        if sug and _commentary(sug, source.strings[f["key"]], target.strings.get(f["key"], "")):
            # real models sometimes put notes or alternatives into the fix: «сатып алушы (жинаушы)»
            rep.dropped.append({"finding": f, "reason": "suggestion содержит комментарий или варианты (suggestion обнулён)"})
            sug = None
        out.append(Finding(
            key=f["key"], issue_type=f["issue_type"], severity=f.get("severity", "medium"),
            source=source.strings[f["key"]], target=target.strings.get(f["key"], ""),
            evidence=_evidence(f), suggestion=sug,
            confidence=float(f.get("confidence", 0.5)), detector="llm",
        ))
    return out, rep


def _commentary(sug: str, src: str, tgt: str) -> bool:
    for mark in ("(", " / ", " или ", "—", "[", "»", "«"):
        if mark in sug and mark not in src and mark not in tgt:
            return True
    return False


def _evidence(f: dict) -> str:
    ev = str(f.get("evidence", "")).strip()
    bt = str(f.get("back_translation") or "").strip()
    return f"Сейчас по-казахски написано: «{bt}». {ev}" if bt else ev


def _invalid(f, keys, source, allowed_types) -> str | None:
    if not isinstance(f, dict):
        return "находка не объект"
    if f.get("key") not in keys:
        return f"ключ {f.get('key')!r} не из этого батча"
    if f.get("issue_type") not in allowed_types:
        return f"недопустимый issue_type {f.get('issue_type')!r}"
    if f.get("severity", "medium") not in SEVERITIES:
        return f"недопустимый severity {f.get('severity')!r}"
    try:
        c = float(f.get("confidence", 0.5))
    except (TypeError, ValueError):
        return "confidence не число"
    if not 0 <= c <= 1:
        return "confidence вне [0,1]"
    if len(str(f.get("evidence", "")).strip()) < 10:
        return "пустой или слишком короткий evidence"
    return None


class FileBackend:
    """Writes each request to <dir>/requests/<id>.json and reads <dir>/responses/<id>.txt if present."""

    def __init__(self, root: str | Path, model: str = "simulated"):
        self.root = Path(root)
        self.model = model
        (self.root / "requests").mkdir(parents=True, exist_ok=True)
        (self.root / "responses").mkdir(parents=True, exist_ok=True)

    def write_request(self, batch_id: str, messages: list[dict]) -> Path:
        p = self.root / "requests" / f"{batch_id}.json"
        p.write_text(json.dumps({"model": self.model, "temperature": 0, "response_format": {"type": "json_object"},
                                 "messages": messages}, ensure_ascii=False, indent=1), encoding="utf-8")
        return p

    def read_response(self, batch_id: str) -> str | None:
        p = self.root / "responses" / f"{batch_id}.txt"
        return p.read_text(encoding="utf-8") if p.exists() else None


class OpenRouterBackend:
    def __init__(self, client):
        self.client = client

    def complete(self, messages: list[dict]) -> str:
        data = self.client.chat_json(messages[0]["content"], messages[1]["content"])
        return json.dumps(data, ensure_ascii=False)


def run_judge(client, source: ArbFile, target: ArbFile, refs: dict[str, ArbFile], version: str = "v3",
              glossary_text: str = "", known: list[Finding] | None = None, hints: list | None = None,
              size: int = 40, workers: int = 4) -> tuple[list[Finding], list[ParseReport]]:
    """Real run through OpenRouter chat completions; answers are cached by the client."""
    from concurrent.futures import ThreadPoolExecutor

    batches = make_batches(source, size=size)

    def one(b: Batch):
        msgs = build_messages(b, version, source, target, refs, glossary_text, known, hints)
        try:
            data = client.chat_json(msgs[0]["content"], msgs[1]["content"])
        except Exception as e:  # noqa: BLE001
            return [], ParseReport(b.id, ok=False, error=str(e)[:300])
        return parse_answer(json.dumps(data, ensure_ascii=False), b, source, target)

    findings, reports = [], []
    with ThreadPoolExecutor(workers) as ex:
        for fs, rep in ex.map(one, batches):
            findings += fs
            reports.append(rep)
    return findings, reports
