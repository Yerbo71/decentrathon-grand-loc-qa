# locqa: контроль качества локализации Flutter ARB

Решение кейса Grand Mobile «Qazaq Loc QA» на Decentrathon 6.0.

`locqa` сравнивает перевод (например, `intl_kk.arb`) с исходником (`intl_ru.arb`), находит ошибки локализации и пишет
отчёт `report.json` и HTML. Затем носитель языка проверяет предложенные исправления, и из одобренных собирается
`intl_kk_fixed.arb`, у каждой изменённой строки которого стоит отметка о проверке человеком.

> **Статус:** весь конвейер работает: `check → suggest → review → apply`, GitHub Action, анализ kk и uz.
> Итоги и цифры — в [METHODOLOGY.md](METHODOLOGY.md).

## Что сдаём (по ТЗ)

| Артефакт | Что это |
|---|---|
| `locqa check --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json` | CLI + HTML-отчёт |
| `glossary.csv` | Игровые термины: ru, kk, en, комментарий по употреблению |
| `intl_kk_fixed.arb` | Исправленный казахский файл с флагом проверки человеком у каждой изменённой строки |
| `METHODOLOGY.md` | Как искали и исправляли, метрики, сколько строк проверил носитель, стоимость LLM |

Как оценивают: детекция подложенных ошибок 35%, качество исправлений по слепой оценке носителей 35%,
универсальность 15%, объяснимость находок 15%. Подробности и стратегия в [PLAN.md](PLAN.md).

## Быстрый старт

Нужно: Python 3.10+, git. Flutter нужен только для финальной проверки файла (см. ниже).

```bash
git clone <repo> && cd <repo>
pip install -e ".[dev,llm]"
cp .env.example .env          # вписать OPENROUTER_API_KEY (ключ спросить у команды)
pytest                        # тесты
```

### Запуск проверки

```bash
H=grand-loc-qa-handout-2026-10-27/handout
# только правила: без сети и ключа, ~1 секунда
python -m locqa.cli check --source $H/intl_ru.arb --target $H/intl_kk.arb --glossary glossary.csv     --ref $H/intl_en.arb --ref $H/intl_uz.arb --out out/kk/report.json
# + проверка смысла через LLM и независимая верификация decision-моделью
python -m locqa.cli check ... --llm --decide typesafe/jev-1.14
```

Что получится в `out/kk/`:

| Файл | Что внутри |
|---|---|
| `report.json` | Находки строго по схеме ТЗ, отсортированы по severity |
| `report.html` | Отчёт для людей: фильтры, поиск, «исходник / сейчас / предлагаем / почему» |
| `qa_queue.json` | Неуверенные находки (`confidence` ниже `--min-confidence`, по умолчанию 0.6): их разбирает QA, в отчёт они не идут |
| `source_notes.json` | Ошибки в русском исходнике, замеченные по ходу проверки (для студии) |
| `summary.json` | Сводка, структура файла (дубли ключей, лишние ключи, ключи, которые не примет gen-l10n), стоимость LLM |

`--fail-on high` возвращает код 1, если есть high-находки: так проверку можно поставить в CI.

### Данные

**Данных в репозитории нет и не должно быть** (условие организаторов). Возьмите архив handout у команды
и распакуйте в корень проекта, чтобы получилось:

```
grand-loc-qa-handout-2026-10-27/handout/intl_ru.arb   # исходник
grand-loc-qa-handout-2026-10-27/handout/intl_kk.arb   # казахский (в нём ~100 подложенных ошибок)
grand-loc-qa-handout-2026-10-27/handout/intl_uz.arb
grand-loc-qa-handout-2026-10-27/handout/intl_en.arb
```

Эта папка, `out/`, `scratch/`, `.env` и `.llm_cache/` уже лежат в `.gitignore`. Перед `git add` проверяйте, что
`.arb` из handout не попали в коммит. Тесты пишем только на синтетических ARB в `tests/fixtures/`.

