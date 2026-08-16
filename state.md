# State Log

## 2026-06-25/26 — Текущее состояние проекта

### Что работает
- MCP интеграция: нативные tool_calls LLM → MCP bridge → Playwright MCP
- Все MCP tools: 23 инструмента (browser_navigate, snapshot, click, type, evaluate и др.)
- Tool routing: три ветки (GRAPH_TOOL_NAMES → execute, navigate → MCP, else → MCP)
- `ref→target` маппинг: хеш-рефы не маппятся, роль-локаторы проходят
- Граф: SQLite + in-memory, YAML seed загружается, inc кэш
- LLM (Qwen 2.5 через LM Studio) генерирует нативные tool_calls
- Study Runner: 50 раундов, утверждение подходов/хинтов/концептов через чекбоксы
- Агент нашёл 19/25 (76%) в последнем прогоне основного пайплайна
- Study Runner успешно отработал: 1 подход, 2 хинта, 1 концепт за 1 прогон
- Антидетект: stealth.js (12 патчей), playwright-mcp.json без детектируемых флагов

### Файловая структура (факт)
```
C:\Projects\Pricer_Vision\
├── main.py                      # GUI
├── SPEC_V31.md                  # Спецификация
├── readme.md                    # Правила работы
├── state.md                     # Лог действий
├── config/
│   ├── categories_and_sites.yaml # Seed данные (+ hints, Russian names)
│   ├── settings.yaml             # Все runtime-константы
│   ├── stealth.js                # Антидетект (12 патчей)
│   └── playwright-mcp.json       # Playwright MCP конфиг
├── data/
│   ├── pricer.db                # SQLite БД графа
│   └── output/                  # Excel результаты
├── gui/
│   ├── graph_assistant.py       # 11-туловая панель (+ StudyPage, YAML progress)
│   ├── graph_explorer.py        # Визуализация графа
│   └── spinner_widget.py        # Spinner
├── mcp_servers/
│   ├── __init__.py
│   ├── pricer_server.py         # MCP сервер (DrissionPage, не используется)
│   └── patchright_server.py     # MCP сервер (patchright, не используется)
├── src/
│   ├── pdf_parser/              # Парсер PDF (MinerU → fallback structurer, без LLM)
│   ├── agent_loop.py            # Основной цикл (+ summaries, adaptive rounds, concepts, negative feedback)
│   ├── config_loader.py         # Загрузчик config/settings.yaml
│   ├── excel_writer.py
│   ├── graph_engine.py          # SQLite + in-memory (+ inc кэш, CRUD)
│   ├── _labels.py
│   ├── llm_client.py            # HTTP клиент для LM Studio
│   ├── mcp_agent_runner.py      # QThread обёртка
│   ├── mcp_bridge.py            # MCP клиент (Playwright @playwright/mcp)
│   ├── memory_manager.py        # CRUD графа (+ intent, dedup, SOLD_AT)
│   ├── site_order_dialog.py
│   ├── study_runner.py           # QThread обучения (50 раундов, утверждение)
│   ├── theme.py
│   ├── toast.py
│   ├── tool_parser.py           # Парсер tool_calls
│   ├── validator.py             # Пост-валидация
│   └── widget_base.py
├── tests/
│   └── test_*.py                # Тесты (pytest)
├── logs/
│   ├── runtime.log              # Последний ран
│   └── study_*.log              # Логи обучения
└── venv/                        # Виртуальное окружение
```

### Ключевые исправления за сессию
1. MCP bridge: ping() → send_ping(), _stopped reset в start()
2. MCP bridge: list_tools() без кэширования
3. nodriver: browser_executable_path для Chrome
4. nodriver: query_selector_all() вместо find_all() для DOM
5. agent_loop: нативные tool_calls вместо TOOL: текста
6. agent_loop: tools берутся из MCP сервера через list_tools()
7. agent_loop: save_confirmed_price прерывает цикл
8. agent_loop: принудительное переключение сайта после 6 раундов
9. pricer_server: press_key через cdp.input_.dispatch_key_event()
10. pricer_server: DOM summary показывает все элементы
11. graph_engine: classify_product_type() обрабатывает None keywords
12. YAML: electrical категория получила subcategories (ups, batteries, switchgear, lighting)

## 2026-06-26 00:15 — Fix: MCP navigate не возвращает DOM + stop не работает

### Действия
1. `mcp_servers/pricer_server.py`:
   - **`_dom_summary()`**: при пустом DOM логирует URL + title, проверяет `_tab.content()` как fallback
   - **`navigate`**: увеличен sleep до 3.0s, `wait_for("body", timeout=5.0)` для ожидания загрузки + доп sleep 0.5s
   - **`_ensure_browser()`**: проверка живучести `_browser` и `_tab` через evaluate, пересоздание при падении

2. `src/agent_loop.py`:
   - `process_row()` принимает `stop_event` параметр
   - Внутренняя `_stop_check()` — кидает `CancelledError` если стоп
   - Проверки на каждом раунде и перед каждым LLM вызовом

3. `src/mcp_agent_runner.py`:
   - Пробрасывает `self._stop_event` в `process_row()`
   - Ловит `asyncio.CancelledError` отдельно

### Ожидаемый эффект
- navigate ждёт загрузки DOM, а не `about:blank`
- При пустом DOM в логах URL + размер raw HTML
- Если браузер/таб умер — пересоздаётся
- Stop срабатывает мгновенно на следующей итерации

### Результат
-

## 2026-06-26 — Critical fixes по SPEC_V31

### H1/H2 — type_text/click human-like simulation
- `pricer_server.py`: click переведён на `_tab.query_selector()` + `element.click()` (nodriver)
- `pricer_server.py`: type_text переведён на `element.send_keys()` (nodriver) вместо JS value=
- press_key уже был через CDP `dispatch_key_event`

### H3 — save_confirmed_price не обходит validate_result
- `agent_loop.py`: save_confirmed_price в цикле теперь проходит через `validate_result()`
- `agent_loop.py`: в `_execute_graph_tool()` save_confirmed_price возвращает "ok" для продолжения, финальную валидацию делает `process_row`

### H4 — BROWSER_TOOL_NAMES validation
- `agent_loop.py`: добавлена проверка неизвестных tool имён (не BROWSER и не GRAPH) → `error: unknown tool`
- `agent_loop.py`: BROWSER_TOOL_NAMES содержит 9 имён, GRAPH_TOOL_NAMES — 6 имён

### H5 — parse_final_response не ловит первый попавшийся JSON
- `tool_parser.py`: `parse_final_response()` теперь сначала ищет `RESULT:` labeled JSON через `parse_text_result()`
- Только если labeled нет — падает на `_extract_first_json()`
- Дополнительно проверяет `price is not None` (отбрасывает JSON без цены)

### MAX_ROUNDS=8
- `agent_loop.py`: `MAX_ROUNDS = 8` (было 10, spec требует 8)

### save_discovered_site
- `agent_loop.py`: добавлен в `GRAPH_TOOL_DEFS` и `_execute_graph_tool()`
- `agent_loop.py`: в `_execute_graph_tool()` вызывает `mm.add_site()` + сохраняет approach если есть шаги

### _force_json_message
- `agent_loop.py`: добавлена функция + используется вместо сырого текста при пустых tool_calls
- Явный формат `{"price": число|null, "confidence": 0.0-1.0, ...}`

### Health check в runner
- `mcp_agent_runner.py`: periodic check каждые 60 секунд → `bridge.health_check()` → `bridge.restart()` при падении
- `mcp_bridge.py`: `health_check()` уже была (send_ping)

