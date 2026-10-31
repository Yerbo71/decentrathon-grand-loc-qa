"""Tests on synthetic ARB files only: the organizers' data must not be committed."""
import json

import pytest

from locqa import arb, fix, icu, profiles
from locqa.checks import hints, rules
from locqa.judge import Batch, parse_answer
from locqa.model import Finding

RU = """{
  "@@locale": "ru",
  "@@comment": "SHOP",
  "buyButton": "Купить",
  "buyFor": "Купить за {price}",
  "confirm": "Вы действительно хотите купить этот предмет?",
  "points": "{points} ОЧКОВ",
  "family": "Баланс семьи",
  "@family": {"description": "family balance"},
  "@@comment": "OTHER",
  "profile": "Профиль",
  "time": "Время: %s",
  "rare": "Редкий",
  "auth": "Ошибка авторизации",
  "missing": "Нет в переводе",
  "giftTo": "Подарок для {name}",
  "cancel": "Отмена",
  "cancel2": "Отмена"
}"""

KK = """{
  "@@locale": "kk",
  "@@comment": "SHOP",
  "buyButton": "Сатып алу",
  "buyFor": "{price}-ге сатып алу",
  "confirm": "Вы действительно хотите купить этот предмет?",
  "points": "ҰПАЙ",
  "family": "Отбасы балансы",
  "@@comment": "OTHER",
  "profile": "Профиль",
  "time": "Уақыт: %s",
  "rare": "Сирек режимін қосу және жалғастыру",
  "auth": "Avtorizatsiya xatosi",
  "giftTo": "{name} үшін сыйлык",
  "cancel": "Болдырмау",
  "cancel2": "Бастау",
  "cancel": "Болдырмау",
  "extra": "Артық"
}"""

UZ = """{"@@locale": "uz", "auth": "Avtorizatsiya xatosi", "rare": "Noyob", "buyButton": "Sotib olish"}"""
EN = """{"@@locale": "en", "auth": "Authorization error", "rare": "Rare", "buyButton": "Buy"}"""


@pytest.fixture
def ctx():
    ru, kk = arb.parse(RU, "ru.arb"), arb.parse(KK, "kk.arb")
    refs = {"uz": arb.parse(UZ), "en": arb.parse(EN)}
    return rules.Context(ru, kk, profiles.KK, refs)


def by_type(findings):
    out = {}
    for f in findings:
        out.setdefault(f.issue_type, set()).add(f.key)
    return out


# --- ARB parsing ---------------------------------------------------------------------------------------------

def test_arb_keeps_sections_duplicates_and_meta():
    ru, kk = arb.parse(RU), arb.parse(KK)
    assert ru.section_of("buyFor") == "SHOP" and ru.section_of("profile") == "OTHER"
    assert "family" in ru.meta and ru.meta["family"]["description"] == "family balance"
    assert set(kk.duplicates) == {"cancel"}
    assert kk.entry("rare").line > kk.entry("buyButton").line > 0


def test_arb_dumps_roundtrip_and_escapes_private_use():
    text = arb.dumps([("@@locale", "kk"), ("@@comment", "A"), ("sym", "\uea34"), ("@@comment", "B"), ("x", "a\nb")])
    assert "\\uea34" in text and text.count('"@@comment"') == 2
    assert arb.parse(text).strings["sym"] == "\uea34"


# --- ICU -----------------------------------------------------------------------------------------------------

def test_icu_tokens_cover_braces_printf_and_tags():
    assert icu.tokens("Купить <g>{x}</g> за %s") == icu.tokens("%s <g>{x}</g> сатып алу")
    assert icu.tokens("{a}") != icu.tokens("a")


def test_icu_plural_parsing_and_required_forms():
    msg = "{n, plural, one{# день} few{# дня} many{# дней} other{# дня}}"
    assert icu.parse(msg).plurals == {"n": {"one", "few", "many", "other"}}
    assert icu.missing_plural_forms("{n, plural, one{# день} other{# дней}}", "ru") == {"n": {"few", "many"}}
    with pytest.raises(icu.IcuError):
        icu.parse("{n, plural, one{x}}")
    with pytest.raises(icu.IcuError):
        icu.parse("{name")


# --- rules ---------------------------------------------------------------------------------------------------

def test_rules_find_each_seeded_type(ctx):
    t = by_type(rules.run(ctx))
    assert "missing" in t["untranslated"]
    assert "points" in t["placeholder_mismatch"]
    assert "confirm" in t["same_as_source"]
    assert "auth" in t["wrong_language"]
    assert "rare" in t["length_overflow"]
    assert "buyFor" in t["suffix_harmony"]


