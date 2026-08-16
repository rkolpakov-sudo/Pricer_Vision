# Pricer Vision — Правила работы

## Процесс разработки (loop)

1. Пользователь ставит задачу
2. Вносим изменения в код
3. Пользователь тестирует и сообщает результат
4. Записываем действие и эффект в `state.md`
5. Переходим к шагу 1

**Важно:** перед изменениями читать `state.md` и `readme.md` — чтобы не повторять ошибки.

---

## Фактическая архитектура

### MCP интеграция
- Tools берутся из MCP сервера через `session.list_tools()`, конвертируются в OpenAI формат
- LLM использует нативные tool_calls (не TOOL: текст)
- MCP Bridge вызывается напрямую для browser tools
- Graph tools выполняются локально

### MCP Bridge
- `src/mcp_bridge.py` — обёртка над MCP протоколом (mcp SDK), управляет одним MCP сервером
- Сервер: `playwright` — `npx.cmd @playwright/mcp` (23 инструмента: browser automation via Playwright MCP)
- `start()`: 2 попытки на сервер, задержка 2с между retry
- `list_tools()` — возвращает tools с сервера
- `call_tool()` — роутит по `_tool_map[name]` к нужному серверу
- Health check через `session.send_ping()` на каждом сервере
- `restart()`: stop → 1s → start; `stop()`: clean exit
- `ref→target` маппинг: хеш-рефы (`e68`, `f5e17`) не маппятся, роль-локаторы (`textbox "Поиск"`) проходят как target
- Circuit Breaker (`mcp_circuit`, 5 отказов/60s): при OPEN `call_tool` → `restart()` и возврат `"error: MCP circuit open"`; success/failure фиксируются по результату `session.call_tool`

### Граф знаний
- `src/graph_engine.py` — SQLite + in-memory dicts
- `src/memory_manager.py` — CRUD прослойка с дедупликацией и intent-классификацией
- Таблицы: product_types, sites, product_sites, approaches, confirmed_prices, hints, concepts, concept_edges
- YAML загружается как seed через `engine.load_yaml_seed()` (INSERT OR REPLACE, обновляет имена)
- Тип `unknown` исключён из `_load_indexes` — не показывается в UI
- `classify_product_type()` возвращает "unknown" как fallback (существует только как значение, не как запись в БД)

### agent_loop.py
- `process_row()` — основной цикл, `MAX_ROUNDS=50` (из settings.yaml), `MAX_ROUNDS_PER_SITE=15`
- Tools = Playwright MCP tools (23) + graph tools (локальные, 7: get_approaches, save_approach, get_confirmed_prices, save_confirmed_price, search_sites, save_discovered_site, get_hints)
- Tool routing: три ветки — `GRAPH_TOOL_NAMES → _execute_graph_tool()`, `browser_navigate → MCP + навигация`, `else → MCP`
- Нативные tool_calls → execute → результат в messages → следующий LLM вызов
- Fallback: если tool_calls пуст, пробует `parse_text_tools(content)` (TOOL: метка)
- Fallback: если `parse_final_response()` не дал цены, пробует `parse_text_result(content)`
- Принудительное переключение сайта после 15 раундов на одном сайте
- save_confirmed_price проходит через `validate_result()`
- Yandex Rule 12 guard удалён — только рекомендация в system prompt
- Отрицательная обратная связь: `record_failure()` при force switch и MAX_ROUNDS, `record_success()` при успехе
- Подстановка param_slots: `_apply_approach()` заменяет `{product_name}` на spec_text
- Семантические паттерны: intent + emoji в `format_steps()`
- `format_steps()` показывает `url` для browser_navigate (первым приоритетом)
- **LLM вызовы** идут через `_query_llm()` — обёртка над `llm_client.chat` с `llm_circuit` (3 отказа/30s)
- **Pydantic-валидация**: `_result_to_schema()` валидирует финальный результат через `ExtractionResult`, наружу отдаёт `model_dump()`
- **StuckDetector**: `record_action()` после каждого MCP-шага, при CRITICAL и `rounds_on_site > 5` — принудительный уход с сайта (BLOCKED не дублируется, обрабатывается captcha-логикой)

### Системный промпт
- 15 правил
- Правило 8: цена с confidence ≥ 0.9 из графа — финальная
- Правило 9: если >10 шагов на одном сайте — принудительное переключение
- Правило 10: если не знаешь, как работать — вызови get_hints
- Правило 5 (best-match): если нет точного совпадения — сохранить лучший аналог
- Правило 12: Яндекс — только поисковик, цена из карточки на сайте магазина
- Правило 14: неподходящие сайты — быстро переключаться
- Правило 15: SPA — прямой URL поиска вместо Enter

### Code-level Yandex guard
Удалён. Правило 12 только в system prompt.