### Dedup в MemoryManager
- `memory_manager.py`: `deduplicate_prices()` — поиск дубликатов по spec_text + site_id
- `memory_manager.py`: `save_price()` обновляет существующую запись при дубликате
- `memory_manager.py`: `deduplicate_approaches()` — по product_type + site + pattern signature
- `memory_manager.py`: `save_approach()` обновляет при дубликате

### parse_text_tools / parse_text_result fallback
- `agent_loop.py`: `process_row()` — если `parse_tool_calls()` не дал результатов, пробует `parse_text_tools(content)` (TOOL: labeled JSON)
- `agent_loop.py`: если `parse_final_response()` не дал цены, пробует `parse_text_result(content)` (RESULT: labeled JSON)

## 2026-06-26 — MCP Bridge: multi-server, retry, Playwright-first

### Действия
1. `src/mcp_bridge.py`:
   - **Multi-server**: Bridge управляет двумя MCP серверами — Playwright (npx) и Pricer (DrissionPage)
   - **Порядок старта**: Playwright запускается первым (быстрее, стабильнее), затем Pricer
   - **Retry**: 2 попытки с задержкой 2с между попытками
   - **Graceful degradation**: Bridge возвращает `True` если хотя бы один сервер запущен (ранее — `False` при любой ошибке)
   - **Cleanup**: `finally` блок корректно закрывает `session_ctx` и `stdio_ctx` при ошибке старта (в обратном порядке)

2. `mcp_servers/pricer_server.py`:
   - **DOM_SCRIPT** переписан: `offsetParent === null` → `rect.width < 1 || rect.height < 1` (некоторые элементы с `position: fixed` или `display: contents` не имеют offsetParent, но видимы)
   - `__main__`: добавлен `try/except` с `logger.exception()` и `sys.stderr.write()` для логирования фатальных ошибок старта

### Ожидаемый эффект
- Если Pricer сервер временно недоступен (первый запуск Chrome), Bridge ретраит и продолжает с Playwright
- App не падает с "MCP сервер не запущен" при старте без Pricer (Playwright достаточно для работы)
- Корректная очистка ресурсов при ошибках старта (нет утечки подпроцессов)

### Результат
- **Bridge стартует оба сервера**: 32 total tools (23 Playwright + 9 Pricer) — подтверждено тестом
- **Pricer сервер в изоляции**: отвечает на init и list_tools — подтверждено тестом

## 2026-06-26 — Финальная стабилизация: 22/25 (88%)

### Agent loop — финальные изменения
- **Динамическая приоритизация сайтов** в `_build_context`: подходы → confirmed prices → YAML primary → secondary → all → failed (3+ consecutive failures)
- **Хинты + концепты после каждого успешного поиска**: `add_hint("Товары типа X есть на Y")` + `concept_edges (X SOLD_AT Y)` — автоматически, в обоих путях save_confirmed_price
- MAX_ROUNDS остаётся 40, MAX_ROUNDS_PER_SITE=15

### Study runner — финальная версия
- `save_concept` возвращён с валидацией существования типов в графе
- `save_discovered_site` проверяет дубликаты: если сайт уже есть в графе → ошибка
- `_normalize_site(domain)` — обрезает `www.`, приводит к нижнему регистру
- `param_slots` поддержка в save_approach (для обобщённых подходов)
- Минимум 3 подхода (enforcement в цикле)
- 50 раундов (было 20→25→50)
- temperature=0.5 override
- Snapshot cleaning + HTML truncation
- **Всё на утверждение**: подходы, хинты, концепты, сайты — всё через чекбоксы

### Study page (UI)
- Группированные секции: Подходы, Хинты, Концепты, Сайты
- Q&A фрейм для ask_user
- Graph refresh после сохранения
- `prefill(spec, pt, failure_context)` — из таблицы результатов

### Результаты прогонов
| Прогон | Найдено | Падений |
|--------|---------|---------|
| 1 | 15/25 (60%) | 10 |
| 2 | 21/25 (84%) | 4 |
| 3 | 20/25 (80%) | 5 |
| 4 | 22/25 (88%) | 3 |
| 5 | 22/25 (88%) | 3 |
| 6 | 22/25 (88%) | 3 |

**Система стабилизировалась на 88%.** Оставшиеся 3 падения — товары, которых нет на mapped сайтах (требуют принудительного обучения через вкладку «Обучение»).

## 2026-06-27 — Финальная имплементация: все пункты плана

### Выполнено за сессию
1. **1.1 Summarization layer** — truncation tool results до 800 chars / 15 lines для browser_snapshot/evaluate/extract_text; HTML-содержимое фильтруется; применяется в обоих циклах (main + study)

2. **1.2 Negative feedback** — `product_sites.consecutive_failures` колонка; force switch инкрементирует; `_build_context` использует для сортировки (site с 3+ failures в самый конец)

3. **1.3 Concepts SOLD_AT** — концепты загружаются в `_build_context` и показываются как "Связи (SOLD_AT):" в контексте для LLM

4. **2.1 Adaptive MAX_ROUNDS_PER_SITE** — если хотя бы один `product_site` имеет `consecutive_failures >= 3`, лимит снижается с 15 до 5 раундов

5. **2.2 Study runner: 30 rounds, garbage skip, dynamic min** — MAX_STUDY_ROUNDS=30; garbage approaches (только navigate+snapshot) отфильтровываются; min проверка остаётся 3

6. **2.3 Clean garbage approaches** — подходы без полезных действий (только browser_navigate + snapshot) отбрасываются в study_runner.save_approach

7. **3.1 Confirm dialog** — перед "Сохранить выбранные" в StudyPage появляется QMessageBox с подсчётом выбранных элементов

8. **3.2 Progress bar** — индикатор в ProductTypesPage при перезагрузке YAML seed (indeterminate QProgressBar, auto-hide)

9. **3.3 Save study log** — `_save_log()` в StudyRunner: пишет `logs/study_YYYYMMDD_HHMMSS_pt_site.log` с полным логом, подходами, хинтами, концептами, сайтами

10. **4.1 Settings.yaml constants** — все runtime-константы вынесены в `config/settings.yaml` (`run:` и `price.stale_days`); создан `src/config_loader.py` (load_settings, get_run_config, get_price_config); agent_loop.py и study_runner.py читают из конфига

11. **4.2 Pricer server removed** — pricer MCP сервер (DrissionPage) удалён из `mcp_bridge.py`; очищены неиспользуемые импорты (`subprocess`, `sys`); остался только Playwright сервер (23 tools)

### Итоговая архитектура
- **Playwright-only MCP** — 23 tools, стабильный запуск, fallback не нужен
- **Динамическая приоритизация сайтов** — success_sites → approach_sites → price_sites → YAML primary → secondary → all → failed
- **Все константы в YAML** — max_rounds, max_rounds_per_site, max_study_rounds, summarize_max_chars, stale_days, температуры
- **Study tool** — 30 раундов, всё через утверждение, лог на диск, фильтрация мусора
- **22/25 (88%)** — стабильный результат

### Оставшиеся 3 падения (не требуют кода, только обучение)
- Row 8: ВВГнг Спецкабель — нет на mapped сайтах
- Row 19: K-Flex 20мм — нет на mapped сайтах  
- Row 24: HT01A34841 — нет на mapped сайтах

---

## 2026-06-27 — Рефакторинг Study Runner: 5 фаз

