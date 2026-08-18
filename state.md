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


## 2026-08-16 — Handoff: переход к новой сессии (после Фазы 2)

### Состояние проекта (актуальная точка)
- Ветки: `main` (базовая), `refactor/v2.0` (от `main`), `phase/1-core` (Фаза 1), `phase/2-llm` (**текущая**, Фаза 2 закоммичена).
- Теги: `v1.0-pre-refactor` (базовая точка до рефакторинга), `phase-1-done`, `v0.1.0` (старый). `phase-2-done` НЕ установлен (ждёт подтверждения).
- Коммиты: `4287129` — Фаза 2; `d9a9e7b` — Фаза 1; `a303207` — baseline; `b0151c7` — handoff baseline.
- Рабочее дерево чистое, всё закоммичено.
- Бэкап БД: `data/pricer_backup_20260816.db` (точка отката).

### Регламент (обязательно к соблюдению в новой сессии)
- **Коммит и тег фаз ТОЛЬКО после подтверждения пользователем.** По умолчанию коммиты и теги фаз не ставить без явного «да».
- Ветки фаз: `phase/N-*` от `refactor/v2.0` (или от последней завершённой фазы). Теги: `phase-N-done`.
- Откат: `git checkout main` / `git checkout v1.0-pre-refactor`; БД — из `data/pricer_backup_20260816.db`.
- **Прогоны товаров не выполняются** — фаза считается завершённой по тестам (`python -m pytest -q`) и ревью кода.
- Перед изменениями читать `readme.md`, `state.md`, `AGENTS.md`.
- После действий — обновлять `state.md`.

### Фаза 2 — итог
- 5/5 задач: TaskScheduler (группировка по сайтам), SemanticCache, AdaptiveRoundManager, температура по фазам, контекстный бюджет 8000 токенов.
- **247 passed** (+38 новых), 0 failed. Регрессий нет.
- Коммит `4287129` на `phase/2-llm`. Следующий шаг: пользователь проверяет работу → подтверждение → тег `phase-2-done` → слияние в `refactor/v2.0`.

### Следующий шаг (Фаза 3)
- **Фаза 3: Антидетект и браузерная автоматизация** (см. `chat-Pricer_Vision Project Analysis.md`, строки ~1060+): расширенный stealth.js (патчи 13–17), имитация человеческого поведения, работа с captcha, ротация профилей браузера.
- Ветка: `phase/3-antidetect` (от `phase/2-llm` или `refactor/v2.0` после слияния).


## 2026-08-16 — Фаза 3: Антидетект и браузерная автоматизация

Реализована на ветке `phase/3-antidetect` (от `refactor/v2.0`).

### 3.1 stealth.js — патчи 13–17
- `config/stealth.js` расширен с 12 до 17 патчей (добавлены в конец, не перезаписывая):
  - 13 — Canvas Fingerprint Randomization (шум ±3 на toDataURL/toBlob через getImageData)
  - 14 — AudioContext (шум в getFloatFrequencyData анализатора)
  - 15 — WebRTC Leak Prevention (фильтр srflx/host кандидатов в addIceCandidate)
  - 16 — Font Enumeration (ограничение доступных шрифтов до 6)
  - 17 — WebGL Vendor/Renderer Masking (0x9245/0x9246 → Intel UHD 620)
- `node --check` проходит.

### 3.2 HumanBehavior (`src/human_behavior.py` — новый)
- `human_click` — случайная точка внутри элемента (не центр) + эмуляция mousemove через `browser_evaluate` (`browser_mouse_move` не существует в @playwright/mcp), fallback на обычный клик при ошибке bbox.
- `human_type` — посимвольная печать с переменной скоростью + «раздумья».
- `human_scroll` — скролл рывками (3–7 шагов) + финальная пауза.
- `random_pause`, `get_random_viewport`.

### 3.3 DomainRateLimiter (`src/rate_limiter.py` — новый)
- Per-domain min_interval (1.5s) + RPM-лимит (20/мин), окно 60s.
- `wait_if_needed(url)` вызывается перед `browser_navigate` в `agent_loop.py`.
- Настройки: `config/settings.yaml → antidetect.rate_limit_min_interval/rate_limit_max_requests_per_minute`, getter `get_antidetect_config` в `config_loader.py`.

### 3.4 SiteAnalyzer (`src/site_analyzer.py` — новый)
- Определяет SPA (window.__NUXT__/__NEXT_DATA__/ng-version/data-reactroot/__vue__/root/app), антибот (cloudflare/recaptcha/hcaptcha/datadome/perimeterx/ddos-guard...), DOM-статистику.
- ⚠️ Корректировка: глобальные индикаторы проверяются через `typeof`, а НЕ через `document.querySelector` (window.__NUXT__ — невалидный CSS-селектор).
- Профиль кэшируется в памяти по домену (`self.profiles`), стратегия: CAUTIOUS/SPA_AWARE/STANDARD.

### 3.5 CaptchaDetector (`src/captcha_detector.py` — новый)
- Типы: NONE/RECAPTCHA_V2/V3/HCAPTCHA/CLOUDFLARE/IMAGE/UNKNOWN, рекомендации (SWITCH_SITE/WAIT_60S_AND_RETRY/ASK_USER...).
- ⚠️ Корректировка: CSS-селекторы не встречаются дословно в HTML-тексте — детекция по характерным подстрокам (`g-recaptcha`, `cf-turnstile`, `captcha.png`...).
- Без авторешения — только детект + рекомендация (по ТЗ v2.0).
- Интеграция в `agent_loop.py`: captcha-ветка теперь логирует тип + рекомендацию и сообщает их LLM.

### Тесты
- Новые: `tests/test_captcha_detector.py` (12), `tests/test_rate_limiter.py` (6), `tests/test_human_behavior.py` (6), `tests/test_site_analyzer.py` (10).
- **281 passed** (было 247, +34 новых). Регрессий нет. `node --check config/stealth.js` — OK.

### Изменённые/новые файлы
- Новые: `src/human_behavior.py`, `src/rate_limiter.py`, `src/site_analyzer.py`, `src/captcha_detector.py`.
- Изменённые: `config/stealth.js`, `src/agent_loop.py`, `src/config_loader.py`, `config/settings.yaml`.


## 2026-08-16 — Handoff: переход к новой сессии (после Фазы 3)

### Состояние проекта (актуальная точка)
- Ветки: `main` (базовая), `refactor/v2.0` (Фазы 1+2 слиты), `phase/1-core`, `phase/2-llm`, `phase/3-antidetect` (**текущая**, Фаза 3 закоммичена).
- Теги: `v1.0-pre-refactor`, `phase-1-done`, `phase-2-done`, `v0.1.0` (старый). `phase-3-done` НЕ установлен (ждёт подтверждения).
- Коммиты Фазы 3 (на `phase/3-antidetect`): от `41a45c1` (merge Фаз 1+2 в `refactor/v2.0`).
- Рабочее дерево чистое, всё закоммичено.
- Бэкап БД: `data/pricer_backup_20260816.db` (точка отката).

### Регламент (обязательно к соблюдению в новой сессии)
- **Коммит и тег фаз ТОЛЬКО после подтверждения пользователем.** По умолчанию коммиты и теги фаз не ставить без явного «да».
- Ветки фаз: `phase/N-*` от `refactor/v2.0` (или от последней завершённой фазы). Теги: `phase-N-done`.
- Откат: `git checkout main` / `git checkout v1.0-pre-refactor`; БД — из `data/pricer_backup_20260816.db`.
- **Прогоны товаров не выполняются** — фаза считается завершённой по тестам (`python -m pytest -q`) и ревью кода.
- Перед изменениями читать `readme.md`, `state.md`, `AGENTS.md`.
- После действий — обновлять `state.md`.

### Фаза 3 — итог
- 5/5 задач: stealth.js 17 патчей, HumanBehavior, DomainRateLimiter, SiteAnalyzer, CaptchaDetector.
- **281 passed** (+34 новых), 0 failed. `node --check config/stealth.js` — OK.
- Следующий шаг: пользователь проверяет работу → подтверждение → тег `phase-3-done` → слияние в `refactor/v2.0`.

### Следующий шаг (Фаза 4)
- **Фаза 4: Эволюция графа знаний** (см. `chat-Pricer_Vision Project Analysis.md`, строки ~1560+): версионирование подходов, effectiveness scoring, TTL/депрекация знаний, пере-валидация устаревших подходов.
- ⚠️ Из «Сверки»: таблиц `revalidation_queue` и полей `success_rate`/`status`/`total_successes` в БД НЕТ — эффективность считать на лету из `success_count/(success_count+failures_count)`; `get_effective_approaches` использовать через существующий `memory_manager.get_approaches_by_site`.
- Ветка: `phase/4-graph` (от `phase/3-antidetect` или `refactor/v2.0` после слияния).


## 2026-08-16 — Фаза 4: Эволюция графа знаний

Реализована на ветке `phase/4-graph` (от `phase/3-antidetect`).

### 4.1 Версионирование подходов и effectiveness scoring
- Новые классы в `src/memory_manager.py`:
  - `ApproachVersioning.update_effectiveness(approach_id, success)` — делегирует в существующие `update_approach_success/failure` (без дублирования счётчиков).
  - `ApproachVersioning.get_effective_approaches(site_id, limit=5)` — сортировка активных подходов по `score = success_rate*0.7 + freshness*0.3` (депрекейтнутые ×0.5); `success_rate` считается на лету (`success_count/(success_count+failures_count)`, колонки в БД нет) и добавляется в каждый подход.

### 4.2 TTL для хинтов
- `graph_engine.py`: колонка `hints.expires_at` в SCHEMA_SQL + миграция `ALTER TABLE hints ADD COLUMN expires_at` в `_init_db` (как для `consecutive_failures`).
- `save_hint(..., expires_at=None)` — сохраняет TTL; кэш `_hints_by_product` включает `expires_at` (подхватывается через `SELECT *` в `_load_indexes`).
- Новый `graph_engine.delete_expired_hints()` → `int` (число удалённых), инвалидирует кэш (`_built = False`).
- Новый `HintManager` в `memory_manager.py`: `create_hint(..., ttl_days=90)`, `get_active_hints(product_type, site=None)` (фильтр просроченных + опционально по сайту), `cleanup_expired()`.

### 4.3 LearningLoop
- Новый `src/learning_loop.py`: `LearningLoop(graph_engine, memory_manager)`.
  - `consolidate_after_run(results)` — вызывается из `MCPAgentRunner._run_async` после цикла строк (до `done_signal`); возвращает `{approaches_updated, new_patterns, new_hints}`.
  - `_update_approach_effectiveness` — агрегация (success/failure уже фиксируются внутри `process_row`, повторный вызов задвоил бы счётчики; срабатывает только если в результате есть `approach_id`).
  - `_extract_patterns` — сохраняет подход только при наличии реальных `selectors` в результате (в текущем пайплайне их нет → no-op; иначе создавались бы «search-only» подходы-мусор).
  - `_generate_hints` — TTL-хинт (priority 0.3) для успешных поисков дольше 60s; дедупликация по фрагменту спецификации в тексте.
  - `_update_site_profiles` — агрегация `success_rate`/`avg_attempts`/`block_count`/`total_runs` по фактическим результатам; персист в `data/site_profiles.json`.
  - `_save_run_statistics` — `last_run_stats` + лог.