### Антидетект
- `config/stealth.js` — 12 патчей: webdriver, chrome.runtime, WebGL, permissions, plugins, connection, media, battery, screen, platform
- `config/playwright-mcp.json` — без `--disable-blink-features=AutomationControlled` (уязвимость), с `--no-sandbox`, `--disable-infobars`

### Файловая структура
```
C:\Projects\Pricer_Vision\
├── main.py                      # GUI
├── SPEC_V31.md                  # Спецификация
├── readme.md                    # Правила работы
├── state.md                     # Лог действий
├── config/
│   ├── categories_and_sites.yaml # Seed данные + hints + русские имена
│   ├── settings.yaml             # Все runtime-константы
│   ├── stealth.js                # Антидетект (12 патчей)
│   └── playwright-mcp.json       # Playwright MCP конфиг
├── data/
│   ├── pricer.db                # SQLite БД графа
│   └── output/                  # Excel результаты
├── gui/
│   ├── graph_assistant.py       # 11-туловая панель (HelpPage + CRUD + обучение)
│   ├── graph_explorer.py        # Визуализация графа (физ. симуляция, фильтры, без авто-фита)
│   └── spinner_widget.py        # Spinner
├── mcp_servers/
│   ├── __init__.py
│   ├── pricer_server.py         # MCP сервер (DrissionPage, не используется)
│   └── patchright_server.py     # MCP сервер (patchright, не используется)
├── src/
│   ├── pdf_parser/              # Парсер PDF (MinerU → fallback structurer, без LLM)
│   │   ├── mineru_backend.py    #   subprocess MinerU 3.4 в изолированном Python 3.11
│   │   ├── structurer.py        #   Fallback-only (pipe-парсинг колонок, LLM удалён)
│   │   ├── feedback.py          #   Таблица pdf_corrections в pricer.db
│   │   ├── review_dialog.py     #   QTableWidget редактирования
│   │   └── runner.py            #   QThread оркестратор
│   ├── agent_loop.py            # Основной цикл (3-веточный routing, format_steps, negative feedback, _query_llm, StuckDetector, температуры фаз, контекстный бюджет)
│   ├── adaptive_limits.py       # AdaptiveRoundManager — динамические лимиты раундов per-site (Фаза 2)
│   ├── audit_logger.py          # Audit-лог JSONL (data/audit/session_*.jsonl)
│   ├── config_loader.py         # Загрузчик config/settings.yaml
│   ├── excel_writer.py
│   ├── graph_engine.py          # SQLite + in-memory (inc кэш, unknown excluded)
│   ├── _labels.py
│   ├── llm_client.py            # HTTP клиент для LM Studio (+ retry с backoff из llm.retry, per-call temperature/max_tokens)
│   ├── mcp_agent_runner.py      # QThread обёртка (+ AuditLogger, TaskScheduler, SemanticCache)
│   ├── mcp_bridge.py            # MCP клиент (Playwright @playwright/mcp, ref→target, mcp_circuit)
│   ├── memory_manager.py        # CRUD графа (+ intent, dedup, SOLD_AT, record_soldat filter)
│   ├── models/                  # Pydantic-схемы (Фаза 1)
│   │   └── schemas.py           #   ExtractionResult, AgentDecision, ExtractedPrice, ActionType
│   ├── resilience.py            # CircuitBreaker (llm/mcp), retry_with_backoff (Фаза 1)
│   ├── semantic_cache.py        # SemanticCache — Jaccard-кэш похожих товаров (data/semantic_cache.json) (Фаза 2)
│   ├── study_runner.py          # QThread обучения (50 раундов, get_hints, утверждение)
│   ├── stuck_detector.py        # StuckDetector — зацикливание/блокировки (Фаза 1)
│   ├── task_scheduler.py        # TaskScheduler — группировка товаров по сайтам (Фаза 2)
│   ├── site_order_dialog.py
│   ├── theme.py
│   ├── toast.py
│   ├── tool_parser.py           # Парсер tool_calls
│   ├── validator.py             # Пост-валидация
│   └── widget_base.py
├── tests/
│   └── test_*.py                # Тесты (pytest)
├── logs/
│   └── runtime.log              # Последний ран
└── venv/                        # Виртуальное окружение
```

## Стилизация кнопок

`src/theme.py` определяет 7 вариантов кнопок:

| ObjectName | Назначение | Визуал |
|------------|-----------|--------|
| (default) | Обычные кнопки | `bg-surface` фон, 1px рамка, 6px radius |
| `#primary` | Главное действие (Поиск, Сохранить, Старт) | Заливка `accent`, жирный, без рамки |
| `#success` | Подтверждение (💾 Сохранить выбранные) | Заливка `success` (зелёный) |
| `#danger` | Удаление/опасное действие | Ghost: красная рамка → заливка при ховере |
| `#warning` | Осторожное действие (Депрекейтнуть, YAML reload) | Ghost: янтарная рамка → заливка при ховере |
| `#ghost` | Панельные кнопки (граф: По размеру, Экспорт) | Прозрачный, невидим до ховера |
| `#small-btn` | Иконка "+" (добавить сайт) | Минимальный padding, крупный шрифт |

