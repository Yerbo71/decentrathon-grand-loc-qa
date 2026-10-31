"""Per-language knowledge used by the rule checks. Adding a language = adding a profile."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Profile:
    code: str
    script: str                                   # "cyrl" | "latn"
    letters: str                                  # language-specific letters (beyond the base alphabet)
    look_alikes: dict[str, str] = field(default_factory=dict)  # specific letter -> what it gets mistyped as
    case_suffixes: tuple[str, ...] = ()           # suffixes that must agree with the stem (vowel harmony)
    foreign_markers: dict[str, str] = field(default_factory=dict)  # regex -> language it indicates


KK = Profile(
    code="kk",
    script="cyrl",
    letters="әғқңөұүһіӘҒҚҢӨҰҮҺІ",
    look_alikes={"ә": "а", "ғ": "г", "қ": "к", "ң": "н", "ө": "о", "ұ": "у", "ү": "у", "һ": "х", "і": "и",
                 "Ә": "А", "Ғ": "Г", "Қ": "К", "Ң": "Н", "Ө": "О", "Ұ": "У", "Ү": "У", "Һ": "Х", "І": "И"},
    case_suffixes=(
        "ға", "ге", "қа", "ке", "на", "не", "а", "е",            # dative
        "ның", "нің", "дың", "дің", "тың", "тің",                # genitive
        "ны", "ні", "ды", "ді", "ты", "ті", "н",                 # accusative
        "да", "де", "та", "те", "нда", "нде",                    # locative
        "дан", "ден", "тан", "тен", "нан", "нен",                # ablative
        "мен", "бен", "пен",                                     # instrumental
        "лар", "лер", "дар", "дер", "тар", "тер",                # plural
        "ы", "і", "сы", "сі",                                     # possessive 3sg
        "ыңыз", "іңіз", "ңыз", "ңіз",
        "ншы", "нші", "ыншы", "інші",                            # ordinal
        "орында", "орынға",
    ),
    foreign_markers={
        r"[oOgG][‘’ʻʼ'`]": "uz",                                   # o‘ g‘ in Uzbek Latin
        r"\b\w+(?:ni|lar|ning|dagi|ga)\b": "uz",
        r"\b(?:sh|ch)\w*": "uz",
        r"[ãõçáéíóú]": "pt",
        r"[ўҳ]": "uz",
    },
)

UZ = Profile(
    code="uz",
    script="latn",
    letters="‘ʻ’",
    foreign_markers={r"[А-Яа-яЁё]{3,}": "ru"},
)

RU = Profile(code="ru", script="cyrl", letters="ёЁ")
EN = Profile(code="en", script="latn", letters="")

PROFILES = {p.code: p for p in (KK, UZ, RU, EN)}


def get(code: str | None) -> Profile:
    base = (code or "").split("_")[0].lower()
    return PROFILES.get(base, Profile(code=base or "xx", script="cyrl", letters=""))