- `TaskScheduler.__init__(mm, site_profiles=None)` + `_get_site_profile` отдаёт приоритет профилю LearningLoop (успех прошлых прогонов) над расчётом по подходам. В `MCPAgentRunner` профили подмешиваются в планировщик следующего прогона.

### 4.4 Оптимизация SQLite
- `graph_engine._apply_pragmas()` вызывается в `build()` (после WAL/foreign_keys): `synchronous=NORMAL`, `cache_size=-64000` (64MB), `temp_store=MEMORY`. WAL уже был включён.

### Конфиг
- `config/settings.yaml → learning`: `hint_ttl_days: 90`, `site_profiles_path: data/site_profiles.json`.
- `config_loader.get_learning_config(key, default)`.

### Тесты
- Новые: `tests/test_learning_loop.py` (13), `test_graph_engine.py` +3 (expires_at, delete_expired, pragmas), `test_memory_manager.py` +9 (HintManager 5, ApproachVersioning 4).
- **293 passed** (было 268, +25 новых), 13 failed — предсуществующие (нет `pytest-asyncio` в venv, async-тесты mcp_bridge/pdf_parser). Регрессий нет.

### Изменённые/новые файлы
- Новые: `src/learning_loop.py`, `tests/test_learning_loop.py`.
- Изменённые: `src/graph_engine.py`, `src/memory_manager.py`, `src/mcp_agent_runner.py`, `src/task_scheduler.py`, `src/config_loader.py`, `config/settings.yaml`, `tests/test_graph_engine.py`, `tests/test_memory_manager.py`, `readme.md`.

### Следующий шаг
- Пользователь проверяет → подтверждение → тег `phase-4-done` → слияние `phase/4-graph` в `refactor/v2.0`.
- Фаза 5: Модернизация PDF-парсера (LLM structurer как опция, OCR-fallback через MinerU-режим) — см. аналитику, строки ~1854+.


## 2026-08-16 — Фаза 4 подтверждена: теги и слияние

- Пользователь подтвердил Фазу 4.
- Теги: `phase-3-done` → `b7bda76`, `phase-4-done` → `cd5417e`.
- `refactor/v2.0` = `ee1a5fc` (merge `phase/4-graph`, --no-ff) — вобрал Фазы 1–4 целиком.
- Рабочее дерево чистое, всё закоммичено.
- **293 passed, 13 failed** — те же предсуществующие async-падения (нет `pytest-asyncio`). Регрессий нет.
- Ветка по умолчанию для следующей сессии: `refactor/v2.0`.

### Следующий шаг (Фаза 5)
- **Фаза 5: Модернизация PDF-парсера** (см. `chat-Pricer_Vision Project Analysis.md`, строки ~1854+): Lightweight LLM Structurer как ОПЦИЯ (конфиг `pdf_parser.use_llm`, fallback остаётся), OCR-fallback через MinerU-режим (без новых зависимостей), покрытие форматов 60% → 95%.
- Ветка: `phase/5-pdf` (от `refactor/v2.0`).


## 2026-08-16 — Фаза 5: Модернизация PDF-парсера

Реализована на ветке `phase/5-pdf` (от `refactor/v2.0`).

### 5.1 Lightweight LLM Structurer (опция)
- `src/pdf_parser/structurer.py` — `SpecStructurer` получил параметры `use_llm`, `max_chars`, `max_tokens`, `temperature`.
- `structure()`: если `use_llm=True` → сначала LLM-ветка `_llm_structure()`, при неудаче/пустом результате — автоматический fallback на существующий `_fallback_parse`.
- `_extract_llm_content()` — извлекает текст из ответа `LLMClient.chat()` (OpenAI envelope `choices[0].message.content` ИЛИ `{"content": ...}`), пусто при `{"error": ...}`.
- `_safe_parse_items()` — вырезает JSON-массив из ответа, `_normalize_item()` приводит к контракту `{pos, name, specs, code, manufacturer, qty, unit, weight}` (qty/weight → float, pos → int).
- Важно: `chat()` реально принимает per-call `temperature`/`max_tokens` (в аналитике было «не принимает» — сверка устарела). LLM-ветка — это обычная малая модель, reasoning не требуется (промпт явно запрещает).
- Конфиг: `pdf_parser.use_llm: false` (по умолчанию выключено), `llm_max_chars: 3000`, `llm_max_tokens: 1024`, `llm_temperature: 0.0`.

### 5.2 OCR Fallback для сканированных PDF
- Новый `src/pdf_parser/ocr_fallback.py` — `OCRFallback`:
  - `needs_ocr(text)` — True, если извлечено меньше `MIN_TEXT_LENGTH` (100) символов.
  - `extract_with_ocr(pdf_path, timeout)` — повторный запуск MinerU через `asyncio.to_thread` (не блокирует event loop).
  - Реальный бэкенд — `MinerUBackend` (mineru_venv), НЕ PaddleOCR/Tesseract — новых зависимостей нет.
- В `runner.py`: после первичного парсинга MinerU, если текст короткий (`ocr_min_text_length`) → повторный парсинг с OCR, сигнал прогресса.

### 5.3 Smart Review с Confidence Scoring
- Новый `src/pdf_parser/review.py` — `SmartReview`:
  - `process_extraction(items)` → `(auto_approved, needs_review)`, добавляет `row["confidence"]` (0..1).
  - `_calculate_confidence()`: name=0.4, qty>0=0.2, unit=0.1, code|manufacturer=0.2, specs=0.1, cap 1.0. Порог `CONFIDENCE_THRESHOLD=0.8` (настраиваемый `threshold`).
  - Цена в контракте отсутствует — не участвует в скорринге (по аналитике).
- В `runner.py`: после `feedback.apply_corrections` → SmartReview, лог `N auto-approved, M need review`.
- В `review_dialog.py`: колонка «Уверенность» (%), авто-подтверждённые — обычные, низкая уверенность (<0.8) — янтарная подсветка строк; заголовок показывает «Авто-подтверждено: N, требует проверки: M».
- В `main.py`: лог SmartReview при получении позиций.

### Конфиг
- `config/settings.yaml → pdf_parser`: добавлены `use_llm`, `llm_max_chars`, `llm_max_tokens`, `llm_temperature`, `ocr_min_text_length: 100`, `review_threshold: 0.8`.
- `config_loader.py`: добавлен `get_pdf_config(key, default)`.

### Тесты
- `tests/test_pdf_parser.py`: +35 новых (TestExtractLlmContent 5, TestSpecStructurerLlm 6, TestOCRFallback 5, TestSmartReview 5, TestConfigLoaderPdf 1, плюс существующие).
- `_extract_llm_content`, LLM-структура с мок-ответом, fallback при `{"error"}` и не-JSON, нормализация, needs_ocr/extract_with_ocr, confidence-скорринг/сплит, порог, чтение конфига.
- **328 passed** (было 293, +35 новых), 0 failed. Регрессий нет.
- `python -m py_compile` по всем изменённым файлам — OK.

### Изменённые/новые файлы
- Новые: `src/pdf_parser/ocr_fallback.py`, `src/pdf_parser/review.py`.
- Изменённые: `src/pdf_parser/structurer.py`, `src/pdf_parser/runner.py`, `src/pdf_parser/review_dialog.py`, `src/pdf_parser/__init__.py`, `src/config_loader.py`, `config/settings.yaml`, `main.py`, `tests/test_pdf_parser.py`, `readme.md` (см. ниже), `state.md`.

### Следующий шаг
- Пользователь проверяет → подтверждение → тег `phase-5-done` → слияние `phase/5-pdf` в `refactor/v2.0`.
- Фаза 6: GUI и мониторинг (real-time монитор агента) — см. аналитику, строки ~2052+.


## 2026-08-16 — Фаза 5 подтверждена: теги и слияние

- Пользователь подтвердил Фазу 5.
- Тег: `phase-5-done` → `7d9c41c`.
- `refactor/v2.0` = `9be309f` (merge `phase/5-pdf`, --no-ff) — вобрал Фазу 5 целиком.
- Рабочее дерево чистое, всё закоммичено.
- **328 passed, 0 failed** — полный зелёный прогон после слияния.
- Ветка по умолчанию для следующей сессии: `refactor/v2.0`.

### Следующий шаг (Фаза 6)
- **Фаза 6: GUI и мониторинг** (см. `chat-Pricer_Vision Project Analysis.md`, строки ~2052+): real-time мониторинг агента, оптимизация графа, улучшение UX.
- Ветка: `phase/6-gui` (от `refactor/v2.0`).


## 2026-08-16 — Фаза 6: GUI и мониторинг

Реализована на ветке `phase/6-gui` (от `refactor/v2.0`).

### 6.1 AgentMonitorPanel (`gui/agent_monitor.py` — новый)
- Вкладка **«Мониторинг»** в правой панели (`main.py`).
- `AgentMonitorPanel` — real-time действия агента: текущее действие, прогресс по строкам, история действий (`QListWidget`, cap 500), кнопка «Очистить».
- `handle_event(event)` обрабатывает события: `start`/`row`/`action`/`row_done`/`done`/`stop`.
- Источник — новый `monitor_signal` в `MCPAgentRunner` + `status_callback` из `agent_loop.py`.

### 6.2 Оптимизация графа (`gui/graph_explorer.py`)
- **Фикс бага `_update_edges`**: операторная предшественность `u == idx or v == idx and ...` (Python: `and` приоритетнее `or`) давала IndexError при `u==idx` и `v` вне границ. Вынесен чистый предикат `_edge_touches(idx, u, v, n)`.
- **LOD**: `_lod_decision(node_count)` — при > 500 нод (`LOD_THRESHOLD`) подписи и физика отключаются, граф рендерится статично + авто-фит. Лимит нод поднят `MAX=250` → `MAX_GRAPH_NODES=1000`. `GraphScene.build(nodes, edges, labels=False)` не создаёт QGraphicsTextItem-ы вообще (LOD-режим).
- **Троттлинг физики**: `_on_physics_update` ограничен `PHYSICS_SYNC_INTERVAL` (~30fps); `sync_all(update_labels=False)` во время физики — подписи пересчитываются только при стабилизации.
- **Рефактор**: шаг физики вынесен в чистую функцию `_physics_step(nodes, edges, alpha)` — тестируется без Qt.

### 6.3 MetricsPanel (`gui/metrics_panel.py` — новый)
- 9 метрик: всего/обработано/найдено/успешность/LLM-запросы/ср. время LLM/кэш-хиты/застревания/блокировки.
- Новый `metrics_signal` в `MCPAgentRunner`, агрегация в чистой `_build_metrics()` (после каждой строки и по завершении).
- Форматирование — чистая `format_metric_value(key, value)`.

### Прокидывание метрик из agent_loop
- `process_row(..., monitor_callback)` — опциональный колбэк: `("llm_call", elapsed)`, `("cache_hit", similarity)`, `("stuck", None)`, `("block", captcha_type)`.
- `_query_llm(..., monitor_callback)` — замер времени LLM-вызова (`time.monotonic`).
- **Скриншоты в монитор НЕ выведены** — доп. MCP round-trip на каждый шаг + риск антибот-детекции (по заверению, критерии фазы этого не требуют).

