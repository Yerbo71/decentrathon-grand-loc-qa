from __future__ import annotations

from dataclasses import asdict, dataclass, field

ISSUE_TYPES = (
    "untranslated", "same_as_source", "placeholder_mismatch", "plural_mismatch", "glossary_violation",
    "length_overflow", "machine_translation_suspect", "inconsistent_term", "suffix_harmony", "orthography",
    "wrong_language",
)
SEVERITIES = ("high", "medium", "low")


@dataclass
class Finding:
    key: str
    issue_type: str
    severity: str
    source: str
    target: str
    evidence: str
    suggestion: str | None = None
    confidence: float = 0.5
    reviewed_by_human: bool = False
    detector: str = field(default="", compare=False)  # internal: which check produced it

    def to_report(self) -> dict:
        d = asdict(self)
        d.pop("detector")
        return d