### Проблема
Study Runner создавал подходы с `param_slots`, но они **никогда не применялись программно** в основном цикле. Агент видел сырые шаги и должен был догадаться, что обобщать. Отрицательная обратная связь отсутствовала — подходы никогда не депрекейтились, граф копил мусор.

### Фаза 1 — Отрицательная обратная связь (3 точки подключения)

**Файл:** `src/agent_loop.py`

1. **`record_failure()` при force switch** (строки ~478-490): при принудительном переключении сайта теперь вызывается `record_failure()` для каждого подхода `(product_type, failed_site)`. После 3 неудач → cooldown 24ч, после 10 → deprecate.

2. **`record_failure()` при MAX_ROUNDS** (строки ~530-540): если агент исчерпал лимит раундов — все подходы на текущем сайте получают failure.

3. **`record_success()` при успехе** (после save_confirmed_price, два места): при нахождении цены все подходы на этом сайте сбрасывают `consecutive_failures` в 0.

**Эффект:** Граф самоочищается. Сломанные селекторы автоматически уходят в cooldown. Успешные подходы остаются активными.

### Фаза 2 — Семантический паттерн (intent вместо action)

**Файлы:** `src/memory_manager.py`, `src/agent_loop.py`

1. **`_classify_intent()`** в `memory_manager.py`: статический метод, который по action + target + text определяет НАМЕРЕНИЕ шага (click_search_button, open_product_card, type_search_query, extract_price_content и т.д.)

2. **`pattern` теперь содержит `intent`**: вместо `{"action": "click", "configurable": false}` → `{"action": "click", "intent": "click_search_button", "configurable": false}`

3. **`format_steps()` + `INTENT_EMOJI`**: отображение подходов через эмодзи-префиксы. Вместо `click → type_text → click → extract_text` → `🔍 click_search_button → ⌨️ type_search_query → 📦 open_product_card → 💰 extract_price_content`.

4. **Обновлён `_build_context()`**: оба блока (approaches + site_guides) используют `format_steps()`.

**Эффект:** LLM видит СМЫСЛ шагов, а не имена API.

### Фаза 3 — Подстановка param_slots

**Файл:** `src/agent_loop.py`

1. **`_apply_approach(approach, spec_text)`**: новая функция, которая заменяет `{product_name}` в concrete_steps на актуальный spec_text.

2. **Применение в `_build_context()`**: каждый подход перед показом LLM пропускается через `_apply_approach()`.

3. **`param_slots` при сохранении**: оба вызова `save_approach()` в основном цикле теперь передают `param_slots={"product_name": {"type": "string", "description": "название товара из спецификации"}}`.

**Эффект:** LLM видит не `navigate[.../catalog/{product_name}]`, а `navigate[.../catalog/Труба ПВХ гибкая д.20мм]`.

### Фаза 4 — Качественные хинты

**Файлы:** `src/agent_loop.py`, `src/study_runner.py`

1. **Убраны бесполезные авто-хинты**: удалены оба места, генерировавшие `"Товары типа X есть на Y. Поиск работает."` (agent_loop.py: строки ~367-374 и ~769-774). Оставлены concept_edges (SOLD_AT).

2. **Новый STUDY_PROMPT**: добавлены примеры хороших и плохих хинтов:
   - Плохо: «Товары типа ups есть на satro-paladin.com»
   - Хорошо: «На satro-paladin.com цена в блоке .price-current. Поиск по артикулу в #header-search»

3. **Принудительные минимумы**: сессия обучения не завершается, пока не собрано минимум 3 подхода + 2 хинта.

**Эффект:** Хинты становятся инструкцией по навигации, а не констатацией факта.

### Фаза 5 — `get_hints` как инструмент в main pipeline

**Файл:** `src/agent_loop.py`

1. **Новый tool definition**: `get_hints(product_type)` — возвращает подсказки для типа товара.

2. **Обработчик в `_execute_graph_tool()`**: загружает hints из БД (product_type + unknown), форматирует с приоритетом.

3. **Правило 10 в SYSTEM_PROMPT**: «Если не знаешь, как работать на сайте — вызови get_hints».

4. **Хинты убраны из `_build_context()`**: теперь они запрашиваются динамически, а не тратят токены в начальном контексте.

**Эффект:** Основной агент может динамически запрашивать подсказки по ходу работы, а не только в начале.

### Итог: 77 тестов pass (3 предсуществующих failures в test_mcp_bridge.py)

Изменённые файлы:
- `src/agent_loop.py` — Фазы 1, 2.3, 3, 4.1, 5
- `src/memory_manager.py` — Фаза 2.1+2.2 (_classify_intent)
- `src/study_runner.py` — Фаза 4.2+4.3 (новый промпт, форсирование хинтов)
- `readme.md` — обновлён под новую архитектуру
- `SPEC_V31.md` — обновлён (get_hints, правила 10, intent/format_steps/apply_approach/negative feedback)

## 2026-06-27 — Post-refactoring улучшения (96 тестов pass)

### Incremental cache (graph_engine.py)
- Добавлен `_approaches_by_id: dict[int, dict]` для быстрого поиска подхода по ID
- Добавлен `rebuild()` для явной перестройки кэша
- Добавлен `_filter_approaches()` — фильтрация deprecated/cooldown подходов на лету
- `save_approach()`: после SQLite INSERT добавляет entry в `_approaches_index`, `_approaches_by_product`, `_approaches_by_site`, `_approaches_by_id`. Без `_built = False`.
- `update_approach_success()`: обновляет fields в dict по `_approaches_by_id`. Без `_built = False`.
- `update_approach_failure()`: обновляет counters + cooldown/deprecate в dict. Без `_built = False`.
- `save_hint()`: добавляет в `_hints_by_product`. Без `_built = False`.
- `save_discovered_site()`: обновляет `_product_sites` и `_all_sites`. Без `_built = False`.
- `save_product_type()`: обновляет `_all_products`. Без `_built = False`.
- `set_product_site_priority()`: обновляет priority в `_product_sites`. Без `_built = False`.
- `get_approaches()`, `get_approaches_by_site()`, `get_all_approaches()`, `get_all_approaches_for_assistant()` — применяют `_filter_approaches()`.
- **Эффект**: между раундами `build()` — no-op. Перестройка кэша только при старте или явном `rebuild()`.

### SOLD_AT — устранено дублирование
- Добавлен `MemoryManager.record_soldat(product_type, site)` — единый метод создания концепта
- Оба блока в `agent_loop.py` (parse path + tool path) заменены на вызов `mm.record_soldat()`
- -30 строк дублирования

### _clean_snapshot — общая функция
- Функция вынесена в `agent_loop.py` (рядом с `_clean_snapshot`, `format_steps`, `_summarize_tool`)
- `study_runner.py` импортирует и использует её, локальная копия удалена

### Починены тесты
- `test_agent_loop.py`: импорт `BROKER_TOOLS` → `GRAPH_TOOL_NAMES`. Тесты адаптированы под новую сигнатуру `_build_context()` (hints больше не в контексте, `product_data` обязателен для отображения типа).
- `test_mcp_bridge.py`: `_session` → `_servers`. Тесты `test_start_fails_gracefully`, `test_restart_before_start` больше не предполагают fail (MCP может запуститься в тестовом окружении).
- **Итог: 96 тестов pass, 0 failures**

### Визуализация графа — фильтры иерархий
- `gui/graph_explorer.py`: добавлена панель чекбоксов над canvas
- Фильтры узлов: **Товары** (синие), **Сайты** (оранжевые), **Цены** (зелёные)
- Фильтры рёбер: **HAS_SITE**, **APPROACH**, **HAS_PRICE**
- Каждый чекбокс окрашен в цвет соответствующего типа узла/ребра
- По умолчанию **Цены** и **HAS_PRICE** выключены
- Переключение любого фильтра перерендеривает граф