### Тесты
- Новые: `tests/test_graph_lod.py` (15), `tests/test_metrics_panel.py` (8), `tests/test_agent_monitor.py` (9, Qt-виджеты через `QApplication.instance()`).
- **Критерий «GUI не тормозит при 1000+ нодах»**: `_physics_step` с 1000 нодами < 2s; `GraphScene.build` 1000 нод без подписей < 5s; LOD включается при >500.
- **360 passed** (было 328, +32 новых), 0 failed. Регрессий нет.

### Изменённые/новые файлы
- Новые: `gui/agent_monitor.py`, `gui/metrics_panel.py`, `tests/test_graph_lod.py`, `tests/test_metrics_panel.py`, `tests/test_agent_monitor.py`.
- Изменённые: `gui/graph_explorer.py`, `main.py`, `src/agent_loop.py`, `src/mcp_agent_runner.py`, `readme.md`, `state.md`.

### Следующий шаг
- Пользователь проверяет → подтверждение → тег `phase-6-done` → слияние `phase/6-gui` в `refactor/v2.0`.
- **Фаза 7: Тестирование и документация** (см. аналитику, строки ~2242+): юнит-тесты критичных модулей, интеграционные тесты `process_row`, обновление `SPEC_V32.md`.


## 2026-08-16 — Фаза 6 подтверждена: теги и слияние

- Пользователь подтвердил Фазу 6.
- Тег: `phase-6-done` → `23e373c`.
- `refactor/v2.0` = `2f37a6d` (merge `phase/6-gui`, --no-ff) — вобрал Фазу 6 целиком.
- Рабочее дерево чистое, всё закоммичено.
- **360 passed, 0 failed** — полный зелёный прогон после слияния.
- Ветка по умолчанию для следующей сессии: `refactor/v2.0`.

### Следующий шаг (Фаза 7)
- **Фаза 7: Тестирование и документация** (см. `chat-Pricer_Vision Project Analysis.md`, строки ~2242+): юнит-тесты для критичных модулей (test_schemas, test_stuck_detector, test_semantic_cache, test_context_optimizer, test_rate_limiter, test_learning_loop, test_smart_review), интеграционные тесты `process_row` (моки LLM/MCP), обновление `SPEC_V32.md`.
- Ветка: `phase/7-testing` (от `refactor/v2.0`).


## 2026-08-16 — Фаза 7: Тесты и документация

Реализована на ветке `phase/7-testing` (от `refactor/v2.0`).

### 7.1 Юнит-тесты критичных модулей
- **Новый `tests/test_config_loader.py`** (14 тестов) — все геттеры и save-функции конфига; реальный `settings.yaml` НЕ трогается (monkeypatch пути через `os.path.dirname` + `_SETTINGS_CACHE`). `config_loader` покрытие 57% → 100%.
- **Новый `tests/test_context_optimizer.py`** (16 тестов) — `_estimate_tokens`/`_message_size`/`_trim_messages_for_budget` (выделено из test_agent_loop в отдельный файл по структуре Фазы 7).
- **Новый `tests/test_smart_review.py`** (13 тестов) — edge cases SmartReview: порог 0.8, сброс между вызовами, пустой вход, строковый/отрицательный qty, cap 1.0.
- **Новый `tests/test_excel_writer.py`** (22 теста) — Qt-free (openpyxl): load_spec, detect_columns, build_item_name, get_specs, write_result, save_output_copy. `excel_writer` покрытие 0% → 97%.
- Существующие критичные модули уже покрыты: schemas (96%), stuck_detector (100%), semantic_cache (95%), rate_limiter (100%), learning_loop (89%).

### 7.2 Интеграционные тесты `process_row`
- **Новый `tests/integration/test_agent_flow.py`** (9 тестов, `@pytest.mark.asyncio`) — полный цикл с моками (FakeLLM/FakeBridge/FakeEngine/FakeMemoryManager):
  - Полное извлечение (LLM сразу даёт цену)
  - Tool_call цикл (navigate → финальная цена)
  - Reuse rule 8 (fresh=False, confidence ≥ 0.9 → без LLM)
  - Semantic cache hit (без LLM)
  - Ошибка LLM → error-result
  - Max rounds (monkeypatch MAX_ROUNDS=3)
  - **Captcha → событие `block`** в monitor_callback
  - **Stuck recovery** → CRITICAL → принудительный уход с сайта → событие `stuck`
  - Контракт `_error_result`

### 7.3 Документация
- **Новый `SPEC_V32.md`** — спецификация v2.0 (пост-рефакторинг): архитектурные решения фаз 1–7, контракт `process_row`, монитор-события, карта фаз, тестирование, метрики.
- `readme.md` — раздел «Тестирование (Фаза 7)», SPEC_V32 в структуре, tests/integration.

### Исправления, найденные тестами
- **`src/pdf_parser/review.py`**: `qty` строкой ("10") падал в `qty > 0` → TypeError. Вынесен `_positive_qty(value)` — принимает числовые строки, игнорирует невалидные.
- **`src/excel_writer.py`**: `detect_columns` fallback добавлял `None`-заголовки в name-колонки (мусор в имени товара). Fallback теперь исключает пустые/None заголовки.

### Окружение
- В `venv/` установлены: `coverage` (7.15.4), `pytest-asyncio` (были 19 async-падений из-за отсутствия плагина — теперь 0).

### Покрытие
- TOTAL src: 43% → **52%** (Qt-тяжёлые модули dialog/worker/study_runner/toast/widget_base остаются 0% — не входят в критичные).
- Критичные модули все >80% (см. 7.1 + 7.2).

### Тесты
- **434 passed, 0 failed** (было 360, +74 новых: 9 integration + 14 config + 16 context + 13 smart_review + 22 excel_writer).

### Изменённые/новые файлы
- Новые: `SPEC_V32.md`, `tests/test_config_loader.py`, `tests/test_context_optimizer.py`, `tests/test_smart_review.py`, `tests/test_excel_writer.py`, `tests/integration/__init__.py`, `tests/integration/test_agent_flow.py`.
- Изменённые: `src/pdf_parser/review.py`, `src/excel_writer.py`, `readme.md`, `state.md`.

### Следующий шаг
- Пользователь проверяет → подтверждение → тег `phase-7-done` → слияние `phase/7-testing` в `refactor/v2.0`.
- После Фазы 7 рефакторинг v2.0 завершён (все 7 фаз); дальше — приёмка по метрикам (прогоны 25 товаров по решению пользователя).


## 2026-08-16 — Фаза 7 подтверждена: теги и слияние (рефакторинг v2.0 завершён)

- Пользователь подтвердил Фазу 7.
- Тег: `phase-7-done` → `34b7b57`.
- `refactor/v2.0` = `af19762` (merge `phase/7-testing`, --no-ff) — вобрал Фазу 7 целиком.
- Рабочее дерево чистое, всё закоммичено.
- **434 passed, 0 failed** — полный зелёный прогон после слияния.
- **Рефакторинг v2.0 завершён: все 7 фаз слиты в `refactor/v2.0`** (теги `phase-1-done`…`phase-7-done`).

### Итоговое состояние v2.0
- Фазы 1–7: стабильность ядра, оптимизация LLM-цикла, антидетект, граф знаний, PDF-парсер, GUI/мониторинг, тесты/документация.
- Спецификация: `SPEC_V32.md` (новый).
- Тесты: **434** (в т.ч. 9 интеграционных `process_row`), критичные модули >80% покрытия.
- Покрытие src: 52% (Qt-тяжёлые GUI-модули 0% — не критичны).

### Дальнейшие шаги (по решению пользователя)
- Приёмка по метрикам: прогон 25 товаров (точность ≥ 92%, ≤ 20 мин, 0 крэшей).
- Ветка по умолчанию для следующей сессии: `refactor/v2.0`.


## 2026-08-16 — CRITICAL FIX: PDF-парсер зависал навсегда (реальный запуск)

### Симптом
Загрузка PDF → «Парсинг PDF через MinerU...» → прогрессбар не двигался >10 минут, процесс не останавливался, результат не появлялся.

### Root Cause (две проблемы)
1. **`subprocess.run(timeout=300)` на Windows не срабатывает**: MinerU 3.4.0 поднимает временный FastAPI-сервис + multiprocessing-воркеров (`--help`: "mineru starts a temporary local mineru-api service"). `subprocess.run` при таймауте убивает только прямой дочерний процесс (mineru.exe), а **внуки держат stdout/stderr пайпы** → `communicate()` блокируется навсегда → QThread runner висит бесконечно. Воспроизведено: PID 23448 жег 4630 CPU-сек, temp-папка пустая.
2. **Прогресс не отображался**: MinerU пишет прогресс в stderr (tqdm), но он захватывался и не показывался — пользователь не видел, что процесс жив.

### Диагностика
- 58-страничный скан PDF, CPU-only (нет GPU), `-m auto`: Layout 58/58, MFR 126/126, Table-ocr — это НЕ зависание, а медленная CPU-обработка (>10 мин). Но без прогресса и с нерабочим таймаутом выглядело как мёртвый процесс.
- Путь PDF содержит кириллицу — НЕ причина (проверено на ASCII-копии, MinerU работал).

### Fix (`src/pdf_parser/mineru_backend.py`, `ocr_fallback.py`, `runner.py`, `main.py`, `config/settings.yaml`)
1. **`MinerUBackend.parse_async()`** — `asyncio.create_subprocess_exec` + параллельное чтение stdout/stderr (`_pump`, иначе переполнение пайпа) + `asyncio.wait_for` с таймаутом.
2. **`_kill_tree(proc)`** — при таймауте/отмене убивает ВСЁ дерево (`taskkill /PID <pid> /T /F` на Windows, `killpg` на POSIX). Старый `parse()` переведён на `Popen`+`communicate(timeout)` с тем же kill дерева.
3. **Живой прогресс**: `_STAGE_RE` парсит `Stage: NN%` из stderr → `progress_callback(stage, percent)` → `progress_signal` → прогрессбар показывает `MinerU: Layout Predict 66%`.
4. **`runner.py`**: `_run_parse()` — поллит `_stop_event` каждые 0.3с, при Стоп отменяет задачу (→ kill дерева) и эмитит «Остановлено пользователем». OCR fallback переведён на `extract_with_ocr_async`.
5. **`main.py`**: кнопка «Стоп» теперь останавливает и PDF-парсинг (`_pdf_runner.stop()`), не только агента.
6. **`config/settings.yaml`**: добавлен `pdf_parser.timeout: 900` (15 мин на большие сканы; таймаут теперь реально срабатывает).

### Верификация
- Smoke-тест на реальном PDF (ASCII-копия): `parse_async(timeout=40)` получил **15 живых событий прогресса** (`Layout Predict 0%→24%`), таймаут поднялся, kill дерева отработал — процесс вернулся без зависания, `mineru`-процессов не осталось.
- Тесты: `test_pdf_parser.py` 52→**53** (+parse_async success/timeout/progress, sync Popen timeout, `_kill_tree`, `_STAGE_RE`, OCR async).
- Полный прогон: **443 passed, 0 failed** (было 434, +9).

### Важно для пользователя
- Остаточные воркеры от зависшего экземпляра (PIDs 10468/23380/23448) — убить вручную или закрыть приложение: они не умрут сами.
- 58-страничный скан на CPU обрабатывается MinerU долго (10–20 мин) — теперь это видно по прогрессу, и можно остановить кнопкой «Стоп».


## 2026-08-16 — ГЛУБОКИЙ АНАЛИЗ: колонки спецификаций детектились неправильно

