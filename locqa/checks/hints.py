"""Cheap signals that are too noisy to report on their own but give the LLM (and the reviewer) concrete evidence."""
from __future__ import annotations

import re
from collections import Counter, defaultdict
from dataclasses import dataclass

from .rules import Context

_NORM = re.compile(r"\W+", re.U)


def _norm(s: str) -> str:
    return _NORM.sub(" ", s.lower()).strip()


@dataclass
class Hint:
    key: str
    kind: str   # copied_translation | number_mismatch | term_usage
    text: str


def copied_translation(ctx: Context) -> list[Hint]:
    """Target text identical to another key's target while the sources share no words (e.g. Брак == Законопослушность)."""
    by_tgt = defaultdict(list)
    for k, v in ctx.target.strings.items():
        if k in ctx.source.strings and len(_norm(v)) > 2:
            by_tgt[_norm(v)].append(k)
    out = []
    for ks in by_tgt.values():
        if not 1 < len(ks) <= 4:
            continue
        words = {k: set(_norm(ctx.source.strings[k]).split()) for k in ks}
        en = next(iter(ctx.refs.values()), None)
        ewords = {k: set(_norm(en.strings.get(k, "")).split()) if en else set() for k in ks}
        for k in ks:
            others = [o for o in ks if o != k and not (words[k] & words[o]) and not (ewords[k] & ewords[o])]
            if others:
                o = others[0]
                out.append(Hint(k, "copied_translation",
                                f"kk совпадает с переводом ключа {o} (ru: «{ctx.source.strings[o]}»), хотя исходники разные"))
    return out


def number_mismatch(ctx: Context) -> list[Hint]:
    out = []
    for k, s in ctx.source.strings.items():
        t = ctx.target.strings.get(k)
        if not t:
            continue
        a = Counter(re.findall(r"\d+", re.sub(r"\{[^}]*\}", "", s)))
        b = Counter(re.findall(r"\d+", re.sub(r"\{[^}]*\}", "", t)))
        if a != b:
            out.append(Hint(k, "number_mismatch",
                            f"числа в ru: {sorted(a.elements()) or '—'}, в kk: {sorted(b.elements()) or '—'}"))
    return out


def term_usage(ctx: Context, glossary: list[dict]) -> list[Hint]:
    """For glossary terms present in a source string: which target rendering the glossary expects."""
    out = []
    for row in glossary:
        ru_term, kk_term = row.get("ru", "").strip(), row.get("kk", "").strip()
        if not ru_term or not kk_term:
            continue
        stem = ru_term.lower()[: max(4, len(ru_term) - 2)]
        for k, s in ctx.source.strings.items():
            t = ctx.target.strings.get(k) or ""
            if stem in s.lower() and kk_term.lower()[:4] not in t.lower():
                out.append(Hint(k, "term_usage", f"глоссарий: «{ru_term}» → «{kk_term}», в kk этого термина не видно"))
    return out


FRAGMENT_KEY = re.compile(r"(Prefix|Suffix|Start$|End$|Part\d+$|_prefix|_suffix)")


def fragments(ctx: Context) -> list[Hint]:
    """Strings that are pieces of a sentence glued in code ("Игру в ", "кейс?"): show the neighbours they join with."""
    out = []
    for k, s in ctx.source.strings.items():
        words = re.findall(r"[^\W\d_]+", s)
        piece = (s != s.strip() and len(words) <= 4) or (FRAGMENT_KEY.search(k) and len(words) <= 4)
        if not piece or not words:
            continue
        near = [e for e in ctx.source.neighbors(k, 1)]
        if near:
            ctx_txt = " | ".join(f"{e.key}: «{e.value}» / kk «{ctx.target.strings.get(e.key, '')}»" for e in near)
            out.append(Hint(k, "fragment", f"похоже на кусок фразы, склеиваемой в коде; соседи: {ctx_txt}"))
    return out


def same_single_word(ctx: Context) -> list[Hint]:
    """One-word strings identical to the source: a loanword (Профиль) or an untranslated word (Напугать)."""
    out = []
    for k, s in ctx.source.strings.items():
        t = ctx.target.strings.get(k)
        words = re.findall(r"[^\W\d_]+", s)
        if t == s and len(words) == 1 and re.search(r"[А-Яа-яЁё]", s):
            out.append(Hint(k, "same_single_word", "kk совпадает с ru одним словом: заимствование или забыли перевести?"))
    return out


def term_variants(ctx: Context) -> list[Hint]:
    """Concordance across the whole file: lets the batch-local LLM see how the same source is translated elsewhere."""
    from .rules import term_groups
    out = []
    for src, variants in term_groups(ctx).items():
        desc = "; ".join(f"«{ctx.target.strings[ks[0]]}» ×{len(ks)} ({', '.join(ks[:2])})" for ks in variants.values())
        for ks in variants.values():
            for k in ks:
                out.append(Hint(k, "term_variants", f"эта же ru-строка по всему файлу переведена так: {desc}"))
    return out


def run(ctx: Context, glossary: list[dict] | None = None) -> list[Hint]:
    return (copied_translation(ctx) + number_mismatch(ctx) + term_usage(ctx, glossary or [])
            + fragments(ctx) + same_single_word(ctx) + term_variants(ctx))
