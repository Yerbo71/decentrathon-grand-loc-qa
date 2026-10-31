"""Automatic gate for proposed fixes: a suggestion that fails here never reaches the human reviewer."""
from __future__ import annotations

import re

from . import icu
from .checks.rules import CYR, LAT, WORD
from .model import Finding
from .profiles import Profile


def validate(f: Finding, suggestion: str, profile: Profile, refs: dict[str, str] | None = None,
             forbidden: list[tuple[str, str]] | None = None) -> list[str]:
    """Return a list of problems with `suggestion` (empty list = passes)."""
    problems = []
    refs = refs or {}
    if not suggestion or not suggestion.strip():
        return ["пустое предложение"]
    if icu.tokens(suggestion) != icu.tokens(f.source):
        problems.append(f"плейсхолдеры/теги не совпадают с исходником: {sorted(icu.tokens(f.source).elements())} "
                        f"против {sorted(icu.tokens(suggestion).elements())}")
    try:
        icu.parse(suggestion)
    except icu.IcuError as e:
        problems.append(f"невалидный ICU: {e}")
    if suggestion.count("\n") != f.source.count("\n") and f.issue_type != "length_overflow":
        problems.append(f"число переносов \\n {suggestion.count(chr(10))}, в исходнике {f.source.count(chr(10))}")
    plain = re.sub(r"\{[^}]*\}|%s", " ", f.source)
    if suggestion.strip() == f.source.strip() and CYR.search(plain) and len(WORD.findall(plain)) >= 2:
        problems.append("предложение совпадает с русским исходником")
    if suggestion == f.target:
        problems.append("предложение не отличается от текущего перевода")
    if profile.script == "cyrl":
        allowed_latin = {w.lower() for s in [f.source, *refs.values()] for w in WORD.findall(s) if LAT.search(w)}
        stray = [w for w in WORD.findall(icu.TAG.sub(" ", re.sub(r"\{[^}]*\}", " ", suggestion)))
                 if LAT.search(w) and w.lower() not in allowed_latin]
        if stray:
            problems.append(f"латиница, которой нет в исходнике: {stray}")
    if f.issue_type == "length_overflow":
        limit = max(len(f.source), *(len(v) for v in refs.values() if v)) if refs else len(f.source)
        limit = int(limit * 1.3) + 2
        longest = max(len(x) for x in suggestion.split("\n"))
        if longest > limit:
            problems.append(f"всё ещё длинно: {longest} символов при лимите {limit}")
    if f.issue_type == "suffix_harmony":
        if re.search(r"(\{\w+\}|%s)-?[а-яәғқңөұүһі]", suggestion, re.I):
            problems.append("после плейсхолдера всё ещё стоит аффикс")
    words = [w.lower() for w in WORD.findall(suggestion)]
    for ru_term, bad in forbidden or []:
        b = bad.lower().strip()
        if not b or ru_term.lower()[: max(4, len(ru_term) - 2)] not in f.source.lower():
            continue  # a forbidden rendering only matters where the source actually uses the glossary term
        if " " in b:
            hit = b in suggestion.lower()
        else:  # whole word or word + case suffix (іс, істі, іске), never a different longer stem (істеу)
            hit = any(w == b or (w.startswith(b) and w[len(b):] in profile.case_suffixes) for w in words)
        if hit:
            problems.append(f"запрещённый глоссарием вариант «{bad}»")
    return problems
