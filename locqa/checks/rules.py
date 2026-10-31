"""Deterministic checks. They are cheap and precise, so they run first and the LLM only sees what they cannot judge."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .. import icu
from ..arb import ArbFile
from ..model import Finding
from ..profiles import Profile

WORD = re.compile(r"[^\W\d_]+", re.U)
CYR = re.compile(r"[А-Яа-яЁёӘәҒғҚқҢңӨөҰұҮүҺһІі]")
LAT = re.compile(r"[A-Za-z]")
UI_KEY = re.compile(r"(button|btn|tab|title|header|label|caption|menu|action|confirm|ok|cancel|buy|close)", re.I)


@dataclass
class Context:
    source: ArbFile
    target: ArbFile
    profile: Profile
    refs: dict[str, ArbFile]  # other translations (en, uz, ...) used as extra evidence


def _strip_markup(s: str) -> str:
    return icu.TAG.sub(" ", icu.PRINTF.sub(" ", re.sub(r"\{[^{}]*\}", " ", s)))


def check_untranslated(ctx: Context):
    for k, src in ctx.source.strings.items():
        tgt = ctx.target.strings.get(k)
        if tgt is None:
            yield Finding(k, "untranslated", "high", src, "", "Ключ есть в исходнике, но отсутствует в переводе",
                          None, 0.99, detector="rules.untranslated")
        elif not tgt.strip() and src.strip():
            yield Finding(k, "untranslated", "high", src, tgt, "Значение пустое, в исходнике есть текст",
                          None, 0.97, detector="rules.untranslated")


def check_placeholders(ctx: Context):
    for k, src in ctx.source.strings.items():
        tgt = ctx.target.strings.get(k)
        if not tgt:
            continue
        a, b = icu.tokens(src), icu.tokens(tgt)
        if a == b:
            continue
        missing, extra = a - b, b - a
        parts = []
        if missing:
            parts.append("пропали: " + ", ".join(sorted(missing.elements())))
        if extra:
            parts.append("лишние: " + ", ".join(sorted(extra.elements())))
        suggestion = tgt
        for tok in missing:  # `{time}` lost its braces and became `time`
            bare = tok.strip("{}")
            if tok.startswith("{") and re.search(rf"(?<![{{\w]){re.escape(bare)}(?![\w}}])", suggestion):
                suggestion = re.sub(rf"(?<![{{\w]){re.escape(bare)}(?![\w}}])", tok, suggestion, count=1)
        for tok in extra:  # typo inside braces: `{platformColo}` vs `{platformColor}`
            near = [m for m in missing if m.startswith("{") and tok.startswith("{") and
                    (m[1:-1].startswith(tok[1:-1]) or tok[1:-1].startswith(m[1:-1]))]
            if near:
                suggestion = suggestion.replace(tok, near[0], 1)
        fixed = icu.tokens(suggestion) == a
        yield Finding(k, "placeholder_mismatch", "high", src, tgt, "Плейсхолдеры не совпадают с исходником; " + "; ".join(parts),
                      suggestion if fixed else None, 0.98, detector="rules.placeholders")


def check_plurals(ctx: Context):
    for k, tgt in ctx.target.strings.items():
        if "{" not in tgt:
            continue
        try:
            miss = icu.missing_plural_forms(tgt, ctx.target.locale)
        except icu.IcuError as e:
            yield Finding(k, "plural_mismatch", "high", ctx.source.strings.get(k, ""), tgt, f"Невалидный ICU: {e}",
                          None, 0.95, detector="rules.plurals")
            continue
        for arg, forms in miss.items():
            yield Finding(k, "plural_mismatch", "medium", ctx.source.strings.get(k, ""), tgt,
                          f"В plural для {{{arg}}} нет форм: {', '.join(sorted(forms))}", None, 0.9, detector="rules.plurals")


def check_same_as_source(ctx: Context):
    for k, src in ctx.source.strings.items():
        tgt = ctx.target.strings.get(k)
        if not tgt or tgt != src:
            continue
        words = WORD.findall(_strip_markup(src))
        cyr_words = [w for w in words if CYR.search(w)]
        if ctx.profile.script == "cyrl" and not cyr_words:
            continue  # brands, Latin names, numbers
        if ctx.profile.script == "latn" and not cyr_words:
            continue
        n = len(cyr_words)
        if n >= 3:
            yield Finding(k, "same_as_source", "high", src, tgt,
                          f"Строка из {n} слов полностью совпадает с исходником: похоже, перевод не сделан", None, 0.9,
                          detector="rules.same_as_source")
        elif n == 2 and ctx.profile.script == "cyrl":
            yield Finding(k, "same_as_source", "low", src, tgt,
                          "Короткая фраза совпадает с исходником; возможно, заимствование, нужна проверка носителем",
                          None, 0.4, detector="rules.same_as_source")
        elif ctx.profile.script == "latn":
            yield Finding(k, "same_as_source", "high", src, tgt, "Кириллический текст исходника в латинском переводе",
                          None, 0.9, detector="rules.same_as_source")


def check_wrong_language(ctx: Context):
    for k, tgt in ctx.target.strings.items():
        src = ctx.source.strings.get(k)
        if not tgt or src is None or tgt == src:
            continue
        text = _strip_markup(tgt)
        if re.match(r"^\s*https?://", text):
            continue
        if icu.tokens(src) != icu.tokens(tgt):
            continue  # `{name}` that lost its braces looks like Latin text; placeholder check owns this case
        full_match_ref = any(r.strings.get(k) == tgt for r in ctx.refs.values())
        if not full_match_ref:
            # Latin words that also appear in a reference translation are brand names (MIDAS CREW, HUD)
            ref_words = {w.lower() for r in ctx.refs.values() for w in WORD.findall(r.strings.get(k, ""))}
            text = " ".join(w for w in WORD.findall(text) if not (LAT.search(w) and w.lower() in ref_words))
        evidence = []
        if ctx.profile.script == "cyrl":
            lat, cyr = len(LAT.findall(text)), len(CYR.findall(text))
            src_lat = len(LAT.findall(_strip_markup(src)))
            if lat >= 6 and lat > cyr and lat > src_lat * 1.5:
                evidence.append(f"{lat} латинских букв против {cyr} кириллических, в исходнике латиницы {src_lat}")
        for pattern, lang in ctx.profile.foreign_markers.items():
            if re.search(pattern, text, re.I) and lang != ctx.profile.code:
                if ctx.profile.script == "cyrl" and not LAT.search(text) and lang != "uz":
                    continue
                evidence.append(f"маркер языка {lang}: «{re.search(pattern, text, re.I).group(0)}»")
                break
        for code, ref in ctx.refs.items():
            if ref.strings.get(k) == tgt and WORD.search(text) and len(text.strip()) > 3:
                evidence.append(f"дословно совпадает с файлом {code}")
        if ctx.profile.script == "cyrl" and len(evidence) < 2 and not (LAT.search(text) and len(LAT.findall(text)) > len(CYR.findall(text))):
            continue
        if not evidence:
            continue
        brand_like = (full_match_ref and len(WORD.findall(text)) <= 3 and not CYR.search(text)
                      and (text.isupper() or re.search(r"\d", tgt) is not None)
                      and not any(re.search(p, text, re.I) for p in ctx.profile.foreign_markers))
        if brand_like:
            yield Finding(k, "wrong_language", "low", src, tgt,
                          "Короткая латинская строка совпадает с en/uz: возможно, название бренда или ивента, "
                          "но в исходнике оно переведено — нужно решение носителя", None, 0.45,
                          detector="rules.wrong_language")
            continue
        yield Finding(k, "wrong_language", "high", src, tgt, "Текст на другом языке: " + "; ".join(evidence),
                      None, min(0.98, 0.7 + 0.12 * len(evidence)), detector="rules.wrong_language")


def check_orthography(ctx: Context):
    p = ctx.profile
    if not p.look_alikes:
        return
    fold = str.maketrans(p.look_alikes)
    freq: Counter = Counter()
    for v in ctx.target.strings.values():
        freq.update(w.lower() for w in WORD.findall(v))
    by_fold = defaultdict(list)
    for w, c in freq.items():
        by_fold[w.translate(fold)].append((w, c))
    src_vocab = {w.lower() for v in ctx.source.strings.values() for w in WORD.findall(v)}
    specific = set(p.letters)
    for k, tgt in ctx.target.strings.items():
        notes, suggestion = [], tgt
        for w in WORD.findall(tgt):
            if LAT.search(w) and CYR.search(w):
                lat = "".join(sorted(set(LAT.findall(w))))
                notes.append(f"«{w}»: латинские буквы «{lat}» внутри кириллического слова")
                suggestion = suggestion.replace(w, w.translate(str.maketrans("aceiopxyACEHIKMOPTXY", "асеіорхуАСЕНІКМОРТХУ")))
                continue
            lw = w.lower()
            if set(lw) & specific or freq[lw] > 2 or lw in src_vocab or len(lw) < 3:
                continue
            alts = [(a, c) for a, c in by_fold[lw] if a != lw and set(a) & specific]
            if alts:
                best, c = max(alts, key=lambda x: x[1])
                if c >= 3 * freq[lw] and c >= 3:
                    notes.append(f"«{w}» встречается {freq[lw]} раз, а «{best}» — {c} раз: казахская буква заменена русской")
                    suggestion = re.sub(rf"\b{re.escape(w)}\b", _match_case(w, best), suggestion)
        if notes:
            yield Finding(k, "orthography", "medium", ctx.source.strings.get(k, ""), tgt, "; ".join(notes),
                          suggestion if suggestion != tgt else None, 0.85, detector="rules.orthography")


def _match_case(orig: str, word: str) -> str:
    if orig.isupper():
        return word.upper()
    if orig[:1].isupper():
        return word[:1].upper() + word[1:]
    return word


def check_suffix_after_placeholder(ctx: Context):
    if not ctx.profile.case_suffixes:
        return
    suf = "|".join(sorted(map(re.escape, ctx.profile.case_suffixes), key=len, reverse=True))
    pat = re.compile(rf"(\{{\w+\}}|%(?:\d+\$)?s)(-?)({suf})(?![\w])", re.I)
    for k, tgt in ctx.target.strings.items():
        for m in pat.finditer(tgt):
            yield Finding(k, "suffix_harmony", "medium", ctx.source.strings.get(k, ""), tgt,
                          f"После плейсхолдера {m.group(1)} жёстко записан аффикс «{m.group(2)}{m.group(3)}»: "
                          f"форма зависит от подставленного значения (гармония гласных, последний звук)",
                          None, 0.9, detector="rules.suffix")
            break


def check_length(ctx: Context, max_src: int = 24):
    for k, src in ctx.source.strings.items():
        tgt = ctx.target.strings.get(k)
        if not tgt or len(src) > max_src or "\n" in src:
            continue
        ls, lt = len(src), max(len(x) for x in tgt.split("\n"))
        refs = [len(r.strings[k]) for r in ctx.refs.values() if r.strings.get(k)]
        ref_max = max(refs) if refs else ls
        ui_like = bool(UI_KEY.search(k)) or src.isupper() or len(WORD.findall(src)) <= 2
        if lt >= 2.2 * ls and lt - ls >= 10 and lt >= 1.8 * ref_max and ui_like:
            sev = "high" if lt >= 3 * ls else "medium"
            ev = f"{lt} символов против {ls} в исходнике"
            if refs:
                ev += f" (в других языках не больше {ref_max})"
            ev += "; короткий элемент интерфейса"
            yield Finding(k, "length_overflow", sev, src, tgt, ev, None, 0.8 if sev == "high" else 0.65,
                          detector="rules.length")


def term_groups(ctx: Context, max_words: int = 4) -> dict[str, dict[str, list[str]]]:
    """Short source strings used in several keys -> {normalized target variant: [keys]} (concordance)."""
    norm = lambda x: re.sub(r"[\W_]+", " ", x.lower()).strip()
    groups: dict[str, list[str]] = defaultdict(list)
    for k, v in ctx.source.strings.items():
        t = ctx.target.strings.get(k)
        if t and norm(v) and len(norm(v).split()) <= max_words:
            groups[norm(v)].append(k)
    out = {}
    for src, ks in groups.items():
        variants = defaultdict(list)
        for k in ks:
            variants[norm(ctx.target.strings[k])].append(k)
        if len(ks) >= 2 and len(variants) > 1:
            out[src] = dict(variants)
    return out


def check_consistency(ctx: Context):
    """Same source string, lexically unrelated target in one key while ≥3 keys agree (ПОДВЕСКА: аспа ×3, ІЛГІШ ×1).
    Groups where the majority itself may be wrong are left to the LLM / decision model."""
    for src, variants in term_groups(ctx).items():
        ranked = sorted(variants.items(), key=lambda x: -len(x[1]))
        top, top_keys = ranked[0]
        if len(top_keys) < 3 or len(top_keys) <= sum(len(v) for _, v in ranked[1:]):
            continue
        for variant, keys in ranked[1:]:
            if set(variant.split()) & set(top.split()):
                continue
            for k in keys:
                yield Finding(k, "inconsistent_term", "medium", ctx.source.strings[k], ctx.target.strings[k],
                              f"Одна и та же строка «{ctx.source.strings[k]}» в {len(top_keys)} ключах переведена как "
                              f"«{ctx.target.strings[top_keys[0]]}» ({', '.join(top_keys[:3])}), а здесь иначе",
                              ctx.target.strings[top_keys[0]], 0.7, detector="rules.consistency")


ALL = [check_untranslated, check_placeholders, check_plurals, check_same_as_source, check_wrong_language,
       check_orthography, check_suffix_after_placeholder, check_length]
# check_consistency is NOT in ALL: in this data the majority variant is itself wrong in several groups
# («Отмена» → «БАСТАУ» ×5 vs the correct «Болдырмау» ×2), so groups go to the vote (decide.vote_questions) instead.


def run(ctx: Context) -> list[Finding]:
    out = []
    for check in ALL:
        out.extend(check(ctx))
    return out
