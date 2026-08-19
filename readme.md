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
- `src/mcp_bridge.py` — обёртка над MCP протоколом (mcp SDK), управляет одним выбранным MCP-сервером (браузерный бэкенд)
- Бэкенды (из `config/settings.yaml → browser.backend/backends`, переключаются в GUI главного окна):
  - `camoufox` (по умолчанию) — антидетект Firefox-fork, подмена отпечатка на уровне C++, реальные пресеты; запускается своим venv проекта через `mcp_servers/browser_server.py`
  - `playwright` — `npx.cmd @playwright/mcp` (23 инструмента: browser automation via Playwright MCP)
  - `nodriver` — CDP-драйвер реального Chrome (третий запасной)
- `start()`: перебор цепочки бэкендов (автофейловер), 2 попытки на сервер, задержка 2с между retry
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
├── SPEC_V31.md                  # Спецификация v31.0
├── SPEC_V32.md                  # Спецификация v2.0 (пост-рефакторинг, фазы 1–7)
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
│   ├── agent_monitor.py        # Real-time мониторинг агента (вкладка «Мониторинг»)
│   ├── metrics_panel.py        # Панель метрик прогона (9 метрик)
│   ├── graph_assistant.py      # 11-туловая панель (HelpPage + CRUD + обучение)
│   ├── graph_explorer.py       # Визуализация графа (физ. симуляция, фильтры, LOD, без авто-фита)
│   └── spinner_widget.py        # Spinner
├── mcp_servers/
│   ├── __init__.py
│   ├── browser_server.py          # MCP сервер бэкендов camoufox/nodriver (используется)
│   └── pricer_server.py           # MCP сервер (DrissionPage, не используется)
├── src/
│   ├── pdf_parser/              # Парсер PDF (MinerU → fallback structurer, LLM-опция)
│   │   ├── mineru_backend.py    #   subprocess MinerU 3.4 в изолированном Python 3.11
│   │   ├── ocr_fallback.py      #   OCR-резерв для сканов (через MinerUBackend, to_thread)
│   │   ├── structurer.py        #   Fallback-only pipe-парсинг + LLM-ветка (use_llm)
│   │   ├── review.py            #   SmartReview — confidence scoring (≥0.8 авто-утверждение)
│   │   ├── feedback.py          #   Таблица pdf_corrections в pricer.db
│   │   ├── review_dialog.py     #   QTableWidget редактирования (колонка Уверенность)
│   │   └── runner.py            #   QThread оркестратор (OCR fallback + SmartReview)
│   ├── agent_loop.py            # Основной цикл (3-веточный routing, format_steps, negative feedback, _query_llm, StuckDetector, температуры фаз, контекстный бюджет)
│   ├── adaptive_limits.py       # AdaptiveRoundManager — динамические лимиты раундов per-site (Фаза 2)
│   ├── audit_logger.py          # Audit-лог JSONL (data/audit/session_*.jsonl)
│   ├── config_loader.py         # Загрузчик config/settings.yaml
│   ├── excel_writer.py
│   ├── graph_engine.py          # SQLite + in-memory (inc кэш, unknown excluded, TTL hints, pragmas)
│   ├── _labels.py
│   ├── learning_loop.py         # LearningLoop — автообучение из результатов прогона (Фаза 4)
│   ├── llm_client.py            # HTTP клиент для LM Studio (+ retry с backoff из llm.retry, per-call temperature/max_tokens)
│   ├── mcp_agent_runner.py      # QThread обёртка (+ AuditLogger, TaskScheduler, SemanticCache, LearningLoop)
│   ├── mcp_bridge.py            # MCP клиент (мультибэкенд camoufox/playwright/nodriver, ref→target, mcp_circuit)
│   ├── memory_manager.py        # CRUD графа (+ intent, dedup, SOLD_AT, HintManager, ApproachVersioning)
│   ├── models/                  # Pydantic-схемы (Фаза 1)
│   │   └── schemas.py           #   ExtractionResult, AgentDecision, ExtractedPrice, ActionType
│   ├── resilience.py            # CircuitBreaker (llm/mcp), retry_with_backoff (Фаза 1)
│   ├── semantic_cache.py        # SemanticCache — Jaccard-кэш похожих товаров (data/semantic_cache.json) (Фаза 2)
│   ├── study_runner.py          # QThread обучения (50 раундов, get_hints, утверждение)
│   ├── stuck_detector.py        # StuckDetector — зацикливание/блокировки (Фаза 1)
│   ├── task_scheduler.py        # TaskScheduler — группировка товаров по сайтам (+ site_profiles из LearningLoop) (Фаза 2)
│   ├── human_behavior.py        # HumanBehavior — человеческие клики/печать/скролл (Фаза 3)
│   ├── rate_limiter.py          # DomainRateLimiter — per-domain RPM лимит (Фаза 3)
│   ├── site_analyzer.py         # SiteAnalyzer — детекция SPA/SSR/антибота (Фаза 3)
│   ├── captcha_detector.py      # CaptchaDetector — типы captcha + рекомендации (Фаза 3)
│   ├── column_classifier.py     # Системная классификация колонок спецификации
│   ├── site_order_dialog.py
│   ├── theme.py
│   ├── toast.py
│   ├── tool_parser.py           # Парсер tool_calls
│   ├── validator.py             # Пост-валидация
│   └── widget_base.py
├── tests/
│   ├── test_*.py                # Тесты (pytest)
│   └── integration/             # Интеграционные тесты агентного цикла (test_agent_flow.py)
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
- **LOD (Level of Detail)**: при > `LOD_THRESHOLD` (500) нодах подписи и непрерывная физическая симуляция отключаются (`_lod_decision`), граф рендерится статично и автоматически фитится. Лимит нод — `MAX_GRAPH_NODES` (1000). Чистые функции `_lod_decision`, `_edge_touches`, `_physics_step` покрыты тестами.
- **Производительность**: обновление позиций при физике троттлится (`PHYSICS_SYNC_INTERVAL` ~30fps), подписи пересчитываются только при стабилизации (`sync_all(update_labels=False)` во время физики). Фикс бага приоритета в `_update_edges` (оператор `or`/`and` без скобок давал IndexError при `u==idx` и `v` вне границ).