def test_rules_do_not_flag_loanwords_or_good_strings(ctx):
    flagged = {f.key for f in rules.run(ctx)}
    assert "profile" not in flagged       # loanword identical to ru
    assert "time" not in flagged          # %s preserved
    assert "family" not in flagged


def test_placeholder_suggestion_restores_braces():
    ru = arb.parse('{"@@locale":"ru","a":"Обновится через: {time}"}')
    kk = arb.parse('{"@@locale":"kk","a":"Жаңарады: time"}')
    (f,) = rules.check_placeholders(rules.Context(ru, kk, profiles.KK, {}))
    assert f.suggestion == "Жаңарады: {time}"


def test_orthography_uses_corpus_frequency():
    words = ", ".join(f'"k{i}": "сыйлық алу"' for i in range(5))
    ru = arb.parse('{"@@locale":"ru","x":"Супер приз",' + ", ".join(f'"k{i}": "приз"' for i in range(5)) + "}")
    kk = arb.parse('{"@@locale":"kk","x":"супер сыйлык",' + words + "}")
    (f,) = rules.check_orthography(rules.Context(ru, kk, profiles.KK, {}))
    assert f.key == "x" and f.suggestion == "супер сыйлық"


def test_mixed_script_word_is_orthography():
    ru = arb.parse('{"@@locale":"ru","x":"Перейди в раздел"}')
    kk = arb.parse('{"@@locale":"kk","x":"Бөлімiне өт"}')  # Latin i
    (f,) = rules.check_orthography(rules.Context(ru, kk, profiles.KK, {}))
    assert "Бөліміне" in f.suggestion


# --- hints ---------------------------------------------------------------------------------------------------

def test_hints_report_concordance_and_numbers(ctx):
    kinds = {(h.key, h.kind) for h in hints.run(ctx)}
    assert ("cancel2", "term_variants") in kinds
    ru = arb.parse('{"@@locale":"ru","a":"от 1 до 70 символов"}')
    kk = arb.parse('{"@@locale":"kk","a":"3-тен 90 таңбаға дейін"}')
    assert hints.number_mismatch(rules.Context(ru, kk, profiles.KK, {}))[0].key == "a"


def test_majority_rule_is_not_an_automatic_finding(ctx):
    # the majority can be wrong («Отмена» → «БАСТАУ»), so concordance never produces findings by itself
    assert all(f.detector != "rules.consistency" for f in rules.run(ctx))


# --- LLM answer parsing --------------------------------------------------------------------------------------

def test_parse_answer_validates_schema(ctx):
    b = Batch("b1", "SHOP", ["buyButton", "confirm"])
    answer = json.dumps({"findings": [
        {"key": "buyButton", "issue_type": "machine_translation_suspect", "severity": "high",
         "back_translation": "купить", "evidence": "проверочное обоснование", "suggestion": "Сатып алу", "confidence": 0.9},
        {"key": "notInBatch", "issue_type": "orthography", "severity": "high", "evidence": "x" * 20, "confidence": 0.9},
        {"key": "confirm", "issue_type": "made_up_type", "severity": "high", "evidence": "x" * 20, "confidence": 0.9},
    ], "source_notes": [{"key": "confirm", "note": "опечатка"}]})
    fs, rep = parse_answer("```json\n" + answer + "\n```", b, ctx.source, ctx.target)
    assert [f.key for f in fs] == ["buyButton"]
    assert "Сейчас по-казахски написано: «купить»" in fs[0].evidence
    assert len(rep.dropped) == 2 and rep.source_notes[0]["key"] == "confirm"


def test_parse_answer_rejects_broken_json(ctx):
    fs, rep = parse_answer("not json at all", Batch("b", None, ["a"]), ctx.source, ctx.target)
    assert fs == [] and not rep.ok


# --- fix validator -------------------------------------------------------------------------------------------

def test_fix_validator():
    f = Finding("buyFor", "suffix_harmony", "medium", "Купить за {price}", "{price}-ге сатып алу", "e")
    assert fix.validate(f, "Сатып алу: {price}", profiles.KK) == []
    assert any("аффикс" in p for p in fix.validate(f, "{price}-ге алу", profiles.KK))
    assert any("плейсхолдер" in p for p in fix.validate(f, "Сатып алу", profiles.KK))
    g = Finding("c", "wrong_language", "high", "Кейс открывается", "Іс ашылуда", "e")
    assert fix.validate(g, "Іс ашылуда.", profiles.KK, forbidden=[("кейс", "іс")])
    # a forbidden word only counts where the source uses the term, and never as a prefix of another stem
    h = Finding("d", "wrong_language", "high", "Запустите снова", "x", "e")
    assert fix.validate(h, "Қайта іске қосыңыз", profiles.KK, forbidden=[("кейс", "іс")]) == []


