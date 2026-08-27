# AGENTS.md — Pricer Vision

Автоматизированный сбор цен на товары у поставщиков (GUI на PySide6 + Playwright MCP + локальный LLM).

## Команды

- **Запуск приложения:** `python main.py`
- **Тесты:** `python -m pytest -q` (ожидается полный зелёный набор: **960 passed, 2 skipped**; async-тесты mcp_bridge/pdf_parser могут падать, если в `venv` нет `pytest-asyncio`).
- **Проверка синтаксиса:** `python -m py_compile <files>` (либо запуск pytest).
- Окружение: `venv/` (единый, Python 3.13; MinerU 3.4 установлен в нём же).

## Рабочий процесс

1. Перед изменениями читать `readme.md` (фактическая архитектура) и `state.md` (лог действий/решений) — чтобы не повторять ошибки и не ломать проверенные паттерны.
2. После каждого действия записывать эффект в `state.md`.
3. Правки вносить на **feature-ветках** от `main` (`git checkout -b feat/...`), PR-описание короткое, commit-ы конвенциональные (императив, по-английски: `feat:`, `fix:`, `test:`, `refactor:`).
4. Перед коммитом: `git status`, `git diff`; не коммитить секреты и мусор. `.gitignore` уже покрывает venv/кэши/рантайм-данные.
5. Не добавлять комментарии в код, если об этом не просили.

## Регламент коммитов и отката (рефакторинг v2.0)

- **Коммит и тег фаз (`phase-N-done`) — ТОЛЬКО после явного подтверждения пользователем.** Без подтверждения изменения остаются незакоммиченными.
- Ветки фаз: `phase/N-*` от `refactor/v2.0` (или от последней завершённой фазы); базовая точка — тег `v1.0-pre-refactor`.
- Бэкап БД: `data/pricer_backup_20260816.db` (откат БД). Откат кода: `git checkout v1.0-pre-refactor`.
- Прогоны 25 товаров как критерий фазы **не выполняются** — фаза считается завершённой по тестам (`python -m pytest -q`) и ревью кода.

## Архитектура (кратко)

