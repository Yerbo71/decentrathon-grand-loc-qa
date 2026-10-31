"""Write the corrected ARB: only human-approved changes, each with an `x-locqa` review flag in its metadata."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from . import arb, icu


def load_decisions(path: Path) -> dict[str, dict]:
    """decisions.jsonl from the review page; the last decision for a key wins."""
    out = {}
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        if line.strip():
            d = json.loads(line)
            out[d["key"]] = d
    return out


def apply(source: arb.ArbFile, target: arb.ArbFile, decisions: dict[str, dict]) -> tuple[list[tuple[str, object]], dict]:
    """Rebuild target entries in their original order: duplicates collapsed to the effective value, approved
    changes applied, keys missing in the target appended at the end of their source section."""
    changed, skipped = [], []
    entries: list[tuple[str, object]] = []
    seen: set[str] = set()
    meta_written: set[str] = set()
    for e in target.entries:
        if e.key == "@@comment":
            entries.append((e.key, e.value))
            continue
        if e.key in seen:
            continue  # duplicate key: jsonDecode keeps the last value, which is what target.strings holds
        seen.add(e.key)
        if e.key.startswith("@"):
            base = e.key[1:]
            if base in decisions and _approved(decisions[base]):
                continue  # rewritten below together with the value
            entries.append((e.key, e.value))
            continue
        value = target.strings.get(e.key, e.value)
        d = decisions.get(e.key)
        if d and _approved(d):
            new = d["final"]
            problem = _problem(source.strings.get(e.key), new)
            if problem:
                skipped.append({"key": e.key, "reason": problem})
            else:
                entries.append((e.key, new))
                entries.append(("@" + e.key, _meta(target.meta.get(e.key), d, value)))
                meta_written.add(e.key)
                changed.append(e.key)
                continue
        entries.append((e.key, value))
    # keys that exist only in the source (untranslated) and got an approved translation
    for key, d in decisions.items():
        if key not in seen and key in source.strings and _approved(d):
            problem = _problem(source.strings[key], d["final"])
            if problem:
                skipped.append({"key": key, "reason": problem})
                continue
            entries.append((key, d["final"]))
            entries.append(("@" + key, _meta(None, d, None)))
            changed.append(key)
    return entries, {"changed": changed, "skipped": skipped,
                     "duplicates_collapsed": sorted(target.duplicates)}


def _approved(d: dict) -> bool:
    return d.get("decision") in ("accept", "edit") and isinstance(d.get("final"), str)


def _problem(src: str | None, new: str) -> str | None:
    if src is not None and icu.tokens(src) != icu.tokens(new):
        return "плейсхолдеры не совпадают с исходником"
    try:
        icu.parse(new)
    except icu.IcuError as e:
        return f"невалидный ICU: {e}"
    return None


def _meta(existing: dict | None, d: dict, original) -> dict:
    meta = dict(existing or {})
    meta["x-locqa"] = {
        "reviewed_by_human": True,
        "reviewer": d.get("reviewer") or "",
        "decision": d["decision"],
        "original": original,
        "issues": d.get("issues", []),
        "reviewed_at": d.get("ts") or datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    return meta