def test_cli_check_writes_report(tmp_path):
    from typer.testing import CliRunner
    from locqa.cli import app
    (tmp_path / "ru.arb").write_text(RU, encoding="utf-8")
    (tmp_path / "kk.arb").write_text(KK, encoding="utf-8")
    out = tmp_path / "out" / "report.json"
    res = CliRunner().invoke(app, ["check", "--source", str(tmp_path / "ru.arb"), "--target", str(tmp_path / "kk.arb"),
                                   "--out", str(out), "--fail-on", "high"])
    assert res.exit_code == 1, res.output  # high findings present -> CI fails
    report = json.loads(out.read_text(encoding="utf-8"))
    assert report and set(report[0]) == {"key", "issue_type", "severity", "source", "target", "evidence",
                                         "suggestion", "confidence", "reviewed_by_human"}
    assert (out.parent / "report.html").exists() and (out.parent / "qa_queue.json").exists()


def test_suggest_review_apply_roundtrip(tmp_path):
    from typer.testing import CliRunner
    from locqa.cli import app
    run = CliRunner().invoke
    (tmp_path / "ru.arb").write_text(RU, encoding="utf-8")
    (tmp_path / "kk.arb").write_text(KK, encoding="utf-8")
    src, tgt = str(tmp_path / "ru.arb"), str(tmp_path / "kk.arb")
    rep, q = tmp_path / "report.json", tmp_path / "queue.json"
    run(app, ["check", "--source", src, "--target", tgt, "--out", str(rep)])
    r = run(app, ["suggest", "--report", str(rep), "--source", src, "--target", tgt, "--out", str(q), "--no-llm"])
    assert r.exit_code == 0, r.output
    queue = json.loads(q.read_text(encoding="utf-8"))
    card = next(c for c in queue if c["key"] == "points")
    assert card["suggestion"] is None  # «ҰПАЙ» lost {points}: no mechanical fix, the reviewer writes one
    assert run(app, ["review", "--queue", str(q), "--out", str(tmp_path / "review.html")]).exit_code == 0
    assert "QUEUE = [" in (tmp_path / "review.html").read_text(encoding="utf-8")
    decisions = [
        {"key": "points", "decision": "edit", "final": "{points} ҰПАЙ", "issues": ["placeholder_mismatch"], "reviewer": "t"},
        {"key": "auth", "decision": "edit", "final": "Авторизация қатесі", "issues": ["wrong_language"], "reviewer": "t"},
        {"key": "missing", "decision": "edit", "final": "Аудармада жоқ", "issues": ["untranslated"], "reviewer": "t"},
        {"key": "buyFor", "decision": "edit", "final": "Сатып алу", "issues": ["suffix_harmony"], "reviewer": "t"},  # drops {price}
        {"key": "rare", "decision": "reject", "final": None, "issues": ["length_overflow"], "reviewer": "t"},
    ]
    (tmp_path / "d.jsonl").write_text("\n".join(json.dumps(d, ensure_ascii=False) for d in decisions), encoding="utf-8")
    fixed = tmp_path / "fixed.arb"
    r = run(app, ["apply", "--source", src, "--target", tgt, "--decisions", str(tmp_path / "d.jsonl"), "--out", str(fixed)])
    assert r.exit_code == 0, r.output
    out = arb.parse(fixed.read_text(encoding="utf-8"))
    assert out.strings["points"] == "{points} ҰПАЙ" and out.meta["points"]["x-locqa"]["reviewed_by_human"] is True
    assert out.meta["points"]["x-locqa"]["original"] == "ҰПАЙ"
    assert out.strings["missing"] == "Аудармада жоқ"           # key absent in target got added
    assert out.strings["buyFor"] == "{price}-ге сатып алу"     # placeholder-breaking edit refused
    assert out.strings["rare"].startswith("Сирек режимін")     # rejected -> unchanged
    assert not out.duplicates                                  # duplicate «cancel» collapsed
    assert "x-locqa" not in out.meta.get("profile", {})        # untouched strings carry no flag