- `main.py` — точка входа, главное окно (таблицы, toolbar, splitter, панель «Режим поиска»).
- `src/` — ядро:
  - `agent_loop.py` — основной цикл обработки строк (MCP + graph tools, нативные tool_calls, `_query_llm` c circuit breaker, StuckDetector, температуры фаз, контекстный бюджет 8000 токенов). Флаги режима: `use_approaches`/`use_site_ranking`; idle-таймаут строки задаёт runner; факты-память RowFacts/SessionFacts; exact-spec реюз ≥0.6; кап confidence по `mismatch_kind` (descriptive-only ≥0.8).
  - `task_scheduler.py` — TaskScheduler: группировка товаров по сайтам, приоритизация батчей (Фаза 2).
  - `semantic_cache.py` — SemanticCache: Jaccard-кэш похожих товаров в `data/semantic_cache.json` (Фаза 2).
  - `session_cache.py` — NegativeCache + SiteBlacklist (сессионный блэклист сайтов; `mark_success` — сайт с ценой не блокируется).
  - `session_facts.py` — RowFacts (память строки, переживает context trim) + SessionFacts (межстрочные факты прогона, гейтинг флагами).
  - `adaptive_limits.py` — AdaptiveRoundManager: динамические лимиты раундов per-site (Фаза 2).
  - `approach_relevance.py` — матчинг наименований + правила сопоставления (Фаза 8); транслитерация брендов, тройные размеры; `model_designators`/`mismatch_kind` (защита реюза от путаницы моделей C10/C20).
  - `human_behavior.py` — HumanBehavior: человеческие клики/печать/скролл (Фаза 3).
  - `rate_limiter.py` — DomainRateLimiter: per-domain RPM лимит перед browser_navigate (Фаза 3).
  - `site_analyzer.py` — SiteAnalyzer: детекция SPA/SSR/антибота (Фаза 3).
  - `captcha_detector.py` — CaptchaDetector: типы captcha + рекомендации, без авторешения (Фаза 3).
  - `mcp_bridge.py` — мультибэкенд-клиент браузерной автоматизации: выбор бэкенда из `config/settings.yaml → browser.backend/backends` (`camoufox` по умолчанию, `playwright`, `nodriver`), автофейловер по цепочке, `mcp_circuit`. Python-бэкенды (`camoufox`/`nodriver`) запускаются через свой venv проекта (`mcp_servers/browser_server.py`), `playwright` — через `npx @playwright/mcp` (пин версии из `deps.playwright_mcp.version`). `_enhance_error` — информативные ошибки Playwright (strict-mode/role-локаторы/ref/fill-таймаут).
  - `graph_engine.py` + `memory_manager.py` — граф знаний (SQLite `data/pricer.db`, seed из `config/categories_and_sites.yaml`).
  - `llm_client.py` — HTTP-клиент к LLM (LM Studio/Ollama/llama.cpp/opencode/routerai), retry с backoff из `llm.retry`, per-call `temperature`/`max_tokens`.
  - `llm_providers.py` — реестр провайдеров (opencode/routerai/локальные); креденшиалы парсятся из системы при каждом запуске (env → opencode auth.json → hermes .env, без хранения секретов в проекте); `create_llm_client()` — фабрика клиента из конфига; списки моделей через `/models` + кэш; `model_id_from_combo_text` (id модели из editable-комбобокса).
  - `study_runner.py` — принудительное обучение (StudyRunner QThread).
  - `learning_loop.py` — автообучение; профили сайтов `(тип|бренд) → site` в `data/site_profiles.json`, `rank_sites()` (MIN_SAMPLES=3).
  - `resilience.py` — CircuitBreaker (`llm_circuit`/`mcp_circuit`), `retry_with_backoff` (Фаза 1).
  - `models/schemas.py` — Pydantic-схемы: `ExtractionResult`, `AgentDecision`, `ExtractedPrice` (Фаза 1).
  - `stuck_detector.py` — StuckDetector: зацикливание/блокировки (Фаза 1).
  - `audit_logger.py` — Audit-лог JSONL в `data/audit/` (Фаза 1).
  - `dependency_manager/` — инструмент «Зависимости» (Qt-free логика в `manager.py`/`npm.py`/`pypi.py`, UI в `dialog.py`). Проверка версий pip+npm и ревизии chromium (`BrowserInfo`) для `@playwright/mcp`.
  - `pdf_parser/` — парсер PDF (MinerU → fallback structurer, LLM отключён).
- `gui/` — `graph_assistant.py` (панели: HelpPage, StudyPage, CRUD), `graph_explorer.py` (визуализация графа), `agent_monitor.py` (мониторинг), `metrics_panel.py`, `spinner_widget.py`.
- `mcp_servers/` — `browser_server.py` (MCP-сервер бэкендов camoufox/nodriver, используется), `pricer_server.py` (**не используется**).
- `config/` — YAML-конфиги (`settings.yaml` → `browser.backend/backends`, `run.reuse_price/use_approaches/use_site_ranking`), `matching_rules.yaml`, `stealth.js` (антидетект), `playwright-mcp.json`.
- `tests/` — pytest. Qt-свободная логика покрыта юнит-тестами (`test_dependency_manager.py`, `test_graph_engine.py`, `test_approach_relevance.py`, `test_main.py`, `test_row_idle_timeout.py` и др.).

## Соглашения

- Логика, не требующая Qt, выносится в модули без `PySide6` — так её можно тестировать без QApplication.
- Долгие операции (сеть, subprocess, LLM) — в QThread-воркерах, UI не блокируется.
- Runtime-данные (`data/`, `logs/`, `graph.json`, `*.db`) в git не попадают.
- Пин версии `@playwright/mcp` и обновление chromium выполняются через UI «Зависимости» (не вручную).
