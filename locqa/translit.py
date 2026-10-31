"""Kazakh Cyrillic → official Latin alphabet (version of April 2021), with UI length and font checks.

Native Kazakh letters follow the official table. Letters used only in Russian loanwords (Ц Ч Щ Ё Ю Я Э Ъ Ь)
are not in that table; we use the common conventions below and say so in the report.
Placeholders {x}, %s and tags <g> are never transliterated.
"""
from __future__ import annotations

import re

from . import icu

OFFICIAL = {
    "А": "A", "а": "a", "Ә": "Ä", "ә": "ä", "Б": "B", "б": "b", "В": "V", "в": "v", "Г": "G", "г": "g",
    "Ғ": "Ğ", "ғ": "ğ", "Д": "D", "д": "d", "Е": "E", "е": "e", "Ж": "J", "ж": "j", "З": "Z", "з": "z",
    "И": "İ", "и": "i", "Й": "İ", "й": "i", "І": "I", "і": "ı", "К": "K", "к": "k", "Қ": "Q", "қ": "q",
    "Л": "L", "л": "l", "М": "M", "м": "m", "Н": "N", "н": "n", "Ң": "Ñ", "ң": "ñ", "О": "O", "о": "o",
    "Ө": "Ö", "ө": "ö", "П": "P", "п": "p", "Р": "R", "р": "r", "С": "S", "с": "s", "Т": "T", "т": "t",
    "У": "U", "у": "u", "Ұ": "Ū", "ұ": "ū", "Ү": "Ü", "ү": "ü", "Ф": "F", "ф": "f", "Х": "H", "х": "h",
    "Һ": "H", "һ": "h", "Ш": "Ş", "ш": "ş", "Ы": "Y", "ы": "y",
}
LOANWORD = {  # not in the official table: conventional renderings
    "Ц": "Ts", "ц": "ts", "Ч": "Ch", "ч": "ch", "Щ": "Şş", "щ": "şş", "Ё": "İo", "ё": "io",
    "Ю": "İu", "ю": "iu", "Я": "İa", "я": "ia", "Э": "E", "э": "e", "Ъ": "", "ъ": "", "Ь": "", "ь": "",
}
TABLE = {**OFFICIAL, **LOANWORD}
_PROTECTED = re.compile(r"\{[^{}]*\}|%(?:\d+\$)?[sdif@]|</?[a-zA-Z][\w-]*>")


def to_latin(text: str) -> str:
    out, pos = [], 0
    for m in _PROTECTED.finditer(text):
        out.append(_convert(text[pos:m.start()]))
        out.append(m.group(0))
        pos = m.end()
    out.append(_convert(text[pos:]))
    return "".join(out)


def _convert(chunk: str) -> str:
    res = []
    for i, ch in enumerate(chunk):
        lat = TABLE.get(ch, ch)
        # an uppercase digraph inside an ALL-CAPS word stays all caps: ЦЕХ → TSEH, not TsEH
        if len(lat) > 1 and ch.isupper():
            nxt = chunk[i + 1:i + 2]
            prv = chunk[i - 1:i] if i else ""
            if (nxt and nxt.isupper()) or (prv and prv.isupper() and not nxt.isalpha()):
                lat = lat.upper()
        res.append(lat)
    return "".join(res)


def loanword_letters(text: str) -> set[str]:
    return {c for c in text if c in LOANWORD}


def non_ascii_glyphs(text: str) -> set[str]:
    return {c for c in text if ord(c) > 127 and c.isalpha()}


def check(source, target, refs: dict, ui_ratio: float = 1.3) -> tuple[dict[str, str], list[dict], dict]:
    """Transliterate every target string; flag UI strings that grow past the space their source/references use."""
    latin, issues, glyphs = {}, [], set()
    for k, v in target.strings.items():
        lv = to_latin(v)
        latin[k] = lv
        glyphs |= non_ascii_glyphs(lv)
        if icu.tokens(lv) != icu.tokens(v):  # must never happen: placeholders are protected
            issues.append({"key": k, "problem": "placeholders changed", "cyrillic": v, "latin": lv})
            continue
        src = source.strings.get(k)
        if not src or "\n" in src or len(src) > 24:
            continue
        widest = max([len(src)] + [len(r.strings.get(k) or "") for r in refs.values()])
        longest = max(len(x) for x in lv.split("\n"))
        if longest > widest * ui_ratio + 2 and longest > max(len(x) for x in v.split("\n")):
            issues.append({"key": k, "problem": "length_overflow", "cyrillic": v, "latin": lv,
                           "evidence": f"латиница {longest} символов, кириллица {len(v)}, самый длинный из ru/en/uz {widest}"})
    summary = {
        "strings": len(latin),
        "with_loanword_letters": sum(1 for v in target.strings.values() if loanword_letters(v)),
        "length_issues": sum(1 for i in issues if i["problem"] == "length_overflow"),
        "font_glyphs_needed": "".join(sorted(glyphs)),
        "note": "Ц Ч Щ Ё Ю Я Э Ъ Ь не входят в официальную таблицу 2021 года: использованы ts, ch, şş, io, iu, ia, e и пропуск",
    }
    return latin, issues, summary