### Переменные окружения (`.env`)

| Переменная | По умолчанию | Зачем |
|---|---|---|
| `OPENROUTER_API_KEY` | — | Ключ OpenRouter. Без него работают только проверки правилами |
| `LOCQA_MODEL` | `anthropic/claude-sonnet-5.5` | Массовая проверка смысла и калек |
| `LOCQA_FIX_MODEL` | `anthropic/claude-opus-5.5` | Черновики исправлений |
| `LOCQA_CACHE_DIR` | `.llm_cache` | Кэш ответов: повторный прогон с теми же строками бесплатный |

## Проверка через flutter gen-l10n

ТЗ требует, чтобы исправленный файл компилировался стандартным `flutter gen-l10n`. Для этого есть мини-проект
`tools/l10n_check/`:

```bash
cd tools/l10n_check && flutter pub get && cd ../..     # один раз
python tools/l10n_check/validate.py grand-loc-qa-handout-2026-10-27/handout/intl_ru.arb out/intl_kk_fixed.arb
```

Скрипт ищет Flutter в `PATH`, затем в `C:\tools\flutter`; другой путь задаётся через `FLUTTER=/path/to/flutter`.
Установка: скачать zip со страницы [Flutter SDK archive](https://docs.flutter.dev/release/archive) и распаковать.

Важно знать:
- **Исходный `intl_ru.arb` сам не проходит `gen-l10n`**: ключи `UNeedHelp`, `TowConfirmDesc` и
  `inventoryEquipped@description` не годятся как имена Dart-методов. `validate.py` убирает их из временных копий
  и сообщает об этом. В нашем файле эти ключи не переименовываем: игра обращается к ним по имени.
- `gen-l10n` **не замечает** пропавших в переводе плейсхолдеров. Их ловит только наш инструмент.

## Как устроен конвейер

```
правила (rules.py) → подсказки (hints.py) → LLM: смысл и термины (judge.py, промпт v3)
   → decision-модель: «ошибка реальна?» (decide.py) → report.json / qa_queue.json
   → LLM: исправления (fix_v2) → валидатор (fix.py) → носитель → intl_kk_fixed.arb → flutter gen-l10n
```

- **Правила** ловят механические ошибки: нет перевода, плейсхолдеры, совпадение с ru, чужой язык, буквы ә/ғ/қ, заменённые
  русскими, аффикс после плейсхолдера, длина кнопок.
- **Подсказки** — слабые сигналы, которые сами в отчёт не идут, но дают LLM доказательства: перевод скопирован из
  другого ключа, разошлись числа, кусок склеиваемой фразы, та же ru-строка в других ключах переведена иначе.
- **LLM (generative)** ищет смысловые ошибки и кальки, объясняет их обратным переводом и предлагает правку.
- **Decision-модель** (OpenRouter Decisions API, например `typesafe/jev-1.14`) отвечает на типизированные вопросы
  калиброванной вероятностью: ошибка реальна? какой тип? предпочтёт ли носитель исправление? Вероятность становится
  `confidence`, неуверенное уходит в QA. Стоит ≈ $0,03 на весь файл.
- Почему так, а не «один промпт на всё»: см. [docs/SIMULATION.md](docs/SIMULATION.md) — прогон конвейера на имитации
  модели, 5 циклов, разбор провалов.

## Структура

```
locqa/
  arb.py        парсер ARB: порядок ключей, разделы @@comment, дубли, метаданные @key, номера строк; запись обратно
  icu.py        плейсхолдеры {x}, %s, теги <g>; разбор ICU plural/select, обязательные формы по CLDR
  llm.py        клиент OpenRouter: JSON-ответы, кэш на диске, учёт токенов и $ по usage.cost
  checks/       rules.py — детерминированные проверки; hints.py — подсказки для LLM
  judge.py      LLM-проверка по разделам: батчи, промпты (prompts/), разбор и валидация ответа
  decide.py     вопросы к decision-моделям: triage, verify, prefer, vote
  fix.py        валидатор предложенных исправлений
  cli.py        locqa check
  report.py     HTML-отчёт (templates/report.html)
tools/l10n_check/   мини-проект Flutter для проверки через gen-l10n
PLAN.md         стратегия и план работ
CLAUDE.md       контекст для Claude Code: особенности данных и доменные правила
```

## Особенности данных, о которых легко споткнуться

- `"@@comment"` повторяется ~130 раз и делит файл на разделы (экраны игры). `json.load` оставит только последний,
  поэтому читаем ARB через `locqa.arb.load()`.
- В `intl_kk.arb` 109 дублирующихся ключей, у 10 из них разные значения. Действует последнее вхождение,
  как и в Flutter.
- Плейсхолдеры бывают двух видов: ICU `{name}` и printf `%s`. Теги `<g>…</g>` и переносы `\n` тоже нужно сохранять.
- `currencySymbol` = `\uea34` — символ из шрифта игры, это не ошибка.
- Совпадение перевода с русским часто законно (`Профиль`, `Капот`, бренды, имена). Ошибка — это русское предложение.

## Типы находок

`untranslated`, `same_as_source`, `placeholder_mismatch`, `plural_mismatch`, `glossary_violation`, `length_overflow`,
`machine_translation_suspect`, `inconsistent_term`, `suffix_harmony`, `orthography`, `wrong_language`.
Других кодов не вводим: жюри считает recall с учётом типа. Формат одной находки:

```json
{
  "key": "garage_rent_button",
  "issue_type": "length_overflow",
  "severity": "high",
  "source": "Арендовать",
  "target": "Жалға алу үшін басыңыз",
  "evidence": "28 символов против 10 в исходнике, ключ похож на кнопку",
  "suggestion": "Жалға алу",
  "confidence": 0.95,
  "reviewed_by_human": true
}
```

## Флаг проверки в исправленном файле

Каждая изменённая строка в `intl_kk_fixed.arb` получает метаданные. Проверено, что `gen-l10n` такой формат принимает:

```json
"bankTotalToPay": "Төлеуге барлығы:",
"@bankTotalToPay": {
  "x-locqa": { "reviewed_by_human": true, "reviewer": "...", "original": "Jami to‘lov:", "issues": ["wrong_language"] }
}
```

Меняем только строки, которые одобрил носитель. Непроверенные правки жюри не засчитывает, а массовые замены снижают оценку.

## Что в работе

- [x] Детекторы правилами: untranslated, placeholder, plural, same_as_source, wrong_language, orthography, suffix_harmony, length_overflow, glossary
- [x] LLM-проверка: machine_translation_suspect и смысловые подмены (проверено на имитации, см. docs/SIMULATION.md)
- [x] `locqa check` → `report.json` + `report.html` + `qa_queue.json`
- [x] Тесты: `pytest` (15 тестов на синтетических ARB)
- [x] Бенчмарк decision-моделей на размеченной выборке: `python tools/decisions_bench.py` (результаты в docs/SIMULATION.md)
- [x] `locqa suggest / review / apply`: очередь исправлений, страница ревью носителя, сборка `intl_kk_fixed.arb`
- [x] GitHub Action (`action.yml`, пример `docs/github-workflows/arb-qa.yml`) и анализ узбекского (профиль `uz`)
- [ ] Страница ревью для носителя → `decisions.jsonl` → `locqa apply` → `intl_kk_fixed.arb`
- [ ] `glossary.csv`
- [ ] Синтетический инжектор ошибок по всему файлу (recall по типам)
- [x] Бонус: переформулировка строк с аффиксом после плейсхолдера (fix v2, `{price}-ге сатып алу` → `Сатып алу: {price}`)
- [ ] Бонус: транслитерация kk в латиницу
