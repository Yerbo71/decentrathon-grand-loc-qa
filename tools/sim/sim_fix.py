"""Dry-run of the fix stage: rule findings without a suggestion -> fix prompt -> automatic validation.

  python tools/sim/sim_fix.py prepare <cycle> [--size 20]
  python tools/sim/sim_fix.py evaluate <cycle>
"""
from __future__ import annotations

import csv
import json
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from locqa import arb, fix, profiles  # noqa: E402
from locqa.checks import rules  # noqa: E402
from locqa.judge import FileBackend  # noqa: E402

H = ROOT / "grand-loc-qa-handout-2026-10-27" / "handout"
OUT = ROOT / "out" / "sim"
NEED_FIX = {"wrong_language", "same_as_source", "untranslated", "length_overflow", "suffix_harmony"}


def setup():
    ru, kk, uz, en = (arb.load(H / f"intl_{l}.arb") for l in ("ru", "kk", "uz", "en"))
    found = rules.run(rules.Context(ru, kk, profiles.KK, {"uz": uz, "en": en}))
    todo = [f for f in found if f.issue_type in NEED_FIX and not f.suggestion and f.severity != "low"]
    rows = list(csv.DictReader(open(ROOT / "glossary.csv", encoding="utf-8")))
    return ru, kk, uz, en, todo, rows


def prepare(cycle: str, size: int, version: str = "v1"):
    ru, kk, uz, en, todo, rows = setup()
    system, user = (ROOT / "locqa" / "prompts" / f"fix_{version}.md").read_text(encoding="utf-8").split("=== USER ===")
    from locqa.checks import hints as hintmod
    frag = {h.key: h.text for h in hintmod.fragments(rules.Context(ru, kk, profiles.KK, {"en": en, "uz": uz}))}
    gtext = "\n".join(f"- {r['ru']} → {r['kk']}" + (f" (нельзя: {r['forbidden_kk']})" if r['forbidden_kk'] else "") for r in rows)
    be = FileBackend(OUT / cycle, model="anthropic/claude-opus-5.5 (simulated)")
    batches = [todo[i:i + size] for i in range(0, len(todo), size)]
    meta = []
    for i, b in enumerate(batches, 1):
        items = [{"key": f.key, "issue_type": f.issue_type, "problem": f.evidence, "ru": f.source, "kk": f.target,
                  "en": en.strings.get(f.key), "uz": uz.strings.get(f.key), "section": ru.section_of(f.key),
                  **({"fragment_context": frag[f.key]} if f.key in frag and version != "v1" else {})} for f in b]
        u = user.strip().replace("{glossary}", gtext).replace("{items_json}", json.dumps(items, ensure_ascii=False, indent=1))
        be.write_request(f"fix_{i:02d}", [{"role": "system", "content": system.strip()}, {"role": "user", "content": u}])
        meta.append({"id": f"fix_{i:02d}", "keys": [f.key for f in b]})
    (OUT / cycle / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{cycle}: {len(todo)} items in {len(batches)} batches; by type {Counter(f.issue_type for f in todo)}")


def evaluate(cycle: str):
    ru, kk, uz, en, todo, rows = setup()
    forbidden = [(r["ru"], w.strip()) for r in rows for w in r["forbidden_kk"].split(";") if w.strip()]
    by_key = {f.key: f for f in todo}
    be = FileBackend(OUT / cycle)
    meta = json.loads((OUT / cycle / "meta.json").read_text(encoding="utf-8"))
    stats, lines = Counter(), []
    per_type = Counter()
    for b in meta:
        text = be.read_response(b["id"])
        if text is None:
            stats["missing_batch"] += 1
            continue
        try:
            data = json.loads(re.search(r"\{.*\}", text, re.S).group(0))
        except Exception as e:  # noqa: BLE001
            stats["parse_error"] += 1
            continue
        got = {x.get("key"): x for x in data.get("fixes", [])}
        for k in b["keys"]:
            f, x = by_key[k], got.get(k)
            if x is None:
                stats["no_answer"] += 1
                lines.append(f"- NO ANSWER `{k}` [{f.issue_type}]")
                continue
            if x.get("keep"):
                stats["keep"] += 1
                lines.append(f"- KEEP `{k}` [{f.issue_type}] «{f.target}» :: {x.get('note')}")
                continue
            if x.get("needs_code"):
                stats["needs_code"] += 1
            if x.get("needs_context"):
                stats["needs_context"] += 1
            s = x.get("suggestion")
            if not s:
                stats["null"] += 1
                lines.append(f"- NULL `{k}` [{f.issue_type}] ru=«{f.source}» :: {x.get('note')}")
                continue
            refs = {c: r.strings.get(k) for c, r in (("en", en), ("uz", uz)) if r.strings.get(k)}
            probs = fix.validate(f, s, profiles.KK, refs, forbidden)
            stats["fail" if probs else "pass"] += 1
            per_type[(f.issue_type, "fail" if probs else "pass")] += 1
            lines.append(f"- {'FAIL' if probs else 'PASS'} `{k}` [{f.issue_type}] ru=«{f.source}» kk=«{f.target}» → «{s}»"
                         + (f" :: {probs}" if probs else "") + f" :: note: {x.get('note')}")
    (OUT / cycle / "fixes.md").write_text("\n".join(lines), encoding="utf-8")
    print(json.dumps({"items": len(todo), **stats, "per_type": {f"{a}/{b}": c for (a, b), c in sorted(per_type.items())}},
                     ensure_ascii=False, indent=1))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "prepare":
        prepare(a[1], int(a[a.index("--size") + 1]) if "--size" in a else 20, a[a.index("--prompt") + 1] if "--prompt" in a else "v1")
    else:
        evaluate(a[1])