### Проблема (фатальная недоработка)
Наивный substring-матчинг `detect_columns` (if/elif по первому совпадению) ломался на реальных спецификациях:
- **«Завод-изготовитель»** (780 строк в реальной спецификации) не детектился как производитель → данные терялись, агент искал без бренда.
- **«Код оборудования, изделия, материала»** попадал в NAME из-за ложного срабатывания «материал» → коды влипали в текст поиска.
- **«Тип, марка, обозначение документа»** (1404 строки!) детектился как артикул из-за «тип» — а фактически это модель/обозначение товара.
- **uom перекрывался**: «Масса единицы (кг)» (обе с «ед») побеждала «Единицу измерения» → единицы неправильные.

### Проверено на реальном файле specification_08_12-23RD_K1_OV (1489 строк)
Старый маппинг: `name=[1,3]; article=[2]; brand=[]; uom=7; qty=6` — производитель потерян, uom неверный, код в name.
Правильный: `position=0; name=[1]; spec=[2]; article=[3]; brand=[4]; uom=5; qty=6; weight=7; note=8`.

### Системное решение — `src/column_classifier.py` (новый, Qt-free)
Заменил детекцию на скоринг-модель:
1. **Нормализация** заголовков (lowercase, кавычки, пробелы).
2. **Взвешенные паттерны** для ролей position/name/spec/article/brand/uom/qty/weight/note (вес 3/2/1).
3. **Валидация по значениям** (сэмпл 50 строк): лексикон ед. изм., целые/десятичные числа, номера позиций («1.»). Противоречивые значения понижают скор (числа — не uom; «масса/вес» — не uom).
4. **Назначение по лучшей роли** — колонка идёт в роль с макс. комбинированным скором; одиночные роли берут только колонки, где они лучшие. Неклассифицированные → `unmapped` + лог при загрузке.

### Прокинуто в агента
- `SpecItem` + `spec` (тип/обозначение); `get_specs` заполняет brand/spec/article.
- `mcp_agent_runner.spec_meta` → + `spec`.
- `_build_context`: «Завод-изготовитель», «Тип/обозначение», «Артикул/код».
- **SYSTEM_PROMPT правило 16**: использовать завод-изготовитель/тип/артикул для правильного выбора товара.
- Производитель **не конкатенируется** в имя/запрос (убрано из `build_item_name`) — идёт отдельно в контекст.

### Файлы
- Новый: `src/column_classifier.py`, `tests/test_column_classifier.py` (25 тестов: реальная структура файла, варианты заголовков, диз-амбигуация, fallback).
- Изменённые: `src/excel_writer.py`, `src/mcp_agent_runner.py`, `src/agent_loop.py` (правило 16), `tests/test_excel_writer.py` (brand отдельно от name), `readme.md`, `state.md`.

### Тесты
- **468 passed, 0 failed** (было 443, +25).
- Реальный файл: маппинг идеальный, 780 позиций с брендом / 1404 с типом / 115 с артикулом доходят до агента.


## 2026-08-16 — CRITICAL FIX: агент искал чужие наименования (устаревший текст подходов)

