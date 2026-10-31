"""Decision-model layer (OpenRouter Decisions API): typed questions with calibrated answers.

Used where a probability is worth more than prose:
  triage  — does this string have a translation problem, and which type (fixed taxonomy, no free text);
  verify  — is a finding from rules/LLM real (a second, independent opinion that sets `confidence`);
  prefer  — would a native speaker prefer the proposed fix over the current text (mirrors the jury's blind test).
Answers below the routing threshold go to the human QA queue instead of the report.
"""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from .model import Finding

TYPE_CRITERIA = {
    "none": "Перевод верный и естественный; проблем нет.",
    "same_as_source": "Казахский текст совпадает с русским и это не бренд, не имя и не заимствование.",
    "wrong_language": "Текст написан на другом языке (узбекский, английский, португальский), а не на казахском.",
    "placeholder_mismatch": "Плейсхолдеры {..}, %s или теги <g> отличаются от русской строки.",
    "length_overflow": "Перевод короткой кнопки или заголовка заметно длиннее исходника, часто с лишним хвостом.",
    "machine_translation_suspect": "Смысл не совпадает с русским (другое слово, чужая строка, другие числа) "
                                   "или это калька с русским порядком слов.",
    "glossary_violation": "Игровой термин (фракция, отбасы, Grand Coins, бизнес, варн) переведён не по глоссарию.",
    "inconsistent_term": "Термин переведён иначе, чем в соседних строках того же раздела.",
    "suffix_harmony": "Сразу после плейсхолдера жёстко стоит падежный аффикс («{name}-ға»).",
    "orthography": "Опечатка или казахская буква (ә ғ қ ң ө ұ ү һ і) заменена русской или латинской.",
    "untranslated": "Перевода нет, строка пустая.",
}

SEVERITY_SCALE = [
    "Проблемы нет или она незаметна игроку",
    "Мелкая шероховатость, смысл понятен",
    "Явная калька или неверный термин, смысл угадывается",
    "Игрок поймёт неверно или не поймёт вовсе",
]


def _state(f_or_key, source, target, refs, section=None, neighbors=None) -> dict:
    key = f_or_key.key if isinstance(f_or_key, Finding) else f_or_key
    st = {"game": "Grand Mobile, мобильная ролевая игра; строки интерфейса", "key": key,
          "section": section or source.section_of(key), "ru": source.strings.get(key), "kk": target.strings.get(key)}
    for code, ref in refs.items():
        if ref.strings.get(key) is not None:
            st[code] = ref.strings[key]
    if neighbors:
        st["neighbors"] = neighbors
    return st


def triage_questions() -> dict:
    return {
        "has_problem": {"type": "noul", "instructions": "Есть ли проблема в казахском переводе (kk) русской строки (ru)?",
                        "criteria": {"true": "В kk есть ошибка любого типа из списка issue_type.",
                                     "false": "kk верно и естественно передаёт ru; заимствования и бренды допустимы."}},
        "issue_type": {"type": "choice", "instructions": "Какая главная проблема у kk?", "criteria": TYPE_CRITERIA},
        "severity": {"type": "score", "instructions": "Насколько проблема мешает игроку?", "criteria": SEVERITY_SCALE},
    }


def verify_questions(f: Finding) -> dict:
    claim = f"Заявленная проблема: {f.issue_type}. Обоснование: {f.evidence}"
    return {
        "is_real": {"type": "noul", "instructions": claim + " Это действительно ошибка перевода?",
                    "criteria": {"true": "Носитель казахского согласится, что это ошибка, и захочет её исправить.",
                                 "false": "Это допустимый вариант перевода, заимствование или ложная тревога."}},
        "issue_type": {"type": "choice", "instructions": "Какой тип у этой проблемы?", "criteria": TYPE_CRITERIA},
    }


def prefer_questions(suggestion: str) -> dict:
    return {
        "preferred": {"type": "choice",
                      "instructions": f"Сравни текущий kk и предложение «{suggestion}». Что выберет носитель "
                                      f"казахского для этой строки игры?",
                      "criteria": {"suggestion": "Предложение лучше: точнее по смыслу, естественнее, по глоссарию.",
                                   "current": "Текущий kk лучше или предложение что-то портит.",
                                   "equal": "Разницы для игрока нет."}},
        "meaning_kept": {"type": "noul", "instructions": f"Передаёт ли предложение «{suggestion}» смысл ru полностью?",
                         "criteria": {"true": "Смысл ru передан полностью, ничего не добавлено и не потеряно.",
                                      "false": "Смысл искажён, что-то потеряно или добавлено."}},
    }


def vote_questions(ru_text: str, variants: list[str]) -> dict:
    """One string translated differently across keys: which variants are correct? (the majority can be wrong)."""
    return {"best": {"type": "choice",
                     "instructions": f"Русская строка интерфейса игры «{ru_text}» в разных местах переведена на казахский "
                                     f"по-разному. Какой вариант правильный и естественный?",
                     "criteria": {f"v{i}": v for i, v in enumerate(variants)}}}


def run_many(client, model: str, jobs: list[tuple[object, dict, dict]], workers: int = 8) -> list[tuple[object, dict | None, str | None]]:
    """jobs: (tag, state, questions) -> [(tag, answers or None, error or None)]."""
    def one(job):
        tag, st, q = job
        try:
            return tag, client.decide(model, st, q), None
        except Exception as e:  # noqa: BLE001 — collect and report, never stop the batch
            return tag, None, str(e)[:300]
    with ThreadPoolExecutor(workers) as ex:
        return list(ex.map(one, jobs))


def p_true(ans: dict | None, name: str) -> float | None:
    a = (ans or {}).get(name)
    return None if a is None else float(a.get("noul"))


def top_choice(ans: dict | None, name: str) -> tuple[str | None, float | None]:
    a = (ans or {}).get(name)
    if a is None:
        return None, None
    probs = a.get("probabilities") or {}
    return a.get("choice"), a.get("confidence", probs.get(a.get("choice")))