## Мониторинг агента

Вкладка **«Мониторинг»** в правой панели (main.py), два виджета:

### AgentMonitorPanel (`gui/agent_monitor.py`)
- Real-time отображение текущего действия (`monitor_signal` из `MCPAgentRunner`), прогресс по строкам, история действий (`QListWidget`, cap 500), кнопка «Очистить».
- `handle_event(event: dict)` обрабатывает события: `start`/`row`/`action`/`row_done`/`done`/`stop`.
- Источник событий — `monitor_signal` в `src/mcp_agent_runner.py` + `status_callback` из `src/agent_loop.py`.

### MetricsPanel (`gui/metrics_panel.py`)
- 9 метрик прогона: всего товаров, обработано, найдено, успешность, запросов к LLM, ср. время LLM, попаданий в кэш, застреваний, блокировок.
- `metrics_signal` из `MCPAgentRunner` (`_build_metrics`) — после каждой строки и по завершении.
- Форматирование — чистая функция `format_metric_value(key, value)`.

### Прокидывание метрик из agent_loop
- `process_row(..., monitor_callback)` — опциональный колбэк событий: `("llm_call", elapsed)`, `("cache_hit", similarity)`, `("stuck", None)`, `("block", captcha_type)`.
- `_query_llm(..., monitor_callback)` — замеряет время LLM-вызова и репортит `llm_call`.
- Скриншоты страницы в монитор НЕ выводятся (дополнительный MCP round-trip на каждый шаг + риск антибот-детекции) — реализовано как API-слот, не подключено.

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
  → OCRFallback (повторный MinerU, если текст < ocr_min_text_length)
  → SpecStructurer (LLM-опция use_llm → fallback pipe-парсинг колонок)
  → SmartReview (confidence scoring, авто-утверждение ≥0.8)
  → ReviewDialog (редактирование строк, подсветка низкой уверенности)
  → _save_pdf_items_to_excel() → временный .xlsx
  → load_spec(path=xlsx_path) → preview_table (тот же путь что и XLSX)