### Симптом (лог реального прогона 1489 строк)
Агент получал подходы, в шагах которых жёстко зашит текст ДРУГОГО товара:
`browser_type text=SRE-Е-2,5/STY-2,5 target=e66` при товаре «Воздуховод из оцинкованной стали Ø100»,
`запрос=SRE-Е-2,5/STY-2,5 (220В, 2,5А) Регулятор скорости». Агент мог искать чужое наименование.
Плюс в контекст инжектились шумовые строки: «Завод-изготовитель: Россия», «Тип/обозначение: ГОСТ 14918-2020».

### Root Cause
1. **Подходы в графе хранят хардкод-текст поиска** в concrete-шагах (`browser_type text="SRE-Е-2,5/STY-2,5"`),
   сохранённый при обучении на другом товаре. `_apply_approach` подменял только `search_query`
   и шаги с `param_slot` — а шаги без слота с чужим текстом показывались агенту как есть.
2. **`_execute_graph_tool("get_approaches")`** рендерил сырые шаги (включая чужой `text`) без адаптации.
3. **Инжекция brand/spec в контекст** без санации: «Россия» (страна, 114 раз) как производитель,
   «ГОСТ 14918-2020» как тип/обозначение.

### Проверено: spec_text НЕ искажается
- Байт-точный дифф `get_specs()` до (7d4bfc3) и после (fda7aff): **diff_in_first20 = 0** —
  наименования идентичны. Искажение шло от подхода, а не от чтения спецификации.

### Fix
1. **`_apply_approach`** (`agent_loop.py`): шаги `browser_type`/`type_text` без плейсхолдера `{slot}`
   подменяют текст на текущий spec_text. Шаблоны с `{slot}` по-прежнему подставляются.
2. **`_execute_graph_tool(..., spec_text)`**: `get_approaches` адаптирует каждый подход через
   `_apply_approach` перед рендером → чужой текст не показывается.
3. **`_clean_brand`** (`excel_writer.py`): убирает кавычки и «страновые» значения
   («Россия», «РФ», «СНГ»…) — реальные бренды остаются. Теперь 666 позиций с настоящим производителем.
4. **`_is_standard_reference`** (`agent_loop.py`): «ГОСТ/ТУ/СНиП/ISO…» не инжектится как «Тип/обозначение».
5. **SYSTEM_PROMPT правило 16** дополнено: страна/стандарт — НЕ бренд, не использовать в поиске.

### Верификация
- Контекст первой строки после фикса: устаревший «SRE-Е-2,5/STY-2,5» заменён на актуальный товар,
  «Россия» и «ГОСТ» отсутствуют.
- **479 passed, 0 failed** (было 468, +11: _apply_approach scrub, _is_standard_reference, _clean_brand).


## 2026-08-16 — FIX: агент искал воздуховод на электро-сайтах (загрязнение подходами типа товара)

### Вопрос пользователя
Почему вместо воздухоотводчика (строка 1) ищется воздуховод и на сайтах «электро тематики»?

### Диагностика (факты)
1. **Планировщик переупорядочивает строки по сайтам**: «Воздуховоды» (тип
   `ventilation_climate_ventilation`) обрабатываются раньше «Воздухоотводчика»
   (`plumbing_heating_pipes`) — это дизайн TaskScheduler (Фаза 2), не искажение.
2. **Корень**: тип `ventilation_climate_ventilation` слишком широкий — в него входят И воздуховоды,
   И регуляторы скорости. В графе 8 подходов для этого типа, ВСЕ сохранены при обучении на
   «Регуляторе скорости SRE-Е-2,5» (search_query = «SRE-Е-2,5 (220В, 2,5А) Регулятор скорости») на
   `satro-paladin.com` (электро-сайт). Эти подходы показывались как «Успешные подходы» для КАЖДОЙ
   воздуховод-строки → агент шёл на satro-paladin.com и искал воздуховод там.
   `get_sites("ventilation_climate_ventilation")` тоже загрязнён: [kvent.ru, vseinstrumenti.ru, satro-paladin.com].

### Системный фикс — релевантность подхода товару
- **Новый `src/approach_relevance.py`**: `approach_relevant(approach, spec_text, extra_text)` —
  значимые слова сохранённого search_query подхода пересекаются со словами товара
  (наименование + артикул). Нет пересечения → подход скрыт. Нет данных → показать (fallback).
- **`_build_context`**: фильтрует подходы по релевантности (артикул из spec_meta учтён).
  Автоматически чинит и сортировку сайтов: сайты только с чужими подходами больше не в approach_sites.
- **`get_approaches`** (tool): фильтрует → «Нет подходов, релевантных текущему товару».
- **`TaskScheduler._determine_target_site`**: 1) сайт с релевантными подходами (макс. приоритет);
  2) иначе сайт БЕЗ подходов (kvent.ru — вентиляция), не загрязнённый чужими;
  3) fallback на все сайты.

### Верификация (реальный файл)
- Воздуховод → `_determine_target_site` = **kvent.ru** (вентиляция), подходы-регуляторы скрыты.
- Воздухоотводчик → **dn.ru**, с релевантными подходами (lunda.ru, «Воздухоотводчик…»).
- Кран шаровой → **santech.ru**.
- **488 passed, 0 failed** (было 479, +9: tests/test_approach_relevance.py).


## 2026-08-16 — FIX: построчная обработка (агент обрабатывал строку 369 до строки 1)

### Симптом
«Воздуховод» (строка 369 спецификации) обрабатывался первым, хотя агент должен идти построчно.
Пользователь: «Как агент получил на вход воздуховод?»

### Root Cause
`MCPAgentRunner` вызывал `TaskScheduler.ordered_specs()` — планировщик группирует ВСЕ строки
по целевым сайтам и сортирует батчи по приоритету. Воздуховоды (тип ventilation, строка 369+)
попадали в ранний батч и обрабатывались до строки 1 (воздухоотводчик). В таблице результатов
строки маппятся обратно в исходный порядок (original_index) — визуально выглядело «построчно»,
хотя агент шёл по сайтам. Это сломало построчный контракт пользователя.

### Fix
1. `config/settings.yaml → run.group_by_site: false` (по умолчанию) — обработка строго в порядке файла.
   `MCPAgentRunner`: `if get_run_config("group_by_site", False): ordered = scheduler.ordered_specs(...) else: ordered = list(self.specs)`.
   Группировка по сайтам доступна как опция.
2. **Заголовки разделов отфильтрованы**: строки-разделы («Отопление», «Вентиляция») без количества
   больше не становятся товарами (`get_specs` пропускает строки с пустой qty-колонкой).
   Реальный файл: 1489 → 1476 товаров, первая строка = «Воздухоотводчик автоматический Ду15».

### Верификация
- Порядок: 1) Воздухоотводчик автоматический Ду15, 2) Кран шаровой дренажный Ду15, ...
- **490 passed, 0 failed** (+2: section-header filter tests).


## 2026-08-16 — CRITICAL FIX: агент сохранял цены НЕСОВПАДАЮЩИХ товаров (катастрофа кэша)

### Симптом (лог реального прогона, 328 строк за ночь)
- Агент сохранял цены чужих товаров как «точное совпадение»: кран шаровый → клапан балансировочный.
- `save_confirmed_price` проверял ТОЛЬКО цену (`validate_result`), НЕ название товара.
- **131 цена добавлена в кэш этой сессией без проверки соответствия** — загрязнение, которое
  переиспользуется правилом 8 (confidence ≥ 0.9) → каскад ошибок.

### Root Cause
`save_confirmed_price` (tool + process_row) доверял LLM на слово «точное совпадение».
Тип товара `plumbing_heating_valves_armature` широкий (краны + клапаны + воздухоотводчики),
поэтому LLM мог выбрать товар того же типа, но другого вида (кран vs клапан).

### Fix — программная проверка соответствия товара при сохранении
- **`src/approach_relevance.py` → `product_name_matches(spec_text, found_name)`**:
  значимые слова без размеров («ду15», «1/2», цифры), префиксное сопоставление словоформ
  («автоматический» ≈ «автомат»). Допуск: ≥2 общих слова ИЛИ единственное слово короткого имени.
  «Кран шаровой» vs «Клапан балансировочный» → **False** (разные товары).
- **`save_confirmed_price`** (tool def): добавлен обязательный `product_name` (точное имя со страницы).
- **Валидация в 2 местах**: `_execute_graph_tool` и `process_row` (при несовпадении — отказ сохранить,
  сообщение LLM «найденный товар не соответствует спецификации — продолжи поиск»).
- **SYSTEM_PROMPT правила 5/17**: аналог ТОЛЬКО того же типа (кран для крана); передавать `product_name`;
  кран и клапан, воздуховод и воздухоотводчик — разные товары.

### Верификация
- Кран шаровой Ду15 vs Клапан балансировочный Ду15 → REJECT ✓
- Клапан балансировочный авт. vs Клапан балансировочный автомат → ACCEPT ✓
- Кран шаровой vs Кран шаровой дренажный → ACCEPT ✓
- Кран шаровой vs Кран фланцевый → REJECT ✓
- **499 passed, 0 failed** (+9: tests/test_approach_relevance.py TestProductNameMatches).

### ⚠️ Очистка заражённого кэша — ВЫПОЛНЕНА (2026-08-17)
- Удалено 131 подтверждённая цена сессии (`DELETE FROM confirmed_prices WHERE id >= 1140`): 1212 → **1081**.
- Осиротевших связей нет (approaches не ссылаются на удалённые цены).
- `semantic_cache.json` (25 записей, все свежие, из этой сессии) очищен; бэкап:
  `data/semantic_cache.json.bak_20260817`. Кэш перестроится при следующем прогоне уже под защитой валидации.
- После очистки — перезапустить прогон (теперь несовпадающие товары отклоняются автоматически).

## 2026-08-17 — Сессионный отрицательный кэш «не найденных» товаров (NegativeCache)

### Проблема
Позитивный кэш (confirmed_prices/semantic_cache) есть, а отрицательного нет: если товар
не найден и повторно встречается в спецификации, агент каждый раз тратит 300с + весь
бюджет раундов на повторный безрезультатный поиск.

### Решение — `src/session_cache.py` (Qt-free, NegativeCache)
- **Только в памяти, только в ходе одной сессии** (в БД ничего не пишется).
- `record(spec)` — учёт неудачного поиска; `is_blocked(spec)` — счётчик ≥ 2.
- Ключ — нормализованный spec_text (lowercase, обрезка, схлопывание пробелов).
- 2 неудачи → товар помечается «не найден»; 3-е+ вхождение в той же сессии пропускается
  сразу (без LLM/браузера), результат `{error: "not_found_cached", requires_review: True}`.

### Подключение
- Раннер (`MCPSpecRunner.run`, mcp_agent_runner.py): создаёт `NegativeCache()` в начале
  прогона; перед `process_row` проверяет `is_blocked` (shortcut без поиска); после
  результата с `price is None` (кроме `cancelled`/`Stopped`) — `record(spec_text)`.
- `process_row` (agent_loop.py): опциональный `negative_cache`, проверка в начале функции
  (страховка + тестируемость напрямую).

### Верификация
- 11 unit-тестов `tests/test_session_cache.py`: порог, независимость товаров,
  нормализация, custom limit, reset, blocked_count.
- 2 интеграционных теста `tests/integration/test_agent_flow.py`: blocked → поиск не
  запускается (LLM/MCP не вызываются); одна неудача → поиск выполняется.
- **512 passed, 0 failed**.

## 2026-08-17 — КАТАСТРОФА: одинаковые цены на разные размеры/товары из кэша + чистка БД

### Проблема
Прогон 17.08 (файл `specification_08_12-23RD_K1_OV_20260817_160640_priced.xlsx`) показал:
- «Кран шаровой Ду15» = 1772.2 и «Кран шаровой Ду20» = 1772.2 — одна и та же цена/URL
  (santech.ru/catalog/317/318/i2641/v10/ — страница товара **Ду20** BVR-R Ридан!).
- «Кран фланцевый Ду100», «Клапан балансировочный авт. Ду15/32/фланц. Ду50» = 15676.8 — одна цена.
- «Компенсаторы сильфонные Ду15–65» = 11092.2, «Труба стальная Ду15–50» = 512.5 — по одной цене на все размеры.

### Причина (цепочка)
1. Агент для строки «Кран шаровой Ду15» открыл страницу Ду20 (лог `[10:43:48]`, заголовок
   «Кран шаровой BVR-R Ду 20 (DN 20) Ридан»), вернул 1772.20 → `save_confirmed_price` (ID 1274, conf 0.95).
2. Guard `product_name_matches` **слеп к размерам** (размерные токены намеренно выкидывались):
   «Кран шаровой Ду15» vs «Кран шаровой Ду20 Ридан» → **True**.
3. Reuse (rule 8, confidence ≥ 0.9, fresh=False) разнёс цену на «Ду20», «Ду40», а через общие
   структурные токены {завод, изготовитель} — даже на «Теплосчетчик, завод-изготовитель Пульсар»:
   `get_confirmed_prices` требовал только overlap ≥ 2 слов без проверки типа/размера/бренда.

### Почему появилось после рефакторинга
- `get_confirmed_prices`/`save_price`/`deduplicate_prices`/rule-8/промпт байт-в-байт те же, что в `v1.0-pre-refactor`.
- Триггер — `fda7aff` (16.08, часть рефакторинга): `build_item_name` перестал включать бренд в spec.text
  (было «Ридан Кран шаровой Ду15», стало «Кран шаровой Ду15», бренд отдельно в `SpecItem.brand`).
  → (1) поисковый запрос без бренда — шире/шумнее выдача, агент чаще берёт чужой товар;
  → (2) LLM дописывает бренд при сохранении — в БД «Кран шаровой Ду15, завод-изготовитель Ридан»,
  общие токены {завод, изготовитель} — новый вектор фальш-матчей (Теплосчетчик ↔ Кран), которого в v1.0 не было.

### Решение (принято пользователем): матчинг по трём измерениям
1. **Тип** — значимые слова (как раньше, словоформы).
2. **Размер** — если есть в обоих, обязан совпадать («Ду15» ≠ «Ду20», «Ø100» ≠ «Ø200», «1/2"» ≠ «3/4"», «500x800» ≠ «500x1000»).
3. **Бренд** — если есть в обоих, обязан совпадать («Ридан» ≠ «Пульсар»).
   «завод/изготовитель/производитель» — структурные маркеры, сходство НЕ доказывают (исключены из значимых токенов).

### Реализовано (`src/approach_relevance.py`)
- `_size_key(text)` — канонические размеры: ду/дн/dn, Ø/мм, «500x1000», «1/2"», «на N выхода».
- `_brand_of(text)` — бренд после маркера «завод-изготовитель/производитель/завод/бренд/марка».
- `_STRUCTURAL_WORDS` = {завод, изготовитель, производитель, марка, бренд, гост, ту} — исключены из `_product_tokens`.
- `product_name_matches` — после проверки типа дополнительно сверяет размер и бренд (при наличии в обоих).

### Реализовано (reuse)
- `src/graph_engine.py::get_confirmed_prices` — кандидаты фильтруются `product_name_matches`
  (пересечение типа + равенство размера/бренда). Rule-8 reuse и LLM-тул автоматически безопасны.
- `src/semantic_cache.py::get_similar` — хит дополнительно требует `product_name_matches`
  (Jaccard ≥ 0.7 сам по себе пропускал «Труба Ду15 ГОСТ…» ↔ «Труба Ду20 ГОСТ…»).

### Чистка БД (рефакторинг = 16.08)
- Граница эпох: `fda7aff` 16.08 18:06 (разделение бренда). Всё с 16.08 — мусор.
- `DELETE FROM confirmed_prices WHERE created_at >= '2026-08-16 00:00'` → **1295 → 781** (удалено 514).
- Оставлены 781 доверенных (511 июнь-июль + 270 от 15.08, v1.0-эпоха).
- semantic_cache.json очищен (19 записей от 17.08, включая ошибочные).
- Бэкапы: `data/pricer_backup_20260817.db`, `data/semantic_cache.json.bak_20260817`.
- Нетронуты: approaches (712), sites (51), product_types (27), product_sites (159), hints (116).

### Верификация
- Кейсы из лога: Ду15 vs Ду20 → False; Теплосчетчик vs Кран → False; Ридан vs Пульсар → False; Ду15 vs Ду15 Ридан → True.
- Новые тесты: 9× `test_approach_relevance` (размер, дюймы, габариты, Ø, бренд, структурные слова),
  3× `test_graph_engine` (другой размер/товар/бренд не возвращаются), 2× `test_semantic_cache`.
- Обновлены: `test_get_confirmed_prices_by_token_overlap`, `test_get_relevant_prices` (пересечение типов
  без размера больше НЕ переиспользуется — 3x1.5 ≠ 3x2.5).
- **525 passed, 0 failed**.

## 2026-08-17 — Антидетект: фикс регрессоров (vseinstrumenti hcheck / lunda 401)

**Симптом**: агент стал чаще детектиться на vseinstrumenti (Cloudflare hcheck-челлендж на каждом `page.goto`,
403, таймауты — 31 детекция captcha за прогон) и lunda (мгновенный 401 «blocks automated access»).
Код антидетекта не менялся с `45daa3b`, сайты раньше работали.

**Причины**:
1. **Устаревший User-Agent**: в `src/mcp_bridge.py` жёстко `_USER_AGENT Chrome/134.0.0.0`, а установленный
   Chrome — 151.0.7922.138. Современный Chrome шлёт `Sec-CH-UA` + `navigator.userAgentData` с реальной
   версией 151 → WAF видит несоответствие UA/фактической версии (классический маркер автоматизации).
2. **`browser.headless: true`** в рабочем `config/settings.yaml` (незакоммичено; HEAD = `false`).
3. **Конфликт WebGL-патчей** в `config/stealth.js`: патчи 7 и 17 переопределяли одни константы
   (37445/37446 == 0x9245/0x9246) разными значениями; выигрывал 17-й — «Intel UHD Graphics 620
   Direct3D11», а реальный GPU машины **NVIDIA RTX 5090** → репортируемый renderer не совпадает
   с фактическим рендером WebGL. Плюс патч 7 отдавал Linux-строку «Mesa DRI» на Windows.

**Фиксы**:
- `src/mcp_bridge.py`: удалены `_USER_AGENT` и `--user-agent` оверрайд — браузер отдаёт реальный UA 151
  + согласованные Client Hints.
- `config/settings.yaml`: `browser.headless: false` (как при успешных прогонах).
- `config/stealth.js`: удалены WebGL-патчи 7 и 17 (реальный GPU теперь виден напрямую — это консистентно
  для реального браузера); патчи перенумерованы 1–15, node --check OK.

**Проверка**: `node --check config/stealth.js` OK; `py_compile src/mcp_bridge.py` OK;
`pytest -q` — **525 passed** (25.3s).

**Замечание**: блок lunda по IP (request_ip=5.228.80.117) может сохраняться несколько часов даже после
смены отпечатка; при первом прогоне после фикса стоит сверить runtime.log.


## 2026-08-18 — Рецидив подбора цен из кэша: усилен матчинг и починен дедуп

**Симптом (лог 18.08, отменён ~07:19, 146/344)**: агент переиспользует цены кэша для
несоответствующих товаров — «клапан баланс. статический Ду15» получил цену 15 676,80 ₽
с карточки santech i1322 «Клапан балансировочный **автомат** APT-R Ду15 Ридан» (автоматический,
не статический). Прошлый фикс `8f71fb3` (тип+размер+бренд) не закрыл подтипы.

**Первопричины**:
1. `product_name_matches` принимал совпадение по ≥2 из 3 токенов: «клапан»+«баланс.» — а
   различающее слово подтипа «статический» игнорировалось. Воспроизведено:
   `get_confirmed_prices("клапан баланс. статический Ду15")` → 5 строк «авт.».
2. Дубликаты: `save_confirmed_price` всегда INSERT, дедуп в `memory_manager.save_price`
   находил existing, но игнорировал его → rule8_reuse плодил копии (8 дублей «авт. Ду15»;
   тест `save_price` при 8 одинаковых строках создавал новую id=1649).
3. `product_name` проверка обходилась: inline-handler слабый, `_execute_graph_tool` валидировал
   по `args["spec_text"]` (от LLM), финальный `final_attempt` JSON сохранялся без валидации.

**Фиксы**:
- `src/approach_relevance.py`: `product_name_matches` — полное покрытие значимых токенов спеки
  (required-множество обязано присутствовать в найденном), префикс-порог 4→3 (авт↔автоматический),
  `_PARAM_WORDS` (ру/kvs и цифровые токены необязательны).
- `src/graph_engine.py`: `save_confirmed_price` обновляет строку при `data["id"]`, ставит `_built=False`.
- `src/agent_loop.py`: флаг `price_confirmed`; инлайн- и graph-tool обработчики `save_confirmed_price`
  требуют `product_name` (иначе reject) и валидируют по реальному `spec_text` строки; финальный
  `final_attempt` при `price_confirmed=False` капается на confidence ≤ 0.7 + `requires_review=True`
  (непроверенная цена не становится доверенным кэшем).

**Тесты**: +6 юнит-кейсов `test_approach_relevance.py` (статический↔авт отклонён, авт↔автоматический
принят, параметрические токены необязательны, короткое имя не покрывает), +2 `test_graph_engine.py`
(подтип не переиспользуется, дедуп через MemoryManager = 1 строка), интеграционный
`test_agent_flow.py::test_full_extraction_flow` обновлён под кап 0.7/requires_review. **532 passed**.

**Очистка БД**: бэкап `data/pricer_backup_20260818.db`; дедуп по `(spec_text, product_type_id,
site_id, price, url)` + near-дубли с `product_type_id IS NULL` — **945 → 303 строки**.
Проверка: `get_confirmed_prices("клапан баланс. статический Ду15")` → 0 строк;
«Клапан балансировочный авт. Ду15» → 1 строка (id 1491, 15 676,8). `semantic_cache.json` чист (18 ключей).


## 2026-08-18 — Агент открывает карточку, но не извлекает цену (santech, компенсатор Ду15)

**Симптом (лог 10:40–10:41, строка 12 «Компенсатор сильфонный под приварку Ду15»)**:
агент открыл карточку `santech.ru/catalog/293/306/i46584/v155997/`, цена на ней ЕСТЬ
(`.ss-js-price` → «7 201,30 Р», проверено вживую), но агент не извлёк её и после
StuckDetector CRITICAL ушёл на lunda/valtec.

**Причины (по логу + живому DOM карточки)**:
1. **Сломанный JS в browser_evaluate** — 2 раза подряд `SyntaxError: Unexpected end of input`
   (карточка, 10:41:16 и 10:41:25). Модель пишет `return x; // комментарий }` — закрывающая
   скобка на той же строке ПОСЛЕ `//` игнорируется → функция не закрыта. Никакого ремонта JS
   перед отправкой не было.
