"""Placeholder extraction and ICU MessageFormat validation for ARB strings."""
from __future__ import annotations

import re
from collections import Counter
from dataclasses import dataclass

PRINTF = re.compile(r"%(?:\d+\$)?[sdif@]")
TAG = re.compile(r"</?[a-zA-Z][\w-]*>")

# CLDR cardinal plural categories that a translation must provide (besides "other").
PLURAL_CATEGORIES = {
    "ru": {"one", "few", "many", "other"},
    "uk": {"one", "few", "many", "other"},
    "kk": {"one", "other"},
    "uz": {"one", "other"},
    "en": {"one", "other"},
    "tr": {"one", "other"},
    "es": {"one", "many", "other"},
    "pt": {"one", "many", "other"},
}


class IcuError(ValueError):
    pass


@dataclass
class Message:
    placeholders: list[str]          # simple {name} arguments, in order of appearance
    plurals: dict[str, set[str]]     # arg -> plural selectors (one, other, =0, ...)
    selects: dict[str, set[str]]


def parse(text: str) -> Message:
    """Minimal ICU parser: argument names, plural/select branches; raises IcuError on broken braces."""
    msg = Message([], {}, {})
    _parse_body(text, 0, msg, top=True)
    return msg


def _parse_body(s: str, i: int, msg: Message, top: bool) -> int:
    while i < len(s):
        c = s[i]
        if c == "'":  # ICU quoting: '' is a literal quote, '{...}' is literal text
            j = s.find("'", i + 1)
            if j == i + 1:
                i += 2
                continue
            if i + 1 < len(s) and s[i + 1] in "{}" and j != -1:
                i = j + 1
                continue
            i += 1
        elif c == "{":
            i = _parse_arg(s, i + 1, msg)
        elif c == "}":
            if top:
                raise IcuError(f"unbalanced '}}' at position {i}")
            return i
        else:
            i += 1
    if not top:
        raise IcuError("unclosed '{'")
    return i


def _parse_arg(s: str, i: int, msg: Message) -> int:
    j = s.find("}", i)
    k = s.find(",", i)
    if j == -1:
        raise IcuError(f"unclosed '{{' at position {i - 1}")
    if k == -1 or k > j:  # simple argument {name}
        name = s[i:j].strip()
        if not re.fullmatch(r"[A-Za-z_]\w*", name):
            raise IcuError(f"invalid placeholder name {{{name}}}")
        msg.placeholders.append(name)
        return j + 1
    name = s[i:k].strip()
    rest = s[k + 1:]
    m = re.match(r"\s*(plural|select|selectordinal|number|date|time)\s*(,|})", rest)
    if not m:
        raise IcuError(f"unknown argument type in {{{name}, ...}}")
    kind, i = m.group(1), k + 1 + m.end()
    if m.group(2) == "}":
        msg.placeholders.append(name)
        return i
    if kind in ("number", "date", "time"):
        end = s.find("}", i)
        if end == -1:
            raise IcuError(f"unclosed {{{name}, {kind}, ...}}")
        msg.placeholders.append(name)
        return end + 1
    selectors: set[str] = set()
    while True:
        m = re.compile(r"\s*(offset:\d+\s*)?").match(s, i)
        i = m.end()
        if i < len(s) and s[i] == "}":
            break
        m = re.compile(r"(=?[\w-]+)\s*\{").match(s, i)
        if not m:
            raise IcuError(f"malformed branch in {{{name}, {kind}, ...}}")
        selectors.add(m.group(1))
        i = _parse_body(s, m.end(), msg, top=False) + 1
    if "other" not in selectors:
        raise IcuError(f"{{{name}, {kind}}} has no 'other' branch")
    (msg.plurals if kind in ("plural", "selectordinal") else msg.selects).setdefault(name, set()).update(selectors)
    return i + 1


def tokens(text: str) -> Counter:
    """Multiset of everything that must survive translation unchanged: {args}, %s, <tags>."""
    c: Counter = Counter()
    try:
        m = parse(text)
        c.update("{" + p + "}" for p in m.placeholders)
        for arg in list(m.plurals) + list(m.selects):
            c["{" + arg + "}"] += 1
    except IcuError:
        c.update(re.findall(r"\{[^{}]*\}", text))
    c.update(PRINTF.findall(text))
    c.update(TAG.findall(text))
    return c


def missing_plural_forms(text: str, locale: str | None) -> dict[str, set[str]]:
    required = PLURAL_CATEGORIES.get((locale or "").split("_")[0])
    if not required:
        return {}
    out = {}
    for arg, sel in parse(text).plurals.items():
        miss = required - sel
        if miss:
            out[arg] = miss
    return out