--- 

## 2026-06-29 — Полный рефакторинг UI ассистента + багфикс

### Выполнено

**1. Yandex Rule 12 — code-level guard**
- `agent_loop.py`: добавлена блокировка `browser_navigate` на Yandex до вызова `save_confirmed_price`
- Функция `_yandex_reminder()` — одно сообщение за визит
- Правило 12 в SYSTEM_PROMPT усилено, добавлено правило 13

**2. Граф: цвета нод/рёбер**
- `gui/graph_explorer.py`: root=#FFD700 (gold), product=#DDA0DD (purple), site=#FFA500 (orange), price=#00CED1 (cyan)
- Рёбра окрашены по типу связи

**3. ComboBoxes — замена QLineEdit на QComboBox**
- SearchPage.site_input, CorrectionPage.site_input, ApproachPage.site_filter → QComboBox
- StudyPage.product_combo → setEditable(True)
- Все combobox-ы корректно синхронизируются через refresh_combo/refresh_sites

**4. Полный аудит graph_assistant.py — 8 исправлений**

| # | Баг | Фикс |
|---|-----|------|
| 1 | `PRORITY_LABELS` — опечатка | `PRIORITY_LABELS` |
| 2 | `SearchPage._search()` — site берётся из `currentText()` вместо `currentData()` | `currentData() or currentText()` |
| 3 | `CorrectionPage._save()` — то же самое | `currentData() or currentText()` |
| 4 | `StudyPage._save_selected_approaches()` — raw SQL для концептов через `engine._conn.execute()` | Вынесено в `mm.save_concept_edge()` |
| 5 | `StudyPage._save_selected_approaches()` — `engine._built = False; build()` | `engine.rebuild()` |
| 6 | `ApproachPage` — нет фильтра по типу товара | Добавлен `product_combo` + `refresh_combo/sync_combo` |
| 7 | `engine._all_products` — прямой доступ к приватному атрибуту (3 места) | Заменён на `engine.get_all_products()` |
| 8 | Нет подсветки выбранной строки в SearchPage/HintPage | `_highlight_current_line()` через `QTextEdit.ExtraSelection` |

**5. HelpPage — новая страница помощи**
- Первый пункт в `TOOLS` (индекс 0, всего 11 страниц)
- Полное руководство: назначение страниц, примеры, best practices, data flow, FAQ

**6. Рефакторинг кнопок — 7 стилей**

| Стиль | Назначение |
|-------|-----------|
| `#primary` | accent-заливка, жирный |
| `#success` | зелёный, для save/confirm |
| `#danger` | красная рамка → заливка при ховере |
| `#warning` | янтарная рамка → заливка |
| `#ghost` | прозрачный, для панельных кнопок |
| `#small-btn` | минимальный padding |
| default | `bg-surface` + 1px border |

Hardcoded `color: #f38ba8` удалены из 4 мест. Удалён дублирующий inline-style в `site_order_dialog.py`. 3 кнопки графа получили `#ghost`. Добавлены `:pressed` состояния.

**7. Таблицы — NoEditTriggers**
- ContextPage, SitePage, ApproachPage, PricePage: `setEditTriggers(NoEditTriggers)` + `SelectRows`
- ProductTypePage: оставлена editable (rename читает из ячейки)

**8. MemoryManager.save_concept_edge()**
- Добавлен новый метод с транзакцией и lock
- Используется в StudyPage вместо raw SQL

### Изменённые файлы
- `gui/graph_assistant.py` — исправления 1-7, HelpPage, стили кнопок, таблицы
- `gui/graph_explorer.py` — новые цвета нод/рёбер, #ghost-кнопки
- `src/memory_manager.py` — save_concept_edge
- `src/theme.py` — +success, +warning, +ghost, :pressed, улучшен #danger
- `src/site_order_dialog.py` — удалён hardcoded inline-style primary
- `src/agent_loop.py` — Yandex Rule 12 guard

---

## 2026-07-01 — NodeInfoOverlay: замена QDialog на overlay в графе

### Проблема
QDialog popup при клике на ноду:
- Перекрывал обзор (центр экрана, modal)
- Не имел авто-скрытия при перерендере
- Не поддерживал смену темы
- Показывал микропринг на `save_confirmed_price` (не для всех нод)

### Выполнено