2. **`browser_click` с URL вместо ref** — модель находит ссылку через evaluate и кликает
   `{"target": "https://..."}`; Playwright MCP падает `Unexpected token "" while parsing css
   selector ""` → потерянный раунд + принудительный fallback на navigate (4+ раз за прогон,
   включая 10:41:00 клик по карточке).
3. **Первый `[class*="price"]` — не цена**: на santech первый матч широкого селектора —
   иконка «Корзина» (`js-order__price-formatted`). Агент получил `{text:"Корзина",...}` и решил,
   что цены нет.
4. **Символ валюты**: santech использует «Р» (7 201,30 Р), агент искал `₽` → null.
5. **Обостряющее**: бюджет контекста 8000 токенов обрезает историю каждый ход («kept 30 of 37»),
   агент теряет уже извлечённое и повторяет упавшие паттерны до 16 раундов на сайте.

**Фиксы**:
- `src/mcp_bridge.py`: `_sanitize_js()` — строкозависимая обрезка хвостового `//`-комментария
  и балансировка фигурных скобок перед отправкой в `browser_evaluate`/`browser_run_code_unsafe`
  (логируется «JS repaired»). Проверено на реальных упавших образцах из лога — исполняются.
- `src/mcp_bridge.py`: `browser_click` с URL-target переписывается в `browser_navigate`
  (не тратит раунд и не падает на пустом css-селекторе).
- `src/agent_loop.py` SYSTEM_PROMPT: правило 18 — цена может быть «₽/P/р./руб», искать по
  классам `[class*="price"]`, собирать ВСЕ кандидаты и выбирать числовой (первый матч может
  быть «Корзина»); правило 19 — запрет `//` в конце строки перед скобкой, комментарии только
  отдельной строкой.

**Проверка**: вживую на карточке — починенный JS исполняется; паттерн «все кандидаты → фильтр
по цифре» даёт `.ss-js-price = 7 201,30 Р`. Тесты: +10 в `tests/test_mcp_bridge.py`
(sanitize_js 6, is_url 4, URL-click→navigate, sanitize в call_tool) — **541 passed** (~30s).

## 2026-08-18 — Глубокий фикс логики: агент не диагностирует неудачу извлечения цены

**Согласованный план (без изменения лимитов раундов)**: бюджет сайта остаётся
`MAX_ROUNDS_PER_SITE=15`, `MAX_ROUNDS=60`, таймаут строки 300с, диагностика капится
2 подсказками на сайт — время на товар не растёт, фиксы лишь заставляют агента
использовать уже имеющиеся раунды по делу.

**Корень логического сбоя**:
1. `record_action` строил подпись `action_type:target`, а у `browser_evaluate` нет `target`/`url`
   (только `function`) → target всегда пустой → **3 подряд РАЗНЫХ evaluate давали одинаковую
   подпись `browser_evaluate:` → ложный StuckLevel.CRITICAL** («зацикливание»).
2. CRITICAL + `rounds_on_site > 5` мгновенно форсировал уход: `current_site=""`,
   `rounds_on_site=limit+1` → впрыск «Принудительно переключись на ДРУГОЙ сайт».
   Ошибки извлечения (`SyntaxError`) уже лежали в истории, но следующий ход LLM был приказом
   уйти — диагноз «карточка открыта, цена не извлечена» невозможен.

**Фиксы (`src/agent_loop.py`)**:
- **A. Подпись StuckDetector**: `_stuck_target(tool_name, tool_args)` — для `browser_evaluate`
  подставляет отпечаток JS (`md5(function)[:10]`). 3 разных скрипта ≠ цикл; 3 одинаковых = CRITICAL.
- **B. Диагностика вместо слепого ухода**: состояние строки `price_candidate_seen`,
  `recent_errors` (до 4), счётчик `diagnostic_prompts`; на CRITICAL впрыскивается
  `_build_diagnostic_message()` (карточка открыта? кандидат найден? ошибки?) и LLM получает ход
  исправить селектор/JS, вернуться к поиску или уйти. Кап 2 подсказки/сайт.
- **C. Жёсткий лимит 15 раундов**: уход сохраняется, но при `price_candidate_seen and not
  price_confirmed` сообщение требует НЕМЕДЛЕННО сохранить найденную цену через
  `save_confirmed_price`, иначе — принудительный уход.
- **D. Аннотация JS-ошибок**: результат `browser_evaluate` с `SyntaxError` дополняется
  подсказкой «перепиши код ПРОСТЫМ однострочным выражением, без // в конце строки».
- **E. Подсветка цены**: `_extract_price_candidate()` (regex `\d[\d\s.,]{0,11}\s*(руб|р\.|₽|Р|P)(?!\w)`)
  — к сообщению LLM добавляется `💰 price_candidate: <фрагмент>`; сброс `price_candidate_seen`
  при смене домена (navigate + snapshot-sync).
- Хелперы: `_is_product_card_url()` (`/catalog/…/i\d+`, `/product|item|p|products/`, `?id=`),
  `DIAGNOSTIC_PROMPT_CAP=2` из конфига.

**Тесты**: `tests/test_agent_loop.py` +20 (StuckTarget 6, ProductCardUrl 5, PriceCandidate 6,
DiagnosticMessage 3), `tests/integration/test_agent_flow.py` +2 (диагностика на открытой карточке
без слепого ухода; кап диагностик → принудительный уход), плюс поведение старого теста
`test_stuck_recovery_forces_site_switch` сохранено (CRITICAL → восстановление → цена).
**564 passed** (~33s).
---

## 2026-08-18 — Чистка семейных страниц + ранний выход при пустых результатах (Task 2/3)

### Контекст (прогон 11:11–11:19)
- Карточка santech `catalog/337/340/i1322/v6/` — «Клапан балансировочный автомат латунь APT-R Ду50»
  45 492,50 Р: товар НЕ соответствует спецификации (латунный резьбовой != фланцевый Ду50).
  Отказ агента сохранять был корректным; фланцевый Ду50 Ридан отсутствует на всех сайтах — «не найдено» честно.
- Загрязнение: в выходном XLSX «Кран фланцевый Ду100» = 15 676,8 с URL `i1322/` (семейная страница).
  15 676,80 — цена варианта Ду15 APT-R; на i1322 несколько модификаций с разными ценами.

### Расследование
- 26 строк `confirmed_prices` имеют семейные URL `/catalog/N/M/i<id>/` (без `/vN/`): часть — явные
  кросс-продуктовые загрязнения (id 655 «Тройник» = цена отвода i551; id 687/713/803 «Переход» = цена отвода;
  id 1491 «Клапан балансировочный авт. Ду15» = 15 676,8/i1322 — источник flagged-цены).
- Текущий матчер `product_name_matches` для «Кран фланцевый Ду100» уже возвращает корректный id 1489
  (80 951,95, ridan 065N9548GR); `get_similar()` = кэш «Кран фланцевый Ду100» → 80 951,95.
- Прогон 11:11 не создавал строк после 11:00 → reuse шёл через rule-8/семантический кэш; после чистки БД
  и кэша воспроизвести 15 676,8 для «Кран фланцевый Ду100» невозможно.

### Чистка данных (выполнено)
- Бэкап: `data/pricer_backup_20260818_cleanup.db`.
- Удалено 26 строк с семейными URL (310 → 284 строки).
- Семантический кэш: удалены 4 записи с семейными URL (Клапан балансировочный авт. Ду15/i1322,
  Теплосчетчик/i43640, муфта стальная переходная/i1112, Компенсатор Ду40/i46584).

### Код (src/agent_loop.py)
- **Task 2 — ранний выход**: `EMPTY_PROBE_LIMIT=3` (config `empty_probe_limit`). Состояние строки
  `empty_probe_streak` (per-domain) + `empty_probe_guidance_sent` (cap 1/сайт). Пустые зонды
  (`_is_empty_search_result`: evaluate → `[]`/`{}`/`null`/`""`/…, find → `No matches found`) накапливают
  счётчик; на 3-м подряд впрыскивается guidance «товара нет на сайте — переключись». Сброс:
  смена домена (navigate + snapshot-sync), `price_candidate_seen`, сохранение цены. Непустые зонды
  счётчик НЕ сбрасывают (по логу 11:18:31 максимум был 2 при сбросе на непустых).
