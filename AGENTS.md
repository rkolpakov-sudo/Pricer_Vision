# AGENTS.md — Pricer Vision

Автоматизированный сбор цен на товары у поставщиков (GUI на PySide6 + Playwright MCP + локальный LLM).

## Команды

- **Запуск приложения:** `python main.py`
- **Тесты:** `python -m pytest -q`
  - Известные падения (НЕ связаны с изменениями): 13 тестов в `tests/test_mcp_bridge.py` и `tests/test_pdf_parser.py` падают, т.к. в `venv` не установлен `pytest-asyncio` (async-тесты). Остальной набор должен быть зелёным.
- **Проверка синтаксиса:** `python -m py_compile <files>` (либо запуск pytest).
- Окружения: `venv/` (основное), `mineru_venv/` (изолированный Python 3.11 для MinerU).

## Рабочий процесс

1. Перед изменениями читать `readme.md` (фактическая архитектура) и `state.md` (лог действий/решений) — чтобы не повторять ошибки и не ломать проверенные паттерны.
2. После каждого действия записывать эффект в `state.md`.
3. Правки вносить на **feature-ветках** от `main` (`git checkout -b feat/...`), PR-описание короткое, commit-ы конвенциональные (императив, по-английски: `feat:`, `fix:`, `test:`, `refactor:`).
4. Перед коммитом: `git status`, `git diff`; не коммитить секреты и мусор. `.gitignore` уже покрывает venv/кэши/рантайм-данные.
5. Не добавлять комментарии в код, если об этом не просили.

## Архитектура (кратко)

- `main.py` — точка входа, главное окно (таблицы, toolbar, splitter).
- `src/` — ядро:
  - `agent_loop.py` — основной цикл обработки строк (MCP + graph tools, нативные tool_calls).
  - `mcp_bridge.py` — клиент Playwright MCP (`npx @playwright/mcp --browser chrome`), чтение пина версии из `config/settings.yaml → deps.playwright_mcp.version`.
  - `graph_engine.py` + `memory_manager.py` — граф знаний (SQLite `data/pricer.db`, seed из `config/categories_and_sites.yaml`).
  - `llm_client.py` — HTTP-клиент к локальному LLM (LM Studio/Ollama/llama.cpp).
  - `study_runner.py` — принудительное обучение (StudyRunner QThread).
  - `dependency_manager/` — инструмент «Зависимости» (Qt-free логика в `manager.py`/`npm.py`/`pypi.py`, UI в `dialog.py`). Проверка версий pip+npm и ревизии chromium (`BrowserInfo`) для `@playwright/mcp`.
  - `pdf_parser/` — парсер PDF (MinerU → fallback structurer, LLM отключён).
- `gui/` — `graph_assistant.py` (панели: HelpPage, StudyPage, CRUD), `graph_explorer.py` (визуализация графа), `spinner_widget.py`.
- `mcp_servers/` — MCP-серверы (patchright/pricer), **не используются**.
- `config/` — YAML-конфиги, `stealth.js` (антидетект), `playwright-mcp.json`.
- `tests/` — pytest. Qt-свободная логика покрыта юнит-тестами (`test_dependency_manager.py`, `test_graph_engine.py`, `test_tool_parser.py` и др.).

## Соглашения

- Логика, не требующая Qt, выносится в модули без `PySide6` — так её можно тестировать без QApplication.
- Долгие операции (сеть, subprocess, LLM) — в QThread-воркерах, UI не блокируется.
- Runtime-данные (`data/`, `logs/`, `graph.json`, `*.db`) в git не попадают.
- Пин версии `@playwright/mcp` и обновление chromium выполняются через UI «Зависимости» (не вручную).
