"""Seed known errors into a translation and measure per-type recall of the rule checks (no LLM, $0).

  python tools/inject.py --source intl_ru.arb --target intl_kk.arb [--ref intl_uz.arb --ref intl_en.arb] [--n 50] [--seed 7]

Each error is planted in a string where the rules found nothing before, so recall is clean; findings on untouched
strings are counted to make sure injection does not create false alarms elsewhere.
"""
from __future__ import annotations

import argparse
import json
import random
import re
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from locqa import arb, icu, profiles  # noqa: E402
from locqa.checks import rules  # noqa: E402

KK_TO_RU = str.maketrans({"қ": "к", "ғ": "г", "ң": "н", "ә": "а", "ө": "о", "ұ": "у", "ү": "у", "і": "и"})
TAILS = [" режимін қосу және жалғастыру", " бөліміне өту үшін басыңыз", " туралы толық ақпарат алу үшін"]


def inject(kind: str, key: str, ru: str, kk: str, refs: dict, rnd: random.Random) -> str | None:
    if kind == "placeholder_mismatch":
        ph = re.findall(r"\{\w+\}", kk)
        return kk.replace(ph[0], ph[0][1:-1], 1) if ph else None
    if kind == "suffix_harmony":
        ph = re.findall(r"\{\w+\}(?![-\w])", kk)
        return kk.replace(ph[0], ph[0] + "-ға", 1) if ph else None
    if kind == "orthography":
        words = [w for w in re.findall(r"\w+", kk) if re.search("[қғңәөұүі]", w) and len(w) > 3]
        if not words:
            return None
        w = rnd.choice(words)
        return re.sub(rf"\b{re.escape(w)}\b", w.translate(KK_TO_RU), kk, count=1)
    if kind == "wrong_language":
        uz = refs.get("uz")
        v = uz.strings.get(key) if uz else None
        return v if v and v != kk and re.search(r"[a-zA-Z]{4}", v) and icu.tokens(v) == icu.tokens(ru) else None
    if kind == "same_as_source":
        return ru if len(re.findall(r"[А-Яа-яЁё]+", ru)) >= 3 and ru != kk else None
    if kind == "length_overflow":
        return kk + rnd.choice(TAILS) if len(ru) <= 12 and "\n" not in ru and "{" not in ru else None
    if kind == "untranslated":
        return ""
    if kind == "plural_mismatch":
        return "{count, plural, one{# зат}}" if "{count}" in ru else None
    raise ValueError(kind)


KINDS = ["plural_mismatch", "placeholder_mismatch", "suffix_harmony", "orthography", "wrong_language", "same_as_source",
         "length_overflow", "untranslated"]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", required=True)
    ap.add_argument("--target", required=True)
    ap.add_argument("--ref", action="append", default=[])
    ap.add_argument("--n", type=int, default=50)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--out", default=str(ROOT / "out" / "inject" / "result.json"))
    a = ap.parse_args()
    rnd = random.Random(a.seed)
    src, tgt = arb.load(a.source), arb.load(a.target)
    refs = {(r := arb.load(p)).locale or Path(p).stem: r for p in a.ref}
    prof = profiles.get(tgt.locale)
    before = {f.key for f in rules.run(rules.Context(src, tgt, prof, refs))}
    clean = [k for k in tgt.strings if k in src.strings and k not in before and tgt.strings[k]]
    rnd.shuffle(clean)
    planted: dict[str, str] = {}
    for kind in KINDS:
        n = 0
        for k in clean:
            if n >= a.n:
                break
            if k in planted:
                continue
            v = inject(kind, k, src.strings[k], tgt.strings[k], refs, rnd)
            if v is None or v == tgt.strings[k]:
                continue
            tgt.strings[k] = v
            planted[k] = kind
            n += 1
    found = rules.run(rules.Context(src, tgt, prof, refs))
    by_key: dict[str, set] = {}
    for f in found:
        by_key.setdefault(f.key, set()).add(f.issue_type)
    res = {}
    for kind in KINDS:
        keys = [k for k, t in planted.items() if t == kind]
        typed = sum(kind in by_key.get(k, set()) for k in keys)
        anyt = sum(k in by_key for k in keys)
        res[kind] = {"planted": len(keys), "recall_typed": round(typed / len(keys), 3) if keys else None,
                     "recall_any_type": round(anyt / len(keys), 3) if keys else None,
                     "missed": [k for k in keys if kind not in by_key.get(k, set())][:5]}
    new_fp = [k for k in by_key if k not in planted and k not in before]
    out = {"seed": a.seed, "per_type": res, "new_findings_on_untouched_strings": len(new_fp), "examples_new_fp": new_fp[:5]}
    Path(a.out).parent.mkdir(parents=True, exist_ok=True)
    Path(a.out).write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"{'type':24} planted  recall(type)  recall(any)")
    for kind, r in res.items():
        print(f"{kind:24} {r['planted']:7}  {r['recall_typed']!s:12}  {r['recall_any_type']!s:10}  missed e.g. {r['missed'][:3]}")
    print("new findings on untouched strings:", len(new_fp), new_fp[:5])


if __name__ == "__main__":
    main()