- **Task 3 — защита от семейных страниц**: `_is_family_page(url)` = `/catalog/\d+/\d+/i\d+$` (без `/vN/`).
  Guard в трёх местах:
  1. `_execute_graph_tool('save_confirmed_price')` — главный (первое сохранение происходит там);
  2. in-loop блок `save_confirmed_price` — ошибка LLM с требованием перейти на карточку `/i<id>/v<N>/`;
  3. `_save_price_and_approach()` (страховка для final_attempt) + `_store_semantic_cache()` (кэш не травится).

### Тесты
- `tests/test_agent_loop.py`: +18 (TestFamilyPage 7, TestEmptySearchResult 9, TestExecuteGraphTool 2 —
  семейная страница отклоняется, карточка варианта сохраняется).
- `tests/integration/test_agent_flow.py`: +3 (guidance после 3 пустых зондов; зонд с ценой не пустой;
  сохранение с i1322 отклоняется, БД не загрязняется).
- **585 passed** (~27s).
### Правило 20 — пред-кликовая проверка карточки (добавлено к Task 2/3)
- Лог прогона (11:17:49 → 11:17:58 → 11:18:26): агент открыл карточку i1322/v6 (латунный APT-R Ду50),
  ЗАРАНЕЕ написав «это латунные клапаны, нужен фланцевый» — выбор по частичному совпадению
  (Ридан+Ду50+авт) без учёта дискриминирующего атрибута «фланцевый vs резьбовой».
- Название до/после перехода одинаковое — не ошибка распознавания; причина: нет жёсткого гейта
  до навигации (`product_name_matches` срабатывает только в `save_confirmed_price`), а правила 4/16
  промпта подталкивали «кликнуть по карточке»/«отдать предпочтение по бренду/артикулу».
- Добавлено правило 20 в SYSTEM_PROMPT (`src/agent_loop.py:194`): перед кликом сверять название
  результата со спецификацией дословно по ВСЕМ обязательным атрибутам (тип соединения, материал,
  Ду, тип товара, бренд/артикул); при отсутствии/противоречии — НЕ открывать карточку;
  на неподходящей открытой карточке не извлекать цену и не искать «варианты» — немедленно уходить.
- Тест: `tests/test_agent_loop.py` TestConstants.test_system_prompt_pre_click_verification_rule.
- **586 passed**.


## 2026-08-18 — Пропуск позиций в предпросмотре + транзитивные полные аналоги

### Исследование
- Предпросмотр (`main.py` `_show_preview`) — read-only таблица; `start_processing()` пересобирает specs из
  Excel (`get_specs()`), предпросмотр на прогон не влиял.
- Существующий сессионный `NegativeCache` (`src/session_cache.py`) — образец session-scoped кэша.
- Матчер `product_name_matches` (`src/approach_relevance.py`) — «тот же товар» по трём измерениям:
  тип (все значимые слова), размер (Ду/Ø, если есть в обоих), бренд (если есть в обоих). Для «полностью
  аналогичных» используется симметрично: `product_name_matches(a,b) and product_name_matches(b,a)`.
- Решение пользователя: транзитивность = точное совпадение ИЛИ полный аналог; бренд учитывать.

### Новый `src/skip_registry.py` (Qt-free)
- `SkipRegistry`: `mark(text, brand)`, `unmark`, `is_skipped`, `matches` (возвращает описание помеченного
  аналога для причины), `blocked_count`, `reset`, `len`.
- Ключ сравнения: `text + " " + brand` (нормализация: lowercase + схлопывание пробелов).
- Пропуск, если: точное совпадение ключа ИЛИ `_full_analog` = симметричный `product_name_matches`
  (защита от «нет токенов → True»: оба описания должны иметь значимые слова).
- Бренд-специфичность: «Ридан» ≠ «Пульсар», отмеченный с брендом не пропускает строку без бренда.

### ExcelWriter: `spec_for_row(excel_row)` (рефакторинг `get_specs`)
- Строит `SpecItem` для одной строки с той же семантикой (пропуск пустых имён и строк без qty) —
  spec_text в предпросмотре и в runner'е совпадают 1:1, отметки попадают точно.

### Runner (`src/mcp_agent_runner.py`)
- `MCPAgentRunner(..., skip_registry=None)`; в цикле до negative_cache: если `is_skipped(spec.text, spec.brand)`
  → результат `error="skipped_by_user"`, `reason="пропуск пользователем (аналог: …)"`, audit +
  `row_done_signal` + continue.

### GUI (`main.py`)
- Колонка «Пропустить» (чекбоксы) первой в предпросмотре; в данных ячейки: excel_row, полный spec.text,
  brand (из `spec_for_row`).
- Живая транзитивная синхронизация: `_on_preview_item_changed` → `mark/unmark` → `_reconcile_skip_checks()`
  (с флагом `_skip_reconciling` против рекурсии); аналогичные строки авто-отмечаются, серые + tooltip с причиной.
- Кнопка «Снять отметки» в тулбаре (`_clear_skip_marks`); реестр сбрасывается при загрузке нового файла
  (сессия = текущая загрузка спецификации); реестр передаётся в `MCPAgentRunner`.

### Тесты
- Новый `tests/test_skip_registry.py` (18): mark/unmark/dedupe/reset/normalization, полные аналоги
  (перефразировка, лишний параметр), НЕ-пропуски (другой размер/тип/бренд, бренд vs без бренда,
  одиночное слово без каскада, «Отопление» vs «Вентиляция»), `matches`, интеграция с runner (storage).
- `tests/test_excel_writer.py`: +4 (TestSpecForRow — совпадение с get_specs, заголовки разделов, пустые имена, no-ws).
- **610 passed** (было 586, +24).

## 2026-08-18 — Кандидаты-фолбэки «не совпадает бренд» + ужесточение правила 20

### Инцидент
- Row 11 «Клапан балансировочный авт. фланцевый Ду100» (завод-изготовитель «Ридан»): агент открыл подряд
  7 карточек «для проверки полного названия» (BROEN/Benarmo/CIM ручные, Giacomini ручной, Ридан ручной),
  затем i867/v3 (Giacomini R206CY310 автоматический фланцевый Ду100, цена 328 106,60 ₽ под заказ),
  но не сохранил цену (бренд Giacomini ≠ Ридан) и ушёл на следующий домен.
- Решение пользователя: бренд — НЕ жёсткий атрибут. Если бренда нет в запросе или он не важен — агент
  должен ЗАПОМИНАТЬ товары, совпадающие по всем атрибутам кроме бренда, и вставлять лучшего в строку
  с пометкой «не совпадает бренд», если точный товар так и не найден.

### `src/approach_relevance.py`
- `product_name_matches` → ядро `_product_matches_core(check_brand=True/False)`; новый
  `product_name_matches_ignore_brand()` — при `check_brand=False` из токенов убираются токены бренда
  (из `_brand_of`), т.к. иначе имя бренда остаётся обязательным словом спецификации.
- `_expand_conn_abbrev()`: «фл» → «фланцевый» (2-символьное слово выпадает из `_WORD_RE`, из-за чего
  кандидат Giacomini «Ду 100 Ру16 фл» не проходил по обязательному «фланцевый»). Применяется в
  brand-ignore матчере для обеих сторон.

### `src/agent_loop.py`
- SYSTEM_PROMPT правило 20 переписано: карточку открывать только если в названии результата есть
  тип + соединение + Ду; «для проверки полного названия» — потерянный раунд, не открывать.
- Новое правило 21: бренд не жёсткий; товар, совпадающий по всем атрибутам кроме бренда, сохранять
  через `save_confirmed_price(brand_mismatch=true)` как КАНДИДАТ-ФОЛБЭК и продолжать поиск точного;
  строка заполняется лучшим кандидатом с пометкой, если точный не найден.
- `GRAPH_TOOL_DEFS.save_confirmed_price`: новый параметр `brand_mismatch` (описание в tool-def).
- `process_row`: локальный `fallback_candidates`; инлайн-обработчик `save_confirmed_price` при
  `brand_mismatch=true` проверяет `product_name_matches_ignore_brand`, кладёт кандидата в список
  (НЕ в БД, НЕ финал), отвечает агенту «продолжай поиск» и продолжает цикл.
- `_execute_graph_tool.save_confirmed_price`: при `brand_mismatch` цена в БД не пишется, возвращается
  сообщение агенту.
- Конец `process_row` (исчерпаны раунды, точной цены нет): `_fallback_result()` — лучший кандидат с
  `brand_mismatch=True`, confidence кап 0.5, `requires_review=True`, reason «не совпадает бренд: …».
- Хелперы: `_pick_best_fallback()`, `_fallback_result()` (чистые, покрыты тестами).
- `_result_to_schema`: пробрасывает `brand_mismatch`.

### Схема (`src/models/schemas.py`)
- `ExtractionResult.brand_mismatch: bool = False`.

### GUI и XLSX (`main.py`, `src/excel_writer.py`)
- `_on_row_done`: при `brand_mismatch` строка — предупреждающий цвет, лог WARN «НЕ СОВПАДАЕТ БРЕНД»,
  в выходной файл пишется пометка «не совпадает бренд».
- `excel_writer._find_output_headers`: новая выходная колонка «Пометка» (детект «помет»/«примечан»);
  `write_result` пишет пометку при `state.brand_mismatch`.

### Тесты
- `tests/test_approach_relevance.py`: +7 (TestProductNameMatchesIgnoreBrand — разный бренд принят,
  «фл»→фланцевый для Giacomini, разный тип/подтип/размер отклонён).
- `tests/test_agent_loop.py`: правило 20/21 переписаны под новую формулировку, +5 (TestFallbackResult —
  выбор лучшего по confidence, пометка/кап confidence/reason, пустые кандидаты, product_type/site).
- `tests/test_schemas.py`: +2 (default False, флаг True + model_dump).
- **624 passed** (было 610, +14).

## 2026-08-18 — MCP зависал навсегда: таймаут вызова + авто-рестарт при зависании браузера

### Инцидент
- Лог `C:\Users\Ruslan\Desktop\Текстовый документ.txt`: строка «Компенсатор сильфонный под приварку Ду40»
  (ridan.ru) — после шага агента «Проверяю, есть ли на странице позиции с DN40/Ду40» вызов браузера
  не вернулся, дальше `Row 16 timed out after 300s`, и строки 17–19 виснут так же. Помогло только ручное
  обновление браузера пользователем.

### Корневая причина
1. `MCPBridge.call_tool` — `await srv.session.call_tool(...)` БЕЗ таймаута. Если Playwright-сервер застрял
   на операции страницы (native-диалог alert/beforeunload, никогда не резолвящийся browser_evaluate) —
   вызов блокируется навсегда, ретрая нет.
2. 300-сек таймаут строки в runner'е отменяет только клиентский запрос; зависшая операция остаётся в
   браузере. Сервер Playwright сериализует операции страницы → следующие строки встают в очередь за ней
   и тоже висят по 300с (каскад). `list_tools`/health_check продолжают работать (не трогают страницу) —
   поэтому мост выглядел живым.

