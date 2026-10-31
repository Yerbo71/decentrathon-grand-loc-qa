"""Build the human review queue: findings + proposed fixes that passed the automatic gate, most valuable first."""
from __future__ import annotations

import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from . import decide, fix
from .arb import ArbFile
from .checks import hints as hintmod, rules
from .model import Finding

PROMPTS = Path(__file__).parent / "prompts"
SEV = {"high": 0, "medium": 1, "low": 2}


def propose_fixes(client, findings: list[Finding], ctx: rules.Context, glossary_text: str,
                  version: str = "v2", size: int = 20, model: str | None = None) -> dict[str, dict]:
    """Ask the fix model for findings without a suggestion. Returns key -> fix dict from the model."""
    system, user = (PROMPTS / f"fix_{version}.md").read_text(encoding="utf-8").split("=== USER ===")
    frag = {h.key: h.text for h in hintmod.fragments(ctx)}
    todo = [f for f in findings if not f.suggestion]
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]

    def one(batch):
        items = [{"key": f.key, "issue_type": f.issue_type, "problem": f.evidence, "ru": f.source, "kk": f.target,
                  **{c: r.strings.get(f.key) for c, r in ctx.refs.items() if r.strings.get(f.key)},
                  "section": ctx.source.section_of(f.key),
                  **({"fragment_context": frag[f.key]} if f.key in frag else {})} for f in batch]
        u = user.strip().replace("{glossary}", glossary_text or "(нет)").replace(
            "{items_json}", json.dumps(items, ensure_ascii=False, indent=1))
        try:
            data = client.chat_json(system.strip(), u, model=model, max_tokens=8000)
        except Exception:  # noqa: BLE001 — a failed batch just leaves those findings without a proposal
            return {}
        return {x.get("key"): x for x in data.get("fixes", []) if isinstance(x, dict)}

    out: dict[str, dict] = {}
    with ThreadPoolExecutor(4) as ex:
        for part in ex.map(one, batches):
            out.update(part)
    return out


def build_queue(findings: list[Finding], fixes: dict[str, dict], ctx: rules.Context,
                forbidden: list[tuple[str, str]], prefer: dict[str, float] | None = None) -> list[dict]:
    """One card per key (several findings of the same key are merged); suggestion only if it passes the gate."""
    by_key: dict[str, list[Finding]] = {}
    for f in findings:
        by_key.setdefault(f.key, []).append(f)
    queue = []
    for key, fs in by_key.items():
        fs.sort(key=lambda f: (SEV[f.severity], -f.confidence))
        main = fs[0]
        proposal = next((f.suggestion for f in fs if f.suggestion), None)
        model_fix = fixes.get(key) or {}
        note = model_fix.get("note")
        if not proposal and model_fix.get("suggestion"):
            proposal = model_fix["suggestion"]
        refs = {c: r.strings.get(key) for c, r in ctx.refs.items() if r.strings.get(key)}
        problems = fix.validate(main, proposal, ctx.profile, refs, forbidden) if proposal else []
        card = {
            "key": key, "section": ctx.source.section_of(key), "severity": main.severity,
            "issues": [{"type": f.issue_type, "evidence": f.evidence, "confidence": f.confidence, "detector": f.detector}
                       for f in fs],
            "ru": ctx.source.strings.get(key, main.source), "kk": ctx.target.strings.get(key, main.target),
            **refs,
            "suggestion": None if problems else proposal,
            "rejected_suggestion": proposal if problems else None, "gate_problems": problems,
            "note": note, "needs_code": bool(model_fix.get("needs_code")), "needs_context": bool(model_fix.get("needs_context")),
            "keep": bool(model_fix.get("keep")),
            "prefer": (prefer or {}).get(key),
        }
        queue.append(card)
    # most valuable first: high with a ready fix, then by confidence; cards without any proposal last
    queue.sort(key=lambda c: (c["suggestion"] is None, SEV[c["severity"]], -(c["prefer"] or 0),
                              -max(i["confidence"] for i in c["issues"])))
    return queue


def prefer_scores(client, model: str, queue_like: list[tuple[str, str]], ctx: rules.Context) -> dict[str, float]:
    """P(native speaker prefers the suggestion) from a decision model (drex-max did best in our benchmark)."""
    jobs = [(k, decide._state(k, ctx.source, ctx.target, ctx.refs), decide.prefer_questions(s)) for k, s in queue_like]
    out = {}
    for k, ans, err in decide.run_many(client, model, jobs):
        a = (ans or {}).get("preferred") or {}
        probs = a.get("probabilities") or {}
        if probs:
            out[k] = round(float(probs.get("suggestion", 0)), 3)
        elif a.get("choice"):
            out[k] = 1.0 if a["choice"] == "suggestion" else 0.0
    return out


def load_findings(path: Path) -> list[Finding]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [Finding(**{k: v for k, v in d.items() if k in Finding.__dataclass_fields__}) for d in data]
