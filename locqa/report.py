"""Self-contained HTML report (no external assets) rendered with Jinja2."""
from __future__ import annotations

from collections import Counter
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

from .model import Finding

TYPE_RU = {
    "untranslated": "нет перевода", "same_as_source": "совпадает с исходником", "placeholder_mismatch": "плейсхолдеры",
    "plural_mismatch": "формы множественного числа", "glossary_violation": "глоссарий", "length_overflow": "слишком длинно",
    "machine_translation_suspect": "смысл / калька", "inconsistent_term": "разнобой терминов",
    "suffix_harmony": "аффикс после плейсхолдера", "orthography": "орфография", "wrong_language": "другой язык",
}


def write_html(path: Path, findings: list[Finding], queue: list[Finding], source_notes: list[dict], summary: dict) -> None:
    env = Environment(loader=FileSystemLoader(Path(__file__).parent / "templates"),
                      autoescape=select_autoescape(["html"]))
    html = env.get_template("report.html").render(
        findings=findings, queue=queue, notes=source_notes, s=summary, type_ru=TYPE_RU,
        types=Counter(f.issue_type for f in findings).most_common(),
        sev=Counter(f.severity for f in findings),
    )
    Path(path).write_text(html, encoding="utf-8")
