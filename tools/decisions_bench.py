"""Benchmark OpenRouter decision models on the labelled simulation sample.

  python tools/decisions_bench.py [--models a,b,c] [--limit N] [--skip-prefer]

Triage: every sample string -> has_problem / issue_type / severity, scored against
  strong positives = injected errors + rule findings + hand-picked suspects, and
  silver positives = findings of the simulated LLM run c4 (≈85–90% real by manual review).
Prefer: c5 fixes -> "suggestion vs current"; the same pairs swapped as a position-bias control.
Results: out/decisions_bench/<model>.json and a summary table on stdout. Costs come from usage.cost.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools" / "sim"))

from locqa import decide, llm, profiles  # noqa: E402
from locqa.checks import rules  # noqa: E402
import sim  # noqa: E402

DEFAULT_MODELS = ["typesafe/jev-1.14", "perplexity/pplx-decider-v1.2", "liquid/d1-ultra",
                  "nace-ai/drex-max", "openai/gpt-6-luna-decisions"]
OUT = ROOT / "out" / "decisions_bench"


def auc(pos: list[float], neg: list[float]) -> float | None:
    if not pos or not neg:
        return None
    wins = sum((p > n) + 0.5 * (p == n) for p in pos for n in neg)
    return wins / (len(pos) * len(neg))


def labels(ru, kk, uz, en):
    keys = sim.sample_keys(ru)
    rule_f = [f for f in rules.run(rules.Context(ru, kk, profiles.KK, {"uz": uz, "en": en})) if f.key in set(keys)]
    rule_type = {f.key: f.issue_type for f in rule_f}
    strong = set(sim.INJECT) | set(rule_type) | {k for k in sim.SUSPECT if k in set(keys)}
    silver = set()
    extra = ROOT / "out" / "sim" / "c4" / "extra.md"
    if extra.exists():
        silver = set(re.findall(r"`([^`]+)`", extra.read_text(encoding="utf-8"))) - strong
    known_type = {**rule_type, **{k: next(iter(t)) for k, (_, t) in sim.INJECT.items() if len(t) == 1}}
    return keys, strong, silver, known_type


def triage(client, model, ru, kk, uz, en, keys, limit):
    keys = keys[:limit] if limit else keys
    q = decide.triage_questions()
    jobs = [(k, decide._state(k, ru, kk, {"en": en, "uz": uz}), q) for k in keys]
    return decide.run_many(client, model, jobs)


def prefer(client, model, ru, kk, uz, en):
    fixes = ROOT / "out" / "sim" / "c5" / "responses"
    pairs = []
    for p in sorted(fixes.glob("*.txt")):
        data = json.loads(re.search(r"\{.*\}", p.read_text(encoding="utf-8"), re.S).group(0))
        pairs += [(x["key"], x["suggestion"]) for x in data.get("fixes", []) if x.get("suggestion")]
    jobs = []
    for k, s in pairs:
        st = decide._state(k, ru, kk, {"en": en, "uz": uz})
        jobs.append((("fix", k), st, decide.prefer_questions(s)))
        swapped = dict(st, kk=s)  # control: present the fix as "current" and the old text as the proposal
        jobs.append((("swap", k), swapped, decide.prefer_questions(kk.strings.get(k) or "")))
    return decide.run_many(client, model, jobs)


def main(argv):
    opt = lambda n, d=None: argv[argv.index(n) + 1] if n in argv else d
    models = opt("--models", ",".join(DEFAULT_MODELS)).split(",")
    limit = int(opt("--limit", 0))
    client = llm.Client()
    if not client.available:
        sys.exit("OPENROUTER_API_KEY is not set: put it into .env (see .env.example)")
    ru, kk, uz, en = sim.load_all()
    keys, strong, silver, known_type = labels(ru, kk, uz, en)
    OUT.mkdir(parents=True, exist_ok=True)
    rows = []
    for model in models:
        before = client.usage.cost_usd
        res = triage(client, model, ru, kk, uz, en, keys, limit)
        errors = [e for _, a, e in res if e]
        ok = [(k, a) for k, a, e in res if a]
        p = {k: decide.p_true(a, "has_problem") for k, a in ok}
        t = {k: decide.top_choice(a, "issue_type")[0] for k, a in ok}
        neg = [v for k, v in p.items() if k not in strong and k not in silver]
        row = {
            "model": model, "answered": len(ok), "errors": len(errors), "first_error": errors[0] if errors else None,
            "auc_strong": auc([p[k] for k in strong if k in p], neg),
            "auc_silver": auc([p[k] for k in silver if k in p], neg),
            "recall_strong@0.5": _share([p[k] >= 0.5 for k in strong if k in p]),
            "flag_rate_unlabelled@0.5": _share([v >= 0.5 for v in neg]),
            "type_acc_known": _share([t[k] == known_type[k] for k in known_type if k in t]),
            "injected": {k: (round(p[k], 2), t[k]) for k in sim.INJECT if k in p},
        }
        if "--skip-prefer" not in argv:
            pr = prefer(client, model, ru, kk, uz, en)
            fix_pref = [decide.top_choice(a, "preferred")[0] for (kind, _), a, e in pr if kind == "fix" and a]
            swap_pref = [decide.top_choice(a, "preferred")[0] for (kind, _), a, e in pr if kind == "swap" and a]
            row["prefer_fix_share"] = _share([c == "suggestion" for c in fix_pref])
            row["prefer_swapped_old_share"] = _share([c == "suggestion" for c in swap_pref])  # should be low
        row["cost_usd"] = round(client.usage.cost_usd - before, 4)
        rows.append(row)
        (OUT / f"{model.replace('/', '__')}.json").write_text(
            json.dumps({"summary": row, "triage": {k: a for k, a in ok}}, ensure_ascii=False, indent=1), encoding="utf-8")
        print(json.dumps(row, ensure_ascii=False))
    print("\nmodel | auc_strong | auc_silver | recall@0.5 | flag_rate | type_acc | prefer_fix | swapped_old | $")
    for r in rows:
        print(" | ".join(str(r.get(c)) for c in ("model", "auc_strong", "auc_silver", "recall_strong@0.5",
                                                   "flag_rate_unlabelled@0.5", "type_acc_known", "prefer_fix_share",
                                                   "prefer_swapped_old_share", "cost_usd")))
    print("total usage:", client.usage.as_dict())


def _share(xs):
    xs = list(xs)
    return round(sum(xs) / len(xs), 3) if xs else None


if __name__ == "__main__":
    main(sys.argv[1:])