```

### Ключевые особенности
- **LLM — опция** (`pdf_parser.use_llm: false` по умолчанию): `SpecStructurer(use_llm=True)` сначала зовёт LLM, при неудаче/пустом результате автоматически падает в `_fallback_parse`. Параметры: `llm_max_chars`, `llm_max_tokens`, `llm_temperature`.
- **OCRFallback** (`src/pdf_parser/ocr_fallback.py`): `needs_ocr(text)` — True при < `ocr_min_text_length` (100) символов; повторный запуск MinerU через `asyncio.to_thread`. Бэкенд — `MinerUBackend` (mineru_venv), без PaddleOCR/Tesseract.
- **SmartReview** (`src/pdf_parser/review.py`): `_calculate_confidence()` (name 0.4 + qty 0.2 + unit 0.1 + code|mfg 0.2 + specs 0.1), порог 0.8 → `(auto_approved, needs_review)`; `row["confidence"]` добавляется к каждой позиции.
- **Асинхронный запуск MinerU** (`MinerUBackend.parse_async`): `asyncio.create_subprocess_exec` + **kill всего дерева процессов** при таймауте/отмене (Windows `taskkill /T /F`). Без этого `subprocess.run(timeout=...)` на Windows вешается навсегда: MinerU 3.4 поднимает временный API-сервис и multiprocessing-воркеров, потомки держат пайпы и `communicate()` не возвращается.
- **Живой прогресс**: стадия/процент из stderr MinerU (Layout/MFR/Table-OCR) парсятся регэкспом `_STAGE_RE` и шлются в `progress_signal` (`progress_callback(stage, percent)`) → прогрессбар приложения двигается.
- **Кнопка «Стоп»** работает и для PDF-парсинга: `PdfParserRunner.stop()` → отмена задачи → kill дерева MinerU.
- `pdf_parser.timeout` (900с по умолчанию) — жёсткий лимит; по истечении дерево убивается и выдаётся ошибка `mineru timeout`.
- `structurer.py` — fallback `_parse_pipe_line()` классифицирует колонки по содержимому.
- Результаты PDF проходят тот же pipeline что и XLSX: сохранение → загрузка как spec → preview_table → Старт → агент.
- `_load_pdf_item_into_spec` — мёртвый код (не вызывается, может пригодиться для прямого логирования).
- Экспорт в xlsx использует "Изготовитель" — теперь он детектится как производитель (см. классификатор колонок) и передаётся агенту отдельно.

### Параметры (config/settings.yaml)
```yaml
pdf_parser:
  lang: east_slavic
  method: auto
  min_chars: 10
  use_llm: false
  llm_max_chars: 3000
  llm_max_tokens: 1024
  llm_temperature: 0.0
  ocr_min_text_length: 100
  review_threshold: 0.8
  timeout: 900
