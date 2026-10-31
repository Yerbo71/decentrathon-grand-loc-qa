"""Dry-run of the LLM review layer with a simulated model.

  python tools/sim/sim.py prepare <cycle> <prompt_version> [--with-rules] [--glossary FILE] [--size N]
  python tools/sim/sim.py evaluate <cycle>

`prepare` writes requests to out/sim/<cycle>/requests; a stand-in model writes out/sim/<cycle>/responses/<id>.txt.
`evaluate` parses the answers exactly like a real run and scores them against injected errors (exact ground truth),
rule findings and a hand-made list of suspicious organic strings (silver labels).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from locqa import arb, judge, profiles  # noqa: E402
from locqa.checks import hints as hintmod, rules  # noqa: E402
import csv  # noqa: E402

H = ROOT / "grand-loc-qa-handout-2026-10-27" / "handout"
OUT = ROOT / "out" / "sim"
SECTIONS = ["DOCS", "DONATE", "TUNING", "FUEL", "ALABUGA_SHCOOL", "BONES", "JUNGLE_TREASURES", "BANK", "FAMILY"]

# Errors we plant ourselves: key -> (new kk value, acceptable issue types)
MT = "machine_translation_suspect"
INJECT = {
    "family_members": ("ЖАНҰЯ ҚҰРАМЫ", {"glossary_violation", "inconsistent_term"}),
    "bankDepositToFraction": ("Топ балансына толтыру", {"glossary_violation", "inconsistent_term"}),
    "tune_install_nitro_error": ("Сізде берілген нитро деңгейі орнатылмаған.", {MT}),
    "dd_date": ("20.02.2024 15:00 дейін", {MT}),
    "dd_result_info": ("Көрсетіңіз байланыс мәліметтерін сыйлықты алу үшін", {MT}),
    "tune_detail_roof": ("КАПОТ", {MT, "inconsistent_term"}),
    "tune_detail_hood": ("ШАТЫР", {MT, "inconsistent_term"}),
    "hydraulics": ("сұйық жетек", {"inconsistent_term", MT, "glossary_violation"}),
    "dd_start": ("Қабылдау қатысуды", {MT}),
}

# Organic strings that look wrong to us (not ground truth: the answer key is with the organizers).
SUSPECT = {
    "marriage": "Брак → «законопослушность» (скопировано из lawAbiding)",
    "balance": "Баланс → «наличные деньги»",
    "favorite": "Избранные → «доступные действия»",
    "rm_title_10": "Имущество → «настройка избранного» (скопировано из editFav)",
    "alabugaSchoolAllDay": "круглосуточно → «с 10 до 22»",
    "choose": "Выбрать → «выберите область»",
    "fuel_current": "В баке → «текущий уровень»",
    "chaseYou": "ЗА ВАМИ → «позади вас»",
    "jungleTreasuresChose": "выбрать → «выберите область»",
    "bones_select": "ВЫБРАТЬ → «выберите область»",
    "jungleTreasuresCancel": "отмена → «НАЧАТЬ»",
    "bones_dealer": "КРУПЬЕ → «крупная вещь»",
    "tune_detail_body_kits": "ОБВЕСЫ → «обзор»",
    "tune_detail_back_alignment": "РАЗВАЛ ЗАДНИХ → «задний свет»",
    "donate_vehicles_test_drive": "тест-драйв → «автопокупка»",
    "donate_plates_title": "БЛАТНЫЕ НОМЕРА → «бесплатные номера»",
    "diagnosis": "Диагноз → «тема»",
    "donate_case_opening": "Кейс → «іс» (юридическое дело), калька",
    "tune_tuning": "ТЮНИНГ → «ТЮНГ», опечатка",
    "family_upgrade_info": "Прокачка → «отправка»",
    "alabugaSchoolOpennedDesc": "три недели → «месяц»",
    "tuning_balance": "у вас на счету → «у вас есть счёт»",
    "tune_buy_headlights_color": "Цвет фар → «цвет светофора»",
}


def load_all():
    ru, kk, uz, en = (arb.load(H / f"intl_{l}.arb") for l in ("ru", "kk", "uz", "en"))
    for k, (v, _) in INJECT.items():
        assert k in kk.strings, k
        kk.strings[k] = v
    return ru, kk, uz, en


def sample_keys(ru):
    return [k for k in ru.strings if ru.section_of(k) in SECTIONS]


def prepare(cycle: str, version: str, with_rules: bool, glossary: str | None, size: int):
    ru, kk, uz, en = load_all()
    keys = sample_keys(ru)
    ctx = rules.Context(ru, kk, profiles.KK, {"uz": uz, "en": en})
    known = rules.run(ctx) if with_rules else None
    rows = list(csv.DictReader(open(glossary, encoding="utf-8"))) if glossary else []
    gtext = "\n".join(f"- {r['ru']} → {r['kk']}" + (f" (нельзя: {r['forbidden_kk']})" if r.get('forbidden_kk') else "")
                      + (f"; {r['comment']}" if r.get('comment') else "") for r in rows)
    hints = hintmod.run(ctx, rows) if with_rules else None
    be = judge.FileBackend(OUT / cycle, model="anthropic/claude-sonnet-5.5 (simulated)")
    batches = judge.make_batches(ru, keys, size=size)
    for b in batches:
        be.write_request(b.id, judge.build_messages(b, version, ru, kk, {"en": en, "uz": uz}, gtext, known, hints))
    meta = {"version": version, "with_rules": with_rules, "glossary": glossary, "size": size,
            "batches": [{"id": b.id, "section": b.section, "keys": b.keys} for b in batches]}
    (OUT / cycle / "meta.json").write_text(json.dumps(meta, ensure_ascii=False, indent=1), encoding="utf-8")
    chars = sum(len(p.read_text(encoding="utf-8")) for p in (OUT / cycle / "requests").glob("*.json"))
    print(f"{cycle}: {len(batches)} batches, {len(keys)} strings, ~{chars // 3} prompt tokens")


def evaluate(cycle: str):
    ru, kk, uz, en = load_all()
    meta = json.loads((OUT / cycle / "meta.json").read_text(encoding="utf-8"))
    be = judge.FileBackend(OUT / cycle)
    rule_f = rules.run(rules.Context(ru, kk, profiles.KK, {"uz": uz, "en": en}))
    sample = set(sample_keys(ru))
    rule_f = [f for f in rule_f if f.key in sample]
    rule_keys = {f.key: f.issue_type for f in rule_f}
    findings, reports, missing = [], [], []
    for b in meta["batches"]:
        batch = judge.Batch(b["id"], b["section"], b["keys"])
        text = be.read_response(b["id"])
        if text is None:
            missing.append(b["id"])
            continue
        fs, rep = judge.parse_answer(text, batch, ru, kk)
        findings += fs
        reports.append(rep)
    by_key: dict[str, list] = {}
    for f in findings:
        by_key.setdefault(f.key, []).append(f)

    def hit(key, types=None):
        return any(types is None or f.issue_type in types for f in by_key.get(key, []))

    # whole pipeline = rules + LLM
    combined = findings + rule_f
    by_key_all: dict[str, list] = {}
    for f in combined:
        by_key_all.setdefault(f.key, []).append(f)
    pipe_inj = sum(any(f.issue_type in t for f in by_key_all.get(k, [])) for k, (_, t) in INJECT.items())
    pipe_sus = sum(k in by_key_all for k in SUSPECT if k in sample)
    import re as _re
    noise = [f for f in findings if (f.suggestion and f.suggestion.lower() == f.target.lower())
             or _re.search(r"регистр|заглавн|строчн|пробел|многоточ|перенос", f.evidence, _re.I)]
    notes = [n for r in reports for n in r.source_notes]
    inj_hit = {k: hit(k, t) for k, (_, t) in INJECT.items()}
    inj_any = {k: hit(k) for k in INJECT}
    sus_hit = {k: hit(k) for k in SUSPECT if k in sample}
    rule_hit = {k: hit(k, {t}) for k, t in rule_keys.items()}
    extra = [f for f in findings if f.key not in INJECT and f.key not in SUSPECT and f.key not in rule_keys]
    dup_rules = [f for f in findings if f.key in rule_keys and f.issue_type == rule_keys[f.key]]
    dropped = [d for r in reports for d in r.dropped]
    res = {
        "cycle": cycle, "prompt": meta["version"], "with_rules": meta["with_rules"],
        "batches": len(meta["batches"]), "answered": len(reports), "missing": missing,
        "parse_failures": [(r.batch_id, r.error) for r in reports if not r.ok],
        "findings": len(findings), "by_type": Counter(f.issue_type for f in findings),
        "by_severity": Counter(f.severity for f in findings),
        "dropped_by_validation": Counter(d["reason"].split(" ")[0] + " " + d["reason"].split(" ")[1] for d in dropped),
        "injected_recall_typed": f"{sum(inj_hit.values())}/{len(INJECT)}",
        "injected_recall_any_type": f"{sum(inj_any.values())}/{len(INJECT)}",
        "injected_missed": [k for k, v in inj_hit.items() if not v],
        "injected_wrong_type": {k: [f.issue_type for f in by_key.get(k, [])] for k in INJECT if inj_any[k] and not inj_hit[k]},
        "suspect_recall": f"{sum(sus_hit.values())}/{len(sus_hit)}",
        "suspect_missed": [k for k, v in sus_hit.items() if not v],
        "rule_overlap": f"{sum(rule_hit.values())}/{len(rule_hit)} rule findings re-found by LLM",
        "rule_missed_by_llm": [k for k, v in rule_hit.items() if not v],
        "duplicates_of_rules": len(dup_rules),
        "extra_findings": len(extra),
        "low_conf_share": round(sum(f.confidence < 0.6 for f in findings) / max(1, len(findings)), 2),
        "noise_case_punct": len(noise),
        "with_suggestion": sum(bool(f.suggestion) for f in findings),
        "source_notes": len(notes),
        "PIPELINE_injected_typed": f"{pipe_inj}/{len(INJECT)}",
        "PIPELINE_suspect_any": f"{pipe_sus}/{sum(k in sample for k in SUSPECT)}",
        "PIPELINE_total_findings": len(combined),
    }
    with open(OUT / cycle / "source_notes.md", "w", encoding="utf-8") as fh:
        for n in notes:
            fh.write(f"- `{n.get('key')}`: {n.get('note')}\n")
    (OUT / cycle / "eval.json").write_text(json.dumps(res, ensure_ascii=False, indent=1, default=dict), encoding="utf-8")
    with open(OUT / cycle / "extra.md", "w", encoding="utf-8") as fh:
        for f in sorted(extra, key=lambda f: (f.issue_type, f.key)):
            fh.write(f"- [{f.issue_type}/{f.severity}/{f.confidence}] `{f.key}` ru=«{f.source}» kk=«{f.target}» "
                     f"→ «{f.suggestion}» :: {f.evidence}\n")
    with open(OUT / cycle / "dropped.md", "w", encoding="utf-8") as fh:
        for d in dropped:
            fh.write(f"- {d['reason']} :: {json.dumps(d['finding'], ensure_ascii=False)[:300]}\n")
    print(json.dumps(res, ensure_ascii=False, indent=1, default=dict))


if __name__ == "__main__":
    a = sys.argv[1:]
    if a[0] == "prepare":
        opt = lambda n, d=None: a[a.index(n) + 1] if n in a else d
        prepare(a[1], a[2], "--with-rules" in a, opt("--glossary"), int(opt("--size", 40)))
    else:
        evaluate(a[1])
