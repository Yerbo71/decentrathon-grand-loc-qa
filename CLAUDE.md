# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Что это

Хакатон Decentrathon 6.0, кейс Grand Mobile №02 «Qazaq Loc QA». Нужно сделать QA-инструмент для локализации
Flutter ARB и применить его к казахскому файлу игры. Полное ТЗ: `Decentrathon 6.0 Grand Mobile Qazaq Loc.docx`
(текст достаётся через `zipfile` + `word/document.xml`). План работ лежит в `PLAN.md`.

Что сдаём:
- CLI `locqa check --source intl_ru.arb --target intl_kk.arb --glossary glossary.csv --out report.json` и HTML-отчёт;
- `glossary.csv` с колонками ru, kk, en и комментарием по употреблению;
- `intl_kk_fixed.arb`: у каждой изменённой строки флаг проверки человеком, файл обязан компилироваться `flutter gen-l10n`;
- описание методологии со стоимостью LLM-вызовов.

Схема одной находки в `report.json` фиксирована ТЗ: `key, issue_type, severity (high|medium|low), source, target,
evidence, suggestion, confidence, reviewed_by_human`. Допустимые `issue_type`: `untranslated, same_as_source,
placeholder_mismatch, plural_mismatch, glossary_violation, length_overflow, machine_translation_suspect,
inconsistent_term, suffix_harmony, orthography, wrong_language`. Других кодов не вводить: recall считают с учётом типа.

## Данные (`grand-loc-qa-handout-2026-10-27/handout/`)

- `intl_ru.arb` — исходник (5479 строк); `intl_kk.arb` — цель, в неё подложено ~100 ошибок; `intl_uz.arb`, `intl_en.arb` — как есть.
  Файлы `._*` — мусор macOS, не трогать и не парсить.
- **Данные нельзя коммитить** (условие организаторов): репозиторий может быть публичным, handout должен быть в `.gitignore`.
  В тестах использовать только синтетические ARB-фикстуры.

Особенности формата, которые ломают наивный парсинг:
- `"@@comment"` повторяется ~130 раз и делит файл на разделы (имя экрана/фичи). Обычный `json.load` оставит только
  последний. Парсить с `object_pairs_hook`, сохраняя порядок и раздел каждого ключа: раздел и соседние строки служат
  контекстом для LLM и для эвристик «это кнопка».
- В `intl_kk.arb` 109 дублирующихся ключей, у 10 значения разные (`bingo_title`, `edu_hud_armor`, ...). Действует
  последнее вхождение (так же ведёт себя `jsonDecode`). В ru 6 дублей, все с одинаковыми значениями.
- Плейсхолдеры двух видов: ICU `{name}` и printf-стиль `%s` (44 вхождения в ru). ICU-плюралов в данных нет, но
  `plural_mismatch` всё равно должен поддерживаться парсером ICU.
- Метаданные `@key` есть только у ~8 ключей. Есть странный ключ `inventoryEquipped@description` (обычная строка, не метаданные).
- `currencySymbol` = `\uea34` (private-use глиф шрифта игры), это не ошибка. Встречается разметка `<g>...</g>`,
  её нужно сохранять как плейсхолдер.
- В kk нет 12 ключей из ru, есть 2 лишних (`inStore`, `more`), 4 пустых значения.
- UTF-8, переводы строк `\n` внутри значений значимы: это ручные переносы под ширину UI.

## Доменные правила

- Совпадение с ru часто легитимно: в kk 287 строк совпадают с ru, большинство из них заимствования (`Профиль`, `Капот`,
  `Металл`, `Аукцион`), бренды, имена, цифры. Ошибкой обычно оказываются целые русские предложения. Без разбора не флагать.
- Глоссарий учитывает игровой сленг: «варн», «мут», «фура», «ГК»/GC = Grand Coins. В kk сейчас встречаются формы
  `Grand Coins`, `Grand Coin`, `GC`, `GRAND COINS`; «семья» → `отбасы`, «фракция» → `фракция`, «бизнес» → `бизнес`.
- Аффикс после плейсхолдера (`{provider}-ға`, `{price}-ге`) — это `suffix_harmony`: выбор окончания зависит от
  подставленного значения. Исправляется переформулировкой без аффикса или пометкой «нужна обработка в коде».
- Казахские буквы ә ғ қ ң ө ұ ү һ і, заменённые русскими аналогами или латинскими `i`/`c`, — это `orthography`.
- Узбекская латиница (`o‘`, `g‘`, `-ni`), португальский и английский текст в kk — это `wrong_language`.

## Ограничения на исправления

- Не переписывать файл через LLM целиком. Каждая изменённая строка попадает в слепое сравнение носителями
  (выборка 150 строк), а непроверенные массовые замены снижают оценку. Меняем только то, что одобрил человек.
- Плейсхолдеры, `<g>`-теги и `\n` в исправлении должны совпадать с исходником.

## Окружение и команды

```bash
pip install -e ".[dev,llm]"     # пакет locqa; скрипт locqa.exe ставится в %APPDATA%\Python\Python311\Scripts (не в PATH),
                                # поэтому надёжнее запускать через python -m locqa.cli
pytest                          # все тесты; один тест: pytest tests/test_x.py::test_name
python tools/l10n_check/validate.py <intl_ru.arb> <intl_kk_fixed.arb>   # настоящий flutter gen-l10n
```

- Flutter 3.47.9 лежит в `C:\tools\flutter` (не в PATH). `validate.py` находит его сам или берёт путь из `FLUTTER`.
- `gen-l10n` не проходит уже на **исходном** `intl_ru.arb`: ключи `UNeedHelp`, `TowConfirmDesc` и
  `inventoryEquipped@description` не годятся как имена Dart-методов. Поэтому `validate.py` убирает их из временных
  копий и сообщает об этом. В `intl_kk_fixed.arb` эти ключи не переименовывать: игра обращается к ним по имени.
- `gen-l10n` не ругается на пропавшие в переводе плейсхолдеры, это ловит только наш инструмент.
- Флаг проверки в fixed-файле хранится в метаданных: `"@key": {"x-locqa": {"reviewed_by_human": true, "original": ..., "issues": [...]}}`.
  Проверено, что `gen-l10n` такой формат принимает.
- LLM: OpenRouter, `locqa/llm.py`. Ключ `OPENROUTER_API_KEY` в `.env` (шаблон `.env.example`), модели задаются через
  `LOCQA_MODEL` / `LOCQA_FIX_MODEL`. Ответы кэшируются в `.llm_cache/`, повторный прогон бесплатный, стоимость
  считается по `usage.cost` от OpenRouter.
- Скрипты исследования данных лежат в `scratch/`, это одноразовый код (в `.gitignore`).