```

## Классификация колонок спецификации

`src/column_classifier.py` — системная замена наивного substring-матчинга `detect_columns` (который ломался на реальных спецификациях: терял «Завод-изготовитель», путал «Код … материала» с наименованием, перекрывал «Единицу измерения» колонкой «Масса единицы (кг)»).

### Принципы
1. **Нормализация** заголовка (lowercase, убрать кавычки, схлопнуть пробелы).
2. **Взвешенная скоринг-модель** — для каждой роли (position/name/spec/article/brand/uom/qty/weight/note) таблица паттернов с весами (3 — сильный, 2 — обычный, 1 — слабый). Суммируются внутри роли.
3. **Валидация по значениям** колонки (сэмпл 50 строк): лексикон ед. изм. (`шт.`, `м`, `м2`, `м.п`, …), целые/десятичные числа, номера позиций (`1.`). Противоречащие значения понижают скор заголовка (числовые значения — не uom; «масса/вес» в заголовке — не uom).
4. **Назначение по лучшей роли**: колонка идёт в роль с максимальным комбинированным скором; одиночные роли (uom/qty/brand/weight/position/note) берут только колонки, для которых они лучшие. Ни одна колонка не теряется молча — неклассифицированные попадают в `unmapped` и логируются при загрузке (`load_spec`).

### Что это даёт
- «Завод-изготовитель» / «Изготовитель» / «Производитель» / «Фирма» / «Бренд» / «Марка» → **brand** (передаётся агенту).
- «Тип, марка, обозначение документа» → **spec** (тип/обозначение, 1404 значения в реальной спецификации — теперь видит агент).
- «Код оборудования, изделия, материала» → **article** (не name).
- «Единица измерения» → uom (не перекрывается «Массой единицы»).
- Производитель **не конкатенируется** в имя/поисковый запрос — он идёт отдельным полем в контекст агента (правило 16 в SYSTEM_PROMPT).

### Прогон на реальной спецификации (1489 позиций)
`name=[1]; article=[3]; brand=[4]; spec=[2]; uom=5; qty=6; weight=7; position=0; note=8` — идеальный маппинг. 780 позиций с производителем, 1404 с типом/обозначением, 115 с артикулом доходят до агента.

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

## Антидетект и браузерная автоматизация (Фаза 3 рефакторинга v2.0)

Реализована на ветке `phase/3-antidetect` (от `refactor/v2.0`).

### stealth.js (17 патчей)
- Патчи 1–12 — базовые (webdriver, plugins, languages, hardware, chrome, permissions, WebGL, screen, connection, platform, mediaDevices, battery).
- Патчи 13–17 — добавлены в Фазе 3: Canvas шум, AudioContext, WebRTC leak prevention, Font enumeration, WebGL vendor/renderer masking.

### HumanBehavior (`src/human_behavior.py`)
- `human_click` — клик в случайную точку элемента + эмуляция mousemove (через `browser_evaluate`; `browser_mouse_move` в @playwright/mcp нет).
- `human_type` — посимвольная печать с переменной скоростью; `human_scroll` — рывками; `random_pause`; `get_random_viewport`.

### DomainRateLimiter (`src/rate_limiter.py`)
- Per-domain: min_interval + RPM-лимит; `wait_if_needed(url)` перед `browser_navigate` в `agent_loop.py`.
- Настройки в `config/settings.yaml → antidetect`.

### SiteAnalyzer (`src/site_analyzer.py`)
- SPA-детекция: `typeof window.__NUXT__` и т.п. (глобальные проверки НЕ через querySelector — невалидные CSS).
- Антибот: cloudflare/recaptcha/hcaptcha/datadome/perimeterx; DOM-статистика; стратегия CAUTIOUS/SPA_AWARE/STANDARD. Профиль кэшируется в памяти по домену.

### CaptchaDetector (`src/captcha_detector.py`)
- Типы: recaptcha_v2/v3, hcaptcha, cloudflare, image, unknown. Рекомендации: SWITCH_SITE/WAIT_60S_AND_RETRY/ASK_USER.
- Детекция по подстрокам HTML (CSS-селекторы дословно в HTML не встречаются). Без авторешения.
- Интеграция в captcha-ветку `agent_loop.py` (тип + рекомендация логируются и сообщаются LLM).

## Эволюция графа знаний (Фаза 4 рефакторинга v2.0)

Реализована на ветке `phase/4-graph` (от `phase/3-antidetect`).

### ApproachVersioning (`src/memory_manager.py`)
- `update_effectiveness(approach_id, success)` — делегирует в `update_approach_success/failure`.
- `get_effective_approaches(site_id, limit=5)` — сортировка по score = `success_rate*0.7 + freshness*0.3` (депрекейтнутые ×0.5). `success_rate` вычисляется на лету из `success_count/(success_count+failures_count)` (колонки в БД нет), добавляется в каждый подход.

### HintManager TTL (`src/memory_manager.py`)
- Колонка `hints.expires_at` (в схеме + миграция ALTER TABLE в `graph_engine._init_db`).
- `create_hint(..., ttl_days=90)` — ставит `expires_at = now + TTL`.
- `get_active_hints(product_type, site=None)` — фильтрует просроченные, опционально по сайту.
- `cleanup_expired()` — удаляет просроченные (`graph_engine.delete_expired_hints`).
- TTL по умолчанию и путь профилей сайтов — в `config/settings.yaml → learning`.

### LearningLoop (`src/learning_loop.py`)
- `consolidate_after_run(results)` вызывается из `MCPAgentRunner` после прогона: агрегирует эффективность подходов, генерирует TTL-хинты для долгих успешных поисков (>60s), обновляет in-memory профили сайтов, сохраняет статистику прогона.
- Профили сайтов (`success_rate`, `avg_attempts`, `block_count`) персистятся в `data/site_profiles.json` и на следующем прогоне подмешиваются в `TaskScheduler.site_profiles` (приоритетнее расчёта по подходам).
- `_extract_patterns` сохраняет подход только если результат содержит реальные `selectors` (в текущем пайплайне нет — подход уже сохранён в `_save_price_and_approach`; иначе были бы «search-only» подходы-мусор).

### SQLite оптимизация (`src/graph_engine.py`)
- `_apply_pragmas()` в `build()`: `synchronous=NORMAL`, `cache_size=-64000` (64MB), `temp_store=MEMORY`. WAL и foreign_keys уже были включены.

## Тестирование (Фаза 7)

- **434 теста**, 0 failures. Запуск: `python -m pytest -q`.
- **Интеграционные** (`tests/integration/test_agent_flow.py`, 9 тестов): полный цикл `process_row` с моками — извлечение, tool_call цикл, reuse (rule 8), semantic cache, ошибки LLM, max rounds, captcha, stuck recovery.
- **Критичные модули >80%**: schemas (96%), stuck_detector (100%), semantic_cache (95%), context_optimizer (100%), rate_limiter (100%), learning_loop (89%), smart_review (100%), config_loader (100%), excel_writer (97%).
- Покрытие: `python -m coverage run --source=src -m pytest tests -q && python -m coverage report` (coverage установлен в venv).
- `pytest-asyncio` установлен в venv — async-тесты (mcp_bridge, pdf_parser, agent_flow) проходят.
- В ходе Фазы 7 исправлены: `SmartReview._calculate_confidence` падал на строковом `qty` (TypeError); `ExcelWriter.detect_columns` fallback добавлял `None`-заголовки в name-колонки.

## Правила сопоставления наименований (Фаза 8)

Три уровня настройки матчинга товара (спецификация vs найденный на сайте) без правки кода:

1. **Конфиг** — `config/matching_rules.yaml`: стоп-слова, структурные, параметрические слова,
   сокращения, контекстные правила (напр. «на грувлоках» незначимо для ВГП-трубы).
   Загружается при старте (`load_rules()` в `main.py`).
2. **GUI-редактор** — кнопка «🧠 Правила сопоставления» в `main.py` → `gui/rules_editor.py`
   (вкладки-таблицы + живая проверка через `product_name_matches`).
3. **Обучение** — подтверждённые LLM-пользователем пары `spec ↔ найденное наименование`
   запоминаются в таблицу `matching_equivalences` графа (`src/graph_engine.py`,
   `src/memory_manager.py`, инлайн-обработчик `save_confirmed_price` в `src/agent_loop.py`).
   Для уже подтверждённой пары предупреждение о несовпадении не показывается.

Правила применяются в `src/approach_relevance.py` (Qt-free, тестируемо); потребители —
`agent_loop.py` (детект несовпадения) и `task_scheduler.py` (фильтр подходов).