Все кнопки в `graph_assistant.py` (25+) имеют явный `setObjectName`. Никаких hardcoded `setStyleSheet` на кнопках.

## Визуализация графа

- **Цвета нод**: root=gold (#FFD700), products=purple (#DDA0DD), sites=orange (#FFA500), prices=cyan (#00CED1)
- **Цвета рёбер**: соответствуют типу связи (HAS_SITE→оранж, APPROACH→голубой, HAS_PRICE→циан)
- **Фильтры**: панель чекбоксов, по умолчанию Цены и HAS_PRICE выключены
- **Авто-фит отключён**: `_fit()` вызывается только по кнопке "По размеру" — зум/панорама не сбрасываются ни при стабилизации физики, ни при перерендере
- **Информер**: `NodeInfoOverlay(QFrame)` — всплывающая панель при клике на ноду (title + type/id + details). Содержит spec, цены (product), URL (site), статистику (root). Авто-скрытие при перерендере/снятии выделения. QLabel с одним родительским stylesheet'ом (шрифты через `#objectName` селекторы).

## Splitter (main.py)

- `QSplitter(Horizontal)` между `center` (таблицы/логи) и `right_tabs` (граф/ассистент)
- `right_tabs.setMinimumWidth(0)` + `setSizePolicy(Ignored)` — сплиттер может сжать правую панель до любой ширины
- `setCollapsible(False)` не используется — пользователь может полностью схлопнуть любую панель

## UI Layout Architecture

Фиксированные размеры для верхних элементов, splitter забирает весь остаток:

| Элемент | Высота | Stretch | Margins |
|---------|--------|---------|---------|
| `btn_frame` (toolbar) | 38px | 0 | (6, 3, 6, 3) |
| `fb_frame` (spinner) | 28px | 0 | (6, 2, 6, 2) |
| `progress_bar` | 21px | 0 | — |
| `splitter` | auto | 1 | — |
| `main_layout` | — | — | (10, 2, 10, 10), spacing=4 |

- `addWidget(widget, stretch=0)` — фиксированная высота
- `addWidget(splitter, stretch=1)` — забирает остаток
- Только таблица и график растягиваются по вертикали

### Spinner
- `main.py`: 16×16px, `spacing=0.5`
- `gui/graph_assistant.py`: 24px, `spacing=0.5`
- Точки прежнего размера, расстояния между ними уменьшены

### Progress bar
- Высота 21px (было 16)
- Gradient chunk (solid → 87% opacity → solid)
- Border с `t["border"]`, border-radius 6px, inset margin 1px

## PDF Parser

### Pipeline
```
PDF → MinerU (subprocess Python 3.11) → сырой текст
  → Structurer (fallback-only, LLM отключён) → pipe-парсинг колонок
  → ReviewDialog (редактирование строк)
  → _save_pdf_items_to_excel() → временный .xlsx
  → load_spec(path=xlsx_path) → preview_table (тот же путь что и XLSX)
```

### Ключевые особенности
- **LLM отключён** — Qwen3.6 тратит 7700+ токенов на reasoning, оставляя 0–500 на JSON
- `structurer.py` — только `_parse_pipe_line()` классифицирует колонки по содержимому
- Результаты PDF проходят тот же pipeline что и XLSX: сохранение → загрузка как spec → preview_table → Старт → агент
- `_load_pdf_item_into_spec` — мёртвый код (не вызывается, может пригодиться для прямого логирования)
- Экспорт в xlsx использует "Изготовитель" вместо "Производитель" — чтобы `detect_columns` не конкатенировал brand в name

### Параметры (config/settings.yaml)
```yaml
pdf_parser:
  lang: east_slavic
  method: auto
  min_chars: 10
```

## Помощник ассистента (HelpPage)

Первая вкладка в `AssistantToolPanel` (индекс 0). Содержит полное руководство по всем 11 страницам:
- Назначение, когда использовать, примеры
- Data flow между страницами
- Best practices и ответы на частые вопросы

## Принудительное обучение (StudyRunner)

Инструмент для отладки и настройки поиска на сайтах поставщиков.

### Запуск
1. Из тулбара: кнопка **📖 Обучение**
2. Из таблицы результатов: кнопка **📖** в строке

### Работа
1. Пользователь вводит URL карточки товара на сайте поставщика
2. Агент открывает URL, находит цену, изучает структуру сайта
3. Агент может задавать вопросы через `ask_user`
4. Агент предлагает подходы, хинты, концепты → пользователь выбирает чекбоксами → сохраняет
5. Всё попадает в граф и становится доступно основному пайплайну

### Инструменты агента (10)
- `get_approaches` — чтение подходов из графа
- `save_approach` — подход с param_slots (требует утверждения)
- `save_hint` — текстовая подсказка (требует утверждения, минимум 2)
- `save_concept` — связь типа с сайтом (требует утверждения)
- `get_confirmed_prices` — похожие цены
- `save_confirmed_price` — цена (сохраняется сразу)
- `get_hints` — подсказки для типа товара
- `search_sites` — список сайтов для типа
- `save_discovered_site` — новый сайт (требует утверждения)
- `ask_user` — вопрос пользователю

### StudyPage (UI)
- URL, спецификация, тип товара — поля ввода
- Тип товара: QComboBox + ▼ кнопка (editable, placeholder "Выберите или введите новый...")
- 50 раундов, temperature=0.5

### Файлы
- `src/study_runner.py` — `StudyRunner(QThread)`, 50 раундов, temperature=0.5
- `gui/graph_assistant.py` — `StudyPage` (вкладка «Обучение»)

## Стабильность ядра (Фаза 1 рефакторинга v2.0)

Реализована на ветке `phase/1-core` (коммит `d9a9e7b`, тег `phase-1-done`).

### Pydantic-валидация
- `src/models/schemas.py` — `ExtractionResult` (контракт `process_row`), `AgentDecision`, `ExtractedPrice`, `ActionType`
- `ExtractionResult` валидирует: `found=True` требует `price`, цена `> 0` и `<= 10_000_000`, `spec_text` не пустой
- В `process_row` финальный результат проходит `_result_to_schema()` → `model_dump()` (контракт runner/ExcelWriter сохранён)
- ⚠️ pydantic 2.13 НЕ запускает `field_validator` на значениях по умолчанию — используем `model_validator(mode="after")`

### Circuit Breaker / Retry / StuckDetector / Audit
- `src/resilience.py` — `CircuitBreaker` (синглтоны `llm_circuit` 3/30s, `mcp_circuit` 5/60s), `retry_with_backoff` (sync+async)
- `src/stuck_detector.py` — детект зацикливания/блокировок (CRITICAL → force site switch при `rounds_on_site > 5`)
- `src/audit_logger.py` — JSONL-лог в `data/audit/session_*.jsonl`, вызывается из `mcp_agent_runner.py`

### Конфиг
- `llm.retry` (`max_attempts`, `backoff_seconds`) в `settings.yaml` подключён к `llm_client.py`

## Оптимизация агентного цикла под локальную LLM (Фаза 2 рефакторинга v2.0)

Реализована на ветке `phase/2-llm` (коммит `4287129`, от `phase/1-core`).

### TaskScheduler (`src/task_scheduler.py`)
- Группирует товары по целевым сайтам (`ProcessingBatch`), сортирует батчи по приоритету (`success_rate*0.4 + работа*0.3 + простота*0.2 + скорость*0.1`).
- `_determine_target_site()`: `classify_product_type` → `mm.get_sites` → лучший по `priority − consecutive_failures*0.5`, fallback `yandex.ru`.
- Интеграция в `mcp_agent_runner.py`: `scheduler.ordered_specs(self.specs)` перед циклом; исходные индексы строк сохраняются через `{id(spec): i}` → `row_done_signal.emit(original_idx, result)`.

### SemanticCache (`src/semantic_cache.py`)
- Без embeddings: нормализация (скобки/размеры убираются) + Jaccard-схожесть, md5-ключ, JSON `data/semantic_cache.json` (лимит 1000, evict 20%).
- В `process_row`: проверка после rule-8 (только `not fresh`, confidence > 0.8), запись через `_store_semantic_cache()` в точках возврата цены.

### AdaptiveRoundManager (`src/adaptive_limits.py`)
- `calculate_limit(site_profile, product_complexity)`: BASE=10, MIN=5, MAX=30, сложность = f(success_rate, failures, antibot).
- `per_site_limits(sites)` надстраивается над `site_round_limits` (failures>=3 → MIN, иначе base).

### Температура по фазам
- `LLMClient.chat()` принимает опциональные `temperature`/`max_tokens` (обратно совместимо).
- Константы в `agent_loop.py`: `TEMP_EXPLORATION=0.7`, `TEMP_NAVIGATION=0.3`, `TEMP_EXTRACTION=0.1`, `TEMP_RECOVERY=0.5`.
- Применяется в `_query_llm(..., temperature=...)` в 4 вызовах process_row.

### Контекстный бюджет
- `_estimate_tokens()` (≈len/4), `_trim_messages_for_budget()` (бюджет 8000 токенов): сохраняет system + хвост от последнего user-сообщения, усекает старые tool/assistant.
- Вызывается в `_query_llm()` перед каждым LLM-запросом.