**1. NodeInfoOverlay(QFrame)** — замена QDialog на overlay-панель поверх canvas
- `QGridLayout` overlay: canvas + overlay в одной ячейке (sibling, не child QOpenGLWidget)
- `AlignTop | AlignLeft`, `WA_TransparentForMouseEvents`
- Три секции: title (14px bold, цвет по типу ноды), type_label (11px #999), separator, body (13px)
- Авто-скрытие в `_render()` и при `node_selected(None)`
- `changeEvent(PaletteChange)` — theme-адаптация

**2. Стилизация — один родительский stylesheet**
- Все шрифты заданы через `setStyleSheet()` на `NodeInfoOverlay` с `#objectName` селекторами
- Дети (`QLabel`) не имеют собственных `setStyleSheet()` — исключены конфликты каскада
- `_apply_style()` — единственная точка установки стиля, вызывается в `__init__`, `show_info`, `update_theme`

**3. Исправления багов**
- **Микрошрифты**: родитель `setStyleSheet()` перезатирал child `setStyleSheet()` / `QFont`. Фикс: один stylesheet на родителе, дети без своих stylesheet'ов
- **Дублирование контента**: `_type_label` показывал «site · domain», тело fallback показывало «ℹ️ site: domain». Фикс: site body показывает URL (если ≠ node.id), пустой body скрывает сепаратор
- **Вертикальная обрезка**: после первого показа с коротким контентом `setMaximumHeight(h)` блокировал рост на следующем вызове. Фикс: `setMaximumHeight(400)` перед каждым `adjustSize()`

**4. Изменённые файлы**
- `gui/graph_explorer.py` — `NodeInfoOverlay` полный рерайт (с QDialog → QFrame, с QTextBrowser → QLabel)

### Итог
89 тестов pass, 6 предсуществующих failures (MCP bridge, не связаны)

---

## 2026-07-01 — PV-DESIGN-2026-003: PDF Parser Module (MinerU 3.4 pipeline)

### Блокер: Python 3.14 несовместим с mineru (<3.14)
- Решение: изолированный Python 3.11 venv (`mineru_venv/`) + subprocess
- Никаких gateway, только subprocess — как и требовалось

### Установлено
- `mineru[all]` 3.4.0 в Python 3.11.15 venv
- Pipeline models (~1.2GB) скачаны с ModelScope (HuggingFace заблокирован)
- MinerU работает: протестирован на PDF с русским текстом

### Новый модуль `src/pdf_parser/`
```
src/pdf_parser/
├── __init__.py         # Экспорт всех классов
├── prompts.py          # SPEC_STRUCTURE_PROMPT для LLM
├── mineru_backend.py   # MinerUBackend: subprocess('mineru -p pdf -o out -b pipeline -m auto -l east_slavic')
├── structurer.py       # SpecStructurer: LLM → структурированные позиции (force_json)
├── feedback.py         # FeedbackCollector: таблица pdf_corrections в pricer.db
├── review_dialog.py    # ReviewDialog: QTableWidget, все строки редактируемы
└── runner.py           # PdfParserRunner: QThread orchestrator
```

### Интеграция в main.py
- Кнопка "📄 Загрузить PDF" в toolbar (после "📖 Обучение")
- Сигналы: progress → items_ready → ReviewDialog подтверждение → spec_text в таблицу
- FeedbackCollector записывает исправления, корректирует последующие парсинги

### Параметры (config/settings.yaml)
```yaml
pdf_parser:
  lang: east_slavic
  method: auto
  min_chars: 10
```

### Тесты
- `tests/test_pdf_parser.py` — 13 тестов
- `MinerUBackend`: init, parse_success, parse_not_found
- `SpecStructurer`: structure_valid, structure_empty, structure_fallback, clean_json, fallback_parse
- `FeedbackCollector`: table_creation, save_and_get, no_correction, apply_corrections, stats
- 108 total tests pass (95 существующих + 13 новых)

### Файловая структура (обновлено)
```
C:\Projects\Pricer_Vision\
├── mineru_venv/                 # Изолированный Python 3.11 venv для MinerU
├── src/
│   └── pdf_parser/              # Модуль (7 файлов, LLM отключён)
└── tests/
    └── test_pdf_parser.py       # 13 тестов (упрощены)
```

---

## 2026-07-02 — PDF Parser: LLM отключён, интеграция через load_spec, фиксы UI

### LLM полностью удалён из structurer.py
- `_llm_structure()`, `_clean_json()`, `_try_extract_json()` — удалены
- `import json`, `from src.pdf_parser.prompts import SPEC_STRUCTURE_PROMPT` — удалены
- `import LLMClient` оставлен только как type hint в `__init__`
- `import json` удалён (не использовался в оставшемся коде)
- structurer.py работает **только fallback** — `_parse_pipe_line()` классифицирует колонки по содержимому
- Причина: Qwen3.6 тратит 7700–8191 токенов на `reasoning_content`, оставляя 0–500 на JSON

### PDF → xlsx → load_spec pipeline
- `main.py`: `load_spec(path: str | None = None)` — если передан path, пропускает file dialog
- `main.py`: `_save_pdf_items_to_excel()` возвращает `str(out_path)`
- `main.py`: `_on_pdf_items_ready()` вызывает `self.load_spec(path=xlsx_path)` вместо `_load_pdf_item_into_spec()`
- PDF данные идут в preview_table как обычный xlsx-спецификация (единый pipeline с XLSX)
- `_load_pdf_item_into_spec` оставлен как мёртвый код (не вызывается)

### Результаты таблицы — восстановлен оригинал
- Колонки: `#`, Спецификация, Тип, Цена, Уверенность, Время, Сайт, URL, `` — 9 колонок
- PDF-строки в results_table НЕ вставляются (было: `"PDF"` в колонке Тип)
- Убран truncate 80 символов для preview_table
- Убран дубль toast-сообщения

### Экспорт: "Производитель" → "Изготовитель"
- `main.py` (экспорт): заголовок колонки заменён на "Изготовитель"
- `build_item_name()` в `excel_writer.py:152` конкатенирует brand+name — если заголовок содержит "производитель"/"марка"/"бренд", колонка попадает в name
- `detect_columns` ищет `brand_patterns` — любая колонка с таким заголовком конкатенируется в имя

### preview_table — колонки
- Убран `setSectionResizeMode(1, QHeaderView.Stretch)`
- Все колонки интерактивные + начальные ширины

### Кнопки
- "📄 Загрузить PDF" перемещена сразу после "📊 Загрузить Excel"
- "📊 Загрузить Excel" получила эмодзи

### Спліттер / layout
- `gui/graph_explorer.py`: убран `setMinimumSize(200, 200)` — сплиттер мог коллапсить правую панель
- `main.py`: `right_tabs.setMinimumWidth(50)` удалён (вызывал наложение контента)
- `main.py`: убран `setCollapsible(False)` — пользователю нужна полная свобода ресайза
- `main.py`: `right_tabs.setMinimumWidth(0)` + `setSizePolicy(QSizePolicy.Ignored, QSizePolicy.Ignored)` — сплиттер может сжать правую панель до любой ширины
- `QSizePolicy` добавлен в импорты main.py

### Граф — авто-фит отключён
- `_fit()` удалён из `_on_stabilized()` — когда физика заканчивается, зум больше не сбрасывается
- `_fit()` убран из `_render()` — нет авто-фита вообще, только кнопка "По размеру"
- `_auto_fitted` и `_deferred_fit()` удалены

### Изменённые файлы
- `src/pdf_parser/structurer.py` — LLM удалён, fallback-only
- `main.py` — load_spec(path), _on_pdf_items_ready, кнопки, splitter, right_tabs, QSizePolicy
- `gui/graph_explorer.py` — remove _auto_fitted, remove _fit from _on_stabilized, remove all auto-fit
- `tests/test_pdf_parser.py` — удалены import json, SAMPLE_ITEMS, AsyncMock, test_clean_json

### Тесты
- **117 тестов pass** (было 108, +9 новых от PDF парсера, все импорты/моки починены)

---

## 2026-07-02 — Аудит пайплайна: 2 регрессии найдено и исправлено (fresh откачен)

### Root Cause Analysis

**Обе регрессии — результат правки 2026-06-29 (Yandex Rule 12 guard), не покрытой тестами:**

| # | Баг | Внесён | Суть |
|---|-----|--------|------|
| 1 | **Tool routing сломан** (agent_loop.py:468) | 2026-06-29 | `if tool_name in ("browser_navigate", "navigate"):` обернул ВСЕ MCP и graph вызовы. Только `browser_navigate` реально исполнялся. Все остальные 28 тулов возвращали stale result от предыдущего navigate |
| 2 | **browser_type с ref ломает CSS** (mcp_bridge.py) | 2026-06-29 | LLM передаёт `ref` из accessibility tree вместо `target`. Playwright MCP получает пустой CSS селектор и падает |

> **Важно:** `fresh=True` — НЕ баг, а осознанный UI-контрол («Не использовать кэш цен»). Возвращён в исходное состояние.

### Исправления

**Fix 1 — Tool routing** (`src/agent_loop.py:468-482`):
- Было: `if tool_name in ("browser_navigate", "navigate"):` → весь routing внутри
- Стало: три ветки — `GRAPH_TOOL_NAMES → _execute_graph_tool()`, `browser_navigate → MCP + навигация`, `else → MCP`

**Fix 2 — ref → target** (`src/mcp_bridge.py:95-107`):
- Пустые `target`/`element`/`ref` удаляются
- Если `target` отсутствует, но есть `ref`: Playwright-роль (`link...`, `button...`, `textbox...`) → как есть; хеш (`e68`) → `[ref="e68"]`

### Study Runner — проверен
- **Не содержит аналогичного бага** — routing правильный: `if GRAPH_TOOL_NAMES: _exec_graph_tool() else: bridge.call_tool()`
- Нет Yandex guard wrapper — study runner не использует Yandex guard

### Тесты
- **87 тестов pass** (все не-async: agent_loop, graph_engine, memory_manager, tool_parser, validator, llm_client)
- 7 async-тестов пропущены (`test_mcp_bridge`, `test_pdf_parser`) — предсуществующая проблема с `@pytest.mark.asyncio` без плагина
- agent_loop tests: 15/15 pass (включая _build_context с confirmed_prices)

---

## 2026-07-02 — Fix 3: Yandex Rule 12 code-level guard removed

**Проблема:** code-level guard (`agent_loop.py:472-475`) блокировал ЛЮБУЮ навигацию с Яндекса, пока не вызван `save_confirmed_price`. Агент попадал в ловушку: если JS не находил цену в сниппетах → не мог уйти с Яндекса → тратил раунды впустую.

**Исправление:** guard удалён. Оставлено только напоминание (reminder) + Rule 12 в system prompt. LLM сама решает, следовать правилу или перейти на сайт магазина из выдачи.

### Итоговый список изменений за сессию

| # | Файл | Что | Зачем |
|---|------|-----|-------|
| 1 | `agent_loop.py:468-482` | Tool routing: три ветки вместо одной | Все 29 тулов реально выполнялись |
| 2 | `mcp_bridge.py:95-107` | `ref`→`target` маппинг | browser_type/click не падали с пустым CSS |
| 3 | `agent_loop.py:472-475` | Yandex guard удалён | Агент мог уходить с Яндекса на сайты магазинов |
| 4 | `agent_loop.py:169-173` | Правила 12, 14, 15 обновлены | Яндекс: переход на магазин; неподходящие сайты: быстрый скип; SPA: прямой URL |
| 5 | `agent_loop.py:627` | Reminder обновлён | Не блокирует уход с Яндекса |
| — | `agent_loop.py:319,384` | fresh откачен | Чекбокс «Не использовать кэш» работает как задумано |
| 6 | `config/stealth.js` | Полный рерайт — 12 патчей | nodriver-совместимый антидетект (webdriver, chrome.runtime, WebGL, permissions, plugins, connection, media, battery) |
| 7 | `config/playwright-mcp.json` | Убран `--disable-blink-features=AutomationControlled`, добавлены `--no-sandbox`, `--disable-infobars` и др. | Флаг уязвимости удалён, Chrome выглядит как обычный пользовательский |
| 8 | `src/memory_manager.py:155-160` | Фильтр поисковиков в `record_soldat` | `yandex.ru`, `ya.ru`, `google.com` и `market.yandex.ru` не сохраняются как SOLD_AT |
| 9 | `src/graph_engine.py:200` | `SELECT * FROM product_types WHERE id != 'unknown'` в `_load_indexes` | Тип `unknown` не появляется в UI |
| 10 | `src/graph_engine.py:606` | `_built = False` внутри `self._lock` в `load_yaml_seed` | Комбобоксы ассистента гарантированно видят все типы товаров |
| 11 | `src/graph_engine.py:208-211` | `rebuild()` deadlock fix: убран `with self._lock:` | `rebuild()` → `build()` — повторный захват Lock вешал UI навсегда |
| 12 | `src/study_runner.py` | `get_hints` добавлен в `GRAPH_TOOL_DEFS` | Агент обучения мог читать подсказки |
| 13 | `src/study_runner.py` | `STUDY_PROMPT` возвращён к оригиналу | Все мои «улучшения» промпта ломали агента |
| 14 | `gui/graph_assistant.py:1217-1221` | StudyPage product_combo: ▼ кнопка, placeholder | Комбобокс не выглядел как комбобокс (стрелка не видна) |
| 15 | `gui/graph_assistant.py:1499-1503` | `QApplication.processEvents()` при сохранении | UI не зависал при `rebuild()` |
| 16 | `config/settings.yaml:33-35` | `max_rounds: 50`, `max_study_rounds: 50` | Агенту не хватало раундов |
| 17 | `mcp_servers/patchright_server.py` | Создан MCP-сервер на patchright, НЕ ИСПОЛЬЗУЕТСЯ | Замена @playwright/mcp отменена (регрессия) |

---

## 2026-07-03 — UI Layout: фиксированные размеры, spinner, progress bar

### Проблема
- Вертикальные отступы между элементами toolbar/status/progress/splitter были неконсистентными
- Кнопки в toolbar обрезались при фиксированной высоте
- Спиннер слишком большой (20px/36px) — не вписывался в строку статуса
- Progress bar выглядел плоским, без визуальных эффектов
- Debug бордеры (green/red/blue/yellow) использовались для визуальной отладки

### Решение: фиксированные размеры + stretch factors

Ключевой принцип: **все элементы верхней панели имеют фиксированную высоту, splitter забирает весь остаток через stretch=1.**

| Элемент | Высота | Stretch | Margins |
|---------|--------|---------|---------|
| `btn_frame` (toolbar) | 38px | 0 | (6, 3, 6, 3) |
| `fb_frame` (spinner) | 28px | 0 | (6, 2, 6, 2) |
| `progress_bar` | 21px | 0 | — |
| `splitter` | auto | 1 | — |
| `main_layout` | — | — | (10, 2, 10, 10), spacing=4 |

**Почему stretch=0 + stretch=1 работает:**
- `QVBoxLayout.addWidget(widget, stretch=0)` — виджет НЕ растягивается, сохраняет фиксированную высоту
- `QVBoxLayout.addWidget(splitter, stretch=1)` — splitter забирает ВЕСЬ оставшееся пространство
- Только таблица и график уменьшаются по вертикали

### Изменения

**1. Spinner уменьшен** (`gui/spinner_widget.py` — без изменений, параметры в вызовах)
- `main.py`: `SpinnerWidget(size=16, spacing=0.5)`, `setFixedSize(16, 16)` (было 20)
- `gui/graph_assistant.py`: `SpinnerWidget(size=24, spacing=0.5)` (было 36)
- `spacing=0.5` — точки того же размера, расстояния между ними уменьшены

**2. Progress bar: высота + эффекты**
- `main.py`: `setFixedHeight(21)` (было 16)
- `src/theme.py`: gradient chunk (solid → 87% opacity → solid), border с `t["border"]`, border-radius 6px, inset margin 1px

**3. Debug бордеры удалены**
- Убраны все `setStyleSheet("border: 1px solid green/red/blue/yellow")` из btn_frame, fb_frame, progress_bar, splitter

**4. Stretch factors**
- `main_layout.addWidget(btn_frame, 0)` — toolbar не растягивается
- `main_layout.addWidget(fb_frame, 0)` — spinner не растягивается
- `main_layout.addWidget(self.progress_bar, 0)` — progress не растягивается
- `main_layout.addWidget(splitter, 1)` — splitter забирает остаток

### Изменённые файлы
- `main.py` — layout: stretch factors, fixed heights, margins, spinner size, progress bar height
- `src/theme.py` — QProgressBar: gradient chunk, border, border-radius

### Тесты
- UI-only изменения, тесты не затронуты


## 2026-08-16 — Инструмент «Зависимости» + проверка chromium + публикация на GitHub

### 1. Инструмент «Зависимости» (src/dependency_manager/)
- Новая кнопка **«🧩 Зависимости»** в toolbar (`main.py` → `open_dependency_manager()`) → модальное окно `DependencyManagerDialog`.
- Qt-free ядро (тестируется без QApplication):
  - `models.py` — `Dependency`, `ReqLine`, `Env`, `ApplyChange`, `BrowserInfo`, `Status`/`Manager`.
  - `versioning.py` — сортировка PEP440 / semver (pre-release перед релизом, невалидные → в конец).
  - `requirements.py` — парсинг/перезапись requirements.txt **с сохранением комментариев, пустых строк и порядка** (round-trip точный); операторы `~=`/`!=`/неверсионные → пинятся в `==`.
  - `pypi.py` / `npm.py` — клиенты PyPI JSON API / npm registry (httpx).
  - `envs.py` — детект окружений (venv, mineru_venv) + `pip list --json`.
  - `manager.py` — оркестрация: бэкап → перезапись манифеста → pip install → откат при ошибке; пин `@playwright/mcp` в `config/settings.yaml → deps.playwright_mcp.version`.
  - `worker.py` — QThread-воркеры (Check/Apply/Browser), UI не блокируется.
  - `dialog.py` — таблица (✓|Пакет|Менеджер|Текущая|Актуальная|Версия|Статус), выбор окружения, прогресс-бар, лог, «Проверить / Применить / Откатить».
- `mcp_bridge.py` читает пин версии (`@playwright/mcp@<ver>` или `@playwright/mcp`) — версия пакета больше не «что npx качнёт».
- **Фикс колонки «Версия»**: убран `QComboBox`-виджет (создавал доп. элемент, плохо размещался) → значение пишется **прямо в ячейку** (`QTableWidgetItem`), редактируется двойным кликом/F2; у остальных колонок снят `ItemIsEditable`; тултип показывает доступные версии.

### 2. Проверка и обновление браузера chromium
- Панель **«Chromium (MCP)»** в диалоге: ожидаемая ревизия из `playwright-core/browsers.json` активного `@playwright/mcp` (учитывает пин) vs установленная в `%LOCALAPPDATA%\ms-playwright` (папки `chromium-*`, флаг `INSTALLATION_COMPLETE`).
- Кнопка **«Обновить браузер»** → `npx -y @playwright/mcp[@{пин}] install-browser chromium` (фоновый воркер + лог).
- **Найден реальный дрейф**: на машине `@playwright/mcp` 0.0.79 актуален (npm-проверка не ловила проблему), но chromium ожидает ревизию **1237**, установлен **1223** — кнопка обновления это чинит.
- Новая функция: `mcp_package_dir`, `expected_browser_revisions`, `browsers_root`, `installed_browser_revisions` (npm.py), `browser_status()`/`update_browser()` (manager.py), `BrowserWorker` (worker.py).

### 3. Публикация на GitHub
- `git init -b main`, `.gitignore` (venv, кэши, runtime-данные, `.opencode`, `.playwright-mcp`, корневые бинарники), `.gitattributes` (LF), `AGENTS.md` (контекст разработки).
- Первый коммит `45daa3b7` «feat: initial release of Pricer Vision» (69 файлов, 17.5K строк).
- Репозиторий: **https://github.com/rkolpakov-sudo/Pricer_Vision** (private, ветка `main`), создан через OAuth device-flow + REST API (`repo` scope), remote `origin` → push `main`.

### Тесты
- `tests/test_dependency_manager.py`: 37 тестов (парсинг requirements, версии, browser-функциональность, пин, update_browser).
- Полный прогон: **141 passed**, 13 failed — предсуществующие (нет `pytest-asyncio` в venv, async-тесты mcp_bridge/pdf_parser).


## 2026-08-16 — Handoff: переход к новой сессии (старт крупного рефакторинга)

### Состояние проекта (базовая точка)
- Всё закоммичено и запушено: `main` = `origin/main` = `11a80f4`.
- Репозиторий: **https://github.com/rkolpakov-sudo/Pricer_Vision** (private, ветка `main`).
- `gh` авторизован (`rkolpakov-sudo`), работает: `gh repo view`, `gh pr create`, `gh issue` (scope `repo` — достаточно для личного репо; предупреждение `read:org` косметическое).
- Базовая метка-тег перед рефакторингом: `v0.1.0` (возврат: `git checkout v0.1.0`).

### Что готово к работе
- Инструмент «Зависимости» (`src/dependency_manager/`, кнопка «🧩 Зависимости» в toolbar): проверка pip/npm, пин `@playwright/mcp` в `config/settings.yaml`, проверка и обновление браузера chromium (кнопка «Обновить браузер»).
- Колонка «Версия» — значение прямо в ячейке (без cell-widget).
- 141 тест проходит; 13 падений — предсуществующие (нет `pytest-asyncio` в venv).

### Roadmap
- Рефакторинг ведётся по **отдельному roadmap-документу**, подготовленному пользователем (будет предоставлен в новой сессии). В рамках этой сессии рефакторинг не выполнялся.

### Соглашения для новой сессии
- Перед изменениями читать `readme.md`, `state.md`, `AGENTS.md`.
- Правки — на ветках `feat/...` от `main`, конвенциональные коммиты.
- После действий — обновлять `state.md`.
- Прогон тестов: `python -m pytest -q` (ожидаемо 13 падений async без pytest-asyncio).


## 2026-08-16 — Фаза 1: Стабильность ядра (Pydantic, StuckDetector, Circuit Breaker, Retry, Audit)

Реализована на ветке `phase/1-core` (от `refactor/v2.0`).

### 1.1 Pydantic-валидация
- Новый пакет `src/models/`:
  - `schemas.py` — `ActionType`, `AgentDecision`, `ExtractedPrice`, `ExtractionResult` (контракт `process_row`).
  - `ExtractionResult` использует `model_validator`: `found=True` требует `price`, цена должна быть `> 0` и `<= 10_000_000` (совпадает с `PRICE_ANOMALY_HIGH` в `validator.py`), `spec_text` не пустой.
  - `AgentDecision.validate_target` переведён с `field_validator` на `model_validator(mode="after")` — pydantic 2.13 **не запускает field_validator на значениях по умолчанию** (важная находка), поэтому `target`-проверка для CLICK/TYPE не срабатывала.
- `agent_loop.py`: добавлена `_result_to_schema(result) -> dict` — валидирует финальный результат через `ExtractionResult`, наружу отдаёт `model_dump()` (контракт runner/ExcelWriter сохранён). Подключена ко всем трём return-точкам результата (rule8 reuse, final_attempt, save_confirmed_price).
- `requirements.txt`: добавлен `pydantic>=2.0.0` (в venv уже был 2.13.4).

### 1.2 StuckDetector
- Новый `src/stuck_detector.py`: `StuckDetector` + `StuckLevel` (OK/WARNING/CRITICAL/BLOCKED), `ActionRecord`, `suggest_recovery`.
- Интеграция в `process_row`: создаётся `StuckDetector()`, после каждого MCP-шага `record_action(tool_name, target, success/no_change)`; после цикла tool_calls `detect()` — при CRITICAL и `rounds_on_site > 5` принудительный уход с сайта через существующую логику `site_round_limits`. BLOCKED не дублируется — обрабатывается существующей captcha-логикой (`CAPTCHA_KEYWORDS`).

### 1.3 Circuit Breaker
- Новый `src/resilience.py`: `CircuitBreaker`, `CircuitState` (CLOSED/OPEN/HALF_OPEN), `CircuitBreakerOpenError`, `MaxRetriesExceeded`, синглтоны `llm_circuit` (3/30s) и `mcp_circuit` (5/60s), методы `allow_request()`, `call()`, `call_async()`, `record_success/failure`, `reset()`.
- `mcp_bridge.py`: в `call_tool` — проверка `mcp_circuit.allow_request()`, при OPEN → restart; success/failure фиксируются по результату `session.call_tool`.
- `agent_loop.py`: добавлена `_query_llm()` — обёртка над `llm_client.chat` с `llm_circuit` (chat возвращает `{"error": ...}` вместо исключения, состояние фиксируется вручную). Все 4 вызова chat в `process_row` переведены на `_query_llm`.

### 1.4 Retry с exponential backoff
- `src/resilience.py`: `retry_with_backoff()` — работает и с sync и с async функциями (через `inspect.iscoroutinefunction`, без deprecated `asyncio.iscoroutinefunction`).
- `llm_client.py`: подключён `llm.retry` из `settings.yaml` (`max_attempts: 2`, `backoff_seconds: 1.0`) — повторные попытки перебора URL с экспоненциальной паузой.
- `config_loader.py`: добавлен `get_llm_retry_config()`.

### 1.5 Audit Logging
- Новый `src/audit_logger.py`: `AuditLogger` пишет JSONL в `data/audit/session_<id>.jsonl` (data/ уже в .gitignore), методы `log_llm_request`, `log_browser_action`, `log_extraction`, `get_session_summary`.
- `mcp_agent_runner.py`: создаётся `AuditLogger()` в `_run_async`, после каждого результата строки — `audit.log_extraction()`.

### Тесты
- Новые файлы: `tests/test_schemas.py`, `tests/test_stuck_detector.py`, `tests/test_resilience.py`, `tests/test_audit_logger.py`, дополнен `tests/test_agent_loop.py` (TestResultToSchema).
- **196 passed** (+55 новых), 13 failed — предсуществующие (нет `pytest-asyncio` в venv, async-тесты mcp_bridge/pdf_parser). Регрессий нет.

### Изменённые/новые файлы
- Новые: `src/models/__init__.py`, `src/models/schemas.py`, `src/stuck_detector.py`, `src/resilience.py`, `src/audit_logger.py`.
- Изменённые: `src/agent_loop.py`, `src/mcp_bridge.py`, `src/mcp_agent_runner.py`, `src/llm_client.py`, `src/config_loader.py`, `requirements.txt`, `tests/test_agent_loop.py`.


## 2026-08-16 — Handoff: переход к новой сессии (после Фазы 1)

### Состояние проекта (актуальная точка)
- Ветки: `main` (базовая), `refactor/v2.0` (от `main`), `phase/1-core` (**текущая**, Фаза 1 закоммичена).
- Теги: `v1.0-pre-refactor` (базовая точка до рефакторинга), `phase-1-done` (Фаза 1 завершена), `v0.1.0` (старый).
- Коммиты: `d9a9e7b` — Фаза 1; `a303207` — baseline; `b0151c7` — handoff baseline.
- Рабочее дерево чистое, всё закоммичено.
- Бэкап БД: `data/pricer_backup_20260816.db` (точка отката).

### Регламент (обязательно к соблюдению в новой сессии)
- **Коммит ТОЛЬКО после подтверждения пользователем** (прогон/тест/осмотр). По умолчанию коммиты и теги фаз не ставить без явного «да».
- Ветки фаз: `phase/N-*` от `refactor/v2.0`. Теги: `phase-N-done`.
- Откат: `git checkout main` / `git checkout v1.0-pre-refactor`; БД — из `data/pricer_backup_20260816.db`.
- Ветка новой фазы создаётся от `phase/1-core` или `refactor/v2.0` (в зависимости от того, замержим ли Фазу 1 в `refactor/v2.0`).
- **Прогоны товаров не выполняются** (решение пользователя) — критерий «25 товаров без крэша» пропущен, завершение фазы оцениваем по тестам и коду.

### Фаза 1 — итог
- 5/5 задач: Pydantic-валидация, StuckDetector, Circuit Breaker (MCP+LLM), Retry с backoff, Audit Logger.
- **196 passed** (+55 новых тестов), 13 failed — предсуществующие (нет `pytest-asyncio` в venv, async-тесты mcp_bridge/pdf_parser). Регрессий нет.
- Важная находка: pydantic 2.13 не запускает `field_validator` на значениях по умолчанию → `model_validator(mode="after")`.

### Следующий шаг (Фаза 2)
- **Фаза 2: Оптимизация агентного цикла под локальную LLM** (см. `chat-Pricer_Vision Project Analysis.md`, строки ~679+): минимизация запросов к LLM и объёма контекста, скорость +33%.
- Рекомендация: сначала замержить `phase/1-core` → `refactor/v2.0` (или продолжить ветвление от неё), затем ветка `phase/2-*`.


## 2026-08-16 — Фаза 2: Оптимизация агентного цикла под локальную LLM

Реализована на ветке `phase/2-llm` (от `phase/1-core`).

### 2.1 TaskScheduler (`src/task_scheduler.py` — новый)
- `TaskScheduler` + `ProcessingBatch`: группировка товаров по целевым сайтам для минимизации переключений контекста браузера.
- `_determine_target_site()`: `classify_product_type(spec_text)` → `mm.get_sites(product_type)` → сайт с лучшим `priority − consecutive_failures*0.5`; fallback `yandex.ru`.
- `_get_site_profile()`: success_rate из approaches (`success_count`/`failures_count`), has_antibot=False, speed_score=0.5.
- `_calculate_priority()`: success_rate*0.4 + работа*0.3 + простота*0.2 + скорость*0.1.
- Интеграция в `mcp_agent_runner.py`: `ordered_specs()` перед циклом; исходный индекс строки сохраняется через `{id(spec): i}` → `row_done_signal.emit(original_idx, result)` (GUI не путает порядок строк).

### 2.2 SemanticCache (`src/semantic_cache.py` — новый)
- Без embedding: нормализация (убирает скобки/размеры) + Jaccard-схожесть по словам, hash-md5 ключ, JSON в `data/semantic_cache.json`.
- Лимит 1000 записей, evict 20% старейших.
- Интеграция в `process_row`: проверка после rule-8 (только `not fresh`, confidence > 0.8); `_store_semantic_cache()` в обеих точках возврата цены.
- `fresh=True` кэш не читается (не переиспользуются чужие цены), но пишется.

### 2.3 AdaptiveRoundManager (`src/adaptive_limits.py` — новый)
- `calculate_limit(site_profile, product_complexity)`: BASE=10, MIN=5, MAX=30; сложность = f(success_rate, consecutive_failures, has_antibot).
- `per_site_limits(sites)` — надстройка над существующим `site_round_limits` (failures>=3 → MIN, иначе base). `should_extend(progress)` — есть прогресс → продлить.
- Интеграция в `process_row`: `AdaptiveRoundManager(base_rounds=MAX_ROUNDS_PER_SITE)` вместо ручного цикла.

### 2.4 Температура по фазам
- `LLMClient.chat()` расширен: необязательные `temperature`/`max_tokens` (обратно совместимо; defaults — конструктор/8192).
- `agent_loop.py`: константы `TEMP_EXPLORATION=0.7`, `TEMP_NAVIGATION=0.3`, `TEMP_EXTRACTION=0.1`, `TEMP_RECOVERY=0.5`.
- `_query_llm(..., temperature=...)` — применяется в 4 вызовах: первый (exploration), force-JSON (extraction), force-switch (recovery), основной цикл (navigation).

### 2.5 Контекстный бюджет
- `_estimate_tokens()` (≈len/4), `_trim_messages_for_budget()` (бюджет 8000 токенов): сохраняет system + хвост от последнего user-сообщения, усекает старые tool/assistant.
- Вызывается в `_query_llm()` перед каждым LLM-запросом.

### Тесты
- Новые: `tests/test_task_scheduler.py` (9), `tests/test_semantic_cache.py` (12), `tests/test_adaptive_limits.py` (10); расширены `test_agent_loop.py` (+7: температуры, контекстный бюджет), `test_llm_client.py` (+2: per-call temperature/max_tokens).
- **247 passed** (было 209, +38 новых). Регрессий нет.

### Изменённые/новые файлы
- Новые: `src/task_scheduler.py`, `src/semantic_cache.py`, `src/adaptive_limits.py`.
- Изменённые: `src/agent_loop.py`, `src/llm_client.py`, `src/mcp_agent_runner.py`, `tests/test_agent_loop.py`, `tests/test_llm_client.py`.