### Фикс
- `src/config_loader.py`: `get_mcp_config(key, default)`.
- `config/settings.yaml`: секция `mcp` — `call_timeout: 60`, `restart_after_timeouts: 2`.
- `src/mcp_bridge.py`:
  - `MCPBridge(headless, call_timeout=None, restart_after_timeouts=None)` — параметры из конфига, можно
    переопределить для тестов.
  - `call_tool`: `asyncio.wait_for(session.call_tool(...), timeout=call_timeout)`; при TimeoutError →
    `mcp_circuit.record_failure()`, счётчик `_consecutive_timeouts`, возврат
    `error: tool call timed out after Ns` (агент делает ретрай/смену сайта по своей recovery-логике).
  - При `_consecutive_timeouts >= restart_after_timeouts` → `_restart_safe()` (перезапуск моста с защитным
    таймаутом 20с) — свежий браузер снимает диалоги и зависшие операции. Счётчик сбрасывается при успехе.
- `src/mcp_agent_runner.py`: при `TimeoutError` строки — `bridge.restart()` (таймаут 20с) перед следующей
  строкой, разрыв каскада зависаний; не рестартует при Stop.

### Тесты
- `tests/test_mcp_bridge.py`: +4 (TestMCPCallTimeout — таймаут возвращает error; 2 подряд таймаута →
  рестарт вызван; успех сбрасывает счётчик; обычная ошибка не считается таймаутом). `_FakeServer` получил
  `name`.
- **628 passed** (было 624, +4).

## 2026-08-18 — Аудит кэша успешных результатов + закрытие двух «потерянных» классов

### Аудит (два кэша)
1. БД `confirmed_prices` (data/pricer.db) — основной; auto-reuse (rule 8) только при confidence ≥ 0.9;
   виден агенту через `get_confirmed_prices`.
2. SemanticCache (data/semantic_cache.json) — для похожих товаров; reuse только при confidence > 0.8.

Фактические данные: БД — 287 записей, все ≥ 0.6 (0.9+ → 234, 0.8–0.9 → 20, 0.6–0.8 → 33);
SemCache — всего 18.

### Найденные «потерянные» успехи
- **brand_mismatch фолбэки (правило 21)**: строка возвращала цену с пометкой, но результат НЕ писался
  ни в один кэш → повторный прогон снова искал.
- **Аналоги rule 5 (confidence 0.3–0.5)**: `memory_manager.save_price` отклонял < 0.6
  (`if confidence < 0.6: return 0`) → такие находки полностью терялись (ни БД, ни SemCache).

### Фикс
- `src/agent_loop.py`: в конце `process_row` фолбэк-результат теперь пишется в SemCache через
  `_store_semantic_cache` перед возвратом. Безопасно: confidence капается до ≤0.5 → auto-reuse
  (порог >0.8) его не возьмёт; БД-реюз (rule 8, ≥0.9) для точного товара выполняется раньше.
- `src/memory_manager.py`: порог сохранения цены в БД снижен 0.6 → 0.3 — аналоги rule 5 теперь
  ложатся в confirmed_prices (видны агенту через get_confirmed_prices; auto-reuse 0.9 их не трогает).

### Тесты
- `tests/test_memory_manager.py`: порог в `test_save_price_below_threshold` 0.5→0.2; +1
  (test_save_price_rule5_analog_band_accepted — confidence 0.4 сохраняется).
- `tests/test_semantic_cache.py`: +1 (test_brand_mismatch_entry_stored_but_not_auto_reusable —
  фолбэк пишется, но confidence ≤ 0.8 не проходит порог auto-reuse).
- **630 passed** (было 628, +2).

## 2026-08-18 — Загрузка файла и старт агента вешали UI на секунды (регрессия фичи «пропуск»)

### Корневая причина
- Рефакторинг `get_specs` → `spec_for_row(excel_row)` внедрил **пер-строковый вызов `detect_columns`**
  (`excel_writer.py:260`), а `detect_columns` классифицирует до 50 строк × все колонки (~11.6мс/вызов).
- `_show_preview` (загрузка) и `start_processing` → `get_specs()` (UI-поток, до старта QThread) зовут
  `spec_for_row` для каждой строки → O(R²).
- Бенчмарк на реальной спецификации (1489 строк): `get_specs` **15.87с** (до рефакторинга был бы ~0.005с).

### Фикс
- `src/excel_writer.py`: маппинг колонок кэшируется в `_columns_mapping` (вычисляется один раз в
  `load_spec`, используется в `spec_for_row`). `get_specs`: **15.87с → 0.005с**.
- `main.py _show_preview`: вместо `insertRow` на каждой строке (O(R²)) — сначала собираем список строк,
  затем один `setRowCount(len)` + заполнение по индексу.
- `src/skip_registry.py`: предвычисленные токены помеченных позиций (`_tokens`) + предфильтр в `matches` —
  полный аналог проверяется только при пересечении значимых слов. 1476 строк × 60 отметок: ~0.93с
  (без предфильтра было бы ~9с).

### Тесты
- Существующие не менялись; поведение `matches`/`mark`/`unmark`/`reset` сохранено.
- **630 passed**.

## 2026-08-18 — Агент открывает карточки, но цены не сохраняются (LLM сокращает product_name)

### Инцидент (лог 15:30–15:50)
- Агент открывает карточку, извлекает цену, но сохранение отклоняется:
  - Ду25 (santech i46584/v156004): точное совпадение «Ридан 065H0022, Ду25, под приварку», цена 7 504,10 ₽ —
    `Product mismatch rejected: found=Компенсатор сильфонный осевой многослойный с кожух…` (без «приварку»).
  - Ду20 (i256/v1) и Ду40 (i22361/v72896): фолбэки тоже отклонены по той же причине. 3 из 5 открытых карточек
    теряют цену; Ду25 уходит на ridan.ru и тратит ~3 минуты на повторный поиск.

### Корневая причина
- LLM передаёт в `save_confirmed_price` Сокращённое `product_name` (вариант без «под приварку», например
  «…б/кожух»), матчер требует ВСЕ значимые слова спецификации → отклоняет. Проверено: полное название с
  карточки проходит и строгий матчер, и ignore-brand; сокращённое — нет.
- Сообщение об отклонении было абстрактным («например кран вместо клапана») — агент не знал, ЧТО не хватает,
  и бросал сайт/уходил на другой.
- Graph-tool при `brand_mismatch=true` возвращал «Кандидат-фолбэк принят» ДО решения инлайн-обработчика;
  при отклонении агент верил первому (ложному) сообщению.

### Фикс
- `src/approach_relevance.py`: `missing_required_tokens(spec, found)` — обязательные слова спецификации,
  отсутствующие в названии карточки; «под» добавлен в стоп-слова (предлог).
- `src/agent_loop.py`: `_mismatch_error_content()` — сообщение об отклонении перечисляет недостающие слова
  («отсутствуют ключевые слова: «приварку»») и требует передать ПОЛНОЕ наименование с h1 карточки. Применено
  в инлайн-обработчике и в graph-tool.
- Graph-tool `save_confirmed_price` при `brand_mismatch=true` возвращает нейтральное сообщение («система
  проверит… см. следующий результат») вместо ложного «принят» — агент больше не ориентируется на него.

### Тесты
- `tests/test_approach_relevance.py`: +5 (TestMissingRequiredTokens — полное название → [], сокращённое →
  «приварку», пустые входы, разный товар).
- `tests/test_agent_loop.py`: +4 (TestMismatchErrorContent — сообщение содержит недостающее слово и h1;
  graph-tool brand_mismatch нейтрален, strict mismatch точечный).
- **639 passed** (было 630, +9).

## 2026-08-18 — Скрипт-советник вместо скрипта-судьи (LLM управляет, скрипт подсказывает)

### Решение пользователя
Жёсткое вето скрипта над LLM («Product mismatch rejected» блокирует сохранение) показало себя
дееспособным — 3 из 5 открытых карточек теряли цену из-за сокращённого LLM `product_name`. Нужно
отдать управление LLM: скрипт — только подсказка «Не ошибся ли ты, когда выбрал это наименование»,
чтобы LLM на основе замечания провела углублённое изучение и сама решила.

### Новый поток `save_confirmed_price`
1. Скрипт проверяет наименование (strict или ignore-brand).
2. **Совпадение** — как раньше: запись, возврат результата (confidence ≥ 0.6) / кандидат-фолбэк
   (brand_mismatch) / низкий confidence → сохранили и продолжаем (с сообщением агенту).
3. **Несовпадение** — НЕ отказ, а «⚠️ КРИТИЧЕСКОЕ ЗАМЕЧАНИЕ»: перечисляются недостающие слова
   («приварку») и даётся инструкция: либо исправить `product_name` и сохранить снова, либо при
   уверенности вызвать повторно с `confirm=true` (цена принимается как требующая ревью).
4. **`confirm=true`** (новый параметр tool-def) — LLM берёт решение на себя: цена принимается,
   помечается `requires_review`, confidence капается до 0.5 (не попадает в доверенный кэш/auto-reuse),
   возвращается как результат строки.

### Устранение двойного сообщения
- `_execute_graph_tool.save_confirmed_price` стал тонким пропуском (проверка product_name/семейной
  страницы + пассивный статус) — решение принимает ТОЛЬКО инлайн-обработчик `process_row`. Больше нет
  противоречивых «принят + отклонён» для одного вызова. record_soldat перенесён в инлайн-путь.
- SYSTEM_PROMPT правило 17 переписано: замечание — приглашение к проверке (не отказ); инструкция про
  исправление названия и `confirm=true`.

### Тесты
- `TestMismatchErrorContent` → `TestMismatchWarningContent` (замечание не `error:`, содержит недостающее
  слово, «Не ошибся ли ты», `confirm=true`); graph-tool возвращает пассивный статус; семейная страница
  по-прежнему отклоняется.
- **639 passed**.

## 2026-08-18 — Чекбокс Headless не применялся во время прогона

### Причина
- `MCPBridge.start()` корректно добавляет `--headless` при `_headless=True`; конфиг `browser.headless`
  сохраняется/читается верно; playwright-core 1.63 поддерживает headless+`--browser chrome`.
- Баг: в `mcp_agent_runner.py` проверка `self._restart_bridge` (установленная `trigger_bridge_restart`
  при переключении чекбокса) стояла ПОСЛЕ цикла строк — переключение во время прогона не действовало,
  а затем bridge сразу останавливался в `finally`. Чекбокс выглядел мёртвым.

### Фикс
- `src/mcp_agent_runner.py`: проверка `_restart_bridge` перенесена в начало каждой итерации цикла строк
  (после stop-проверки) — переключение headless перезапускает bridge со СЛЕДУЮЩЕЙ строки
  (`bridge.set_headless` с защитным таймаутом 20с). Мёртвый блок после цикла удалён.
- `src/mcp_bridge.py`: лог запуска `MCP launch: ... (mode=headless|headed)` — видно в логе, какой режим
  реально поднялся.
- Study-чекбокс (graph_assistant) сохраняет конфиг; study_runner читает его при каждом старте — ок.

### Тесты
- `tests/test_mcp_bridge.py`: +1 (test_start_passes_headless_flag — мокает stdio/ClientSession, проверяет,
  что `--headless` попадает в аргументы при headless=True и отсутствует при False).
- **640 passed**.
