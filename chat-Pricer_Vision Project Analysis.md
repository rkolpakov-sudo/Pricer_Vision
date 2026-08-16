# 📋 ПЛАН РЕФАКТОРИНГА PRICER_VISION v2.0

## Комплексная программа модернизации с учётом ограничений локальной LLM

---

## 🎯 ЦЕЛИ РЕФАКТОРИНГА

| Метрика | Текущая | Целевая | Прирост |
|---------|---------|---------|---------|
| Точность извлечения | 76% | 92% | +16% |
| Стабильность (без крэшей) | ~85% | 99% | +14% |
| Время обработки 25 товаров | ~30 мин | ~20 мин | -33% |
| Частота банов на сайтах | Высокая | Низкая | -60% |
| Покрытие PDF-форматов | ~60% | 95% | +35% |
| Потраченные впустую раунды | ~30% | <10% | -67% |

### Фундаментальные принципы:

1. **Локальная LLM — единая точка.** Никаких параллельных запросов к модели. Вся оптимизация строится вокруг минимизации количества и контекста запросов.
2. **Последовательная обработка с умным планированием.** Группировка, кэширование, переиспользование знаний вместо параллелизма.
3. **Каждая фаза — атомарна.** Завершение фазы = запись в `state.md` + тест пользователем + **обязательный коммит ПОСЛЕ подтверждения пользователем** (см. «Регламент коммитов и отката»).
4. **Откат в любой момент.** Каждая фаза — отдельная git-ветка от `refactor/v2.0`; тег-контрольная точка `phase-N-done` после каждого подтверждённого коммита; возврат к текущему состоянию — `main` (тег `v1.0-pre-refactor`).

---

## 🗓 ОБЩАЯ ДОРОЖНАЯ КАРТА

```
Неделя 1          Неделя 2          Неделя 3          Неделя 4
┌─────────┐      ┌─────────┐      ┌─────────┐      ┌─────────┐
│ ФАЗА 1  │ ──►  │ ФАЗА 2  │ ──►  │ ФАЗА 3  │ ──►  │ ФАЗА 4  │
│Стабильн.│      │Оптим.   │      │Антидетект│     │Граф     │
│ядра     │      │LLM-цикла│      │браузера │      │знаний   │
└─────────┘      └─────────┘      └─────────┘      └─────────┘

Неделя 5          Неделя 6          Неделя 7
┌─────────┐      ┌─────────┐      ┌─────────┐
│ ФАЗА 5  │ ──►  │ ФАЗА 6  │ ──►  │ ФАЗА 7  │
│PDF-     │      │GUI и    │      │Тесты и  │
│парсер   │      │мониторинг│     │документ.│
└─────────┘      └─────────┘      └─────────┘
```

---

## 🔍 СВЕРКА ПЛАНА С РЕАЛЬНЫМ КОДОМ (базовое состояние 2026-08-16)

> ⚠️ **Важно:** примеры кода в фазах 1–7 — эскизы. Перед реализацией каждой фазы сверяться с разделом ниже, чтобы не использовать API, которых в проекте нет.

### 1. Реальный состав модулей

| Путь | Реальность |
|------|-----------|
| `src/agent_loop.py` | **НЕТ класса `AgentLoop`.** Это модуль с функциями: `async def process_row(spec_text, llm_client, mcp_bridge, graph_engine, memory_manager, stop_event, status_callback, fresh, spec_meta) -> dict`. Маршрутизация: `GRAPH_TOOL_NAMES` → `_execute_graph_tool`; `browser_navigate`/`navigate` → MCP; иначе → MCP. Константы: `MAX_ROUNDS` (settings `run.max_rounds`, 50), `MAX_ROUNDS_PER_SITE` (15), `SUMMARIZE_MAX_CHARS/LINES`, `CAPTCHA_KEYWORDS`, `SEARCH_ENGINE`. |
| `src/mcp_bridge.py` | Класс `MCPBridge`. Метод вызова — **`async call_tool(tool_name, arguments) -> str`** (не `execute_tool`). `list_tools()`, `health_check()`, `restart()`, `set_headless()`, `start()`, `stop()`. Реальные инструменты: `browser_navigate`, `browser_snapshot`, `browser_type`, `browser_click`, `browser_evaluate`, `browser_press_key`, `browser_wait_for`, `browser_take_screenshot`, `browser_tabs`, `browser_close`, `browser_fill_form`, `browser_hover`, `browser_drag`, `browser_drop`, `browser_select_option`, `browser_network_requests` и др. `browser_evaluate` принимает **`{"function": ...}`**, а НЕ `{"expression": ...}`. |
| `src/llm_client.py` | Класс `LLMClient`. Метод — **`async chat(messages, tools=None, force_json=False) -> dict`**. **НЕ принимает `model`, `max_tokens`, `temperature` на вызов** — температура и модель задаются в конструкторе; `max_tokens` захардкожен (8192). Fallback: LM Studio → Ollama → llama.cpp. Retry внутри — только перебор URL, **без backoff-пауз** (поле `llm.retry` в settings.yaml НЕ читается). |
| `src/graph_engine.py` | Класс `GraphEngine(db_path)`. **НЕТ `get_site()`, `get_sites_for_category()`, `get_default_site_id()`, `upsert_pattern()`, `update_site_profile()`.** Реальные методы: `get_approaches`, `get_approaches_by_site`, `get_all_approaches`, `get_best_approach`, `save_approach`, `update_approach_success/failure`, `get_confirmed_prices`, `save_confirmed_price`, `get_hints`, `save_hint`, `get_sites_for_product`, `save_discovered_site`, `save_product_type`, `delete_*`, `classify_product_type`, `load_yaml_seed`, `get_stats`, `get_cached_categories`, `get_all_products/sites/hints/confirmed_prices`, CRUD. |
| `src/memory_manager.py` | `MemoryManager(engine)` — прослойка. **НЕТ атрибутов `approach_versioning`, `hint_manager`.** Реальные методы: `get_best_approach`, `get_all_approaches`, `get_site_approaches`, `save_approach`, `record_success`, `record_failure`, `get_relevant_prices`, `save_price`, `get_hints`, `add_hint`, `get_sites`, `add_site`, `record_soldat`, `save_concept_edge`, CRUD. |
| `src/tool_parser.py` | `parse_tool_calls`, `parse_final_response`, `parse_text_tools`, `parse_text_result` — парсинг **нативных tool_calls**, не текстовых «решений». |
| `src/validator.py` | `validate_result(result: dict, spec_text) -> dict` — пост-валидация. Пороги: `CONF_MIN=0.6`, `CONF_GOOD=0.8`. |
| `src/study_runner.py` | `StudyRunner(QThread)` — 50 раундов, предложение подходов/хинтов/концептов на утверждение. |
| `src/pdf_parser/` | `runner.py` (`PdfParserRunner(QThread)`), `structurer.py` (`SpecStructurer`, LLM отключён — только `_fallback_parse`), `mineru_backend.py` (`MinerUBackend`), `feedback.py` (`FeedbackCollector`, таблица `pdf_corrections`), `review_dialog.py` (`ReviewDialog`), `prompts.py`. |
| `src/dependency_manager/` | 9 модулей: `manager`, `dialog`, `npm`, `pypi`, `models`, `worker`, `envs`, `requirements`, `versioning`. |
| `src/site_order_dialog.py` | **МЁРТВЫЙ КОД**: импортирует несуществующий `src.category_router` → ImportError при импорте. Нигде не используется. |
| `src/theme.py`, `src/toast.py`, `src/widget_base.py` | **лежат в `src/`, а не в `gui/`**. |
| `gui/` | `graph_assistant.py` (`AssistantToolPanel`, 11 страниц: SearchPage, ContextPage, HintPage, CorrectionPage, StatsPage, ProductTypePage, SitePage, ApproachPage, PricePage, StudyPage, HelpPage), `graph_explorer.py` (`GraphExplorerWidget` + QGraphicsScene), `spinner_widget.py`. |
| `mcp_servers/` | `patchright_server.py`, `pricer_server.py` — **не используются**. |
| `tests/` | 10 файлов: `test_agent_loop`, `test_dependency_manager`, `test_graph_engine`, `test_llm_client`, `test_mcp_bridge`, `test_memory_manager`, `test_pdf_parser`, `test_tool_parser`, `test_validator` + `conftest.py`. |

### 2. Реальная схема БД (`data/pricer.db`)

```sql
product_types(id TEXT PK, name, category, keywords, created_at)
sites(id TEXT PK, name, base_url, group_name, source DEFAULT 'yaml', created_at)  -- НЕТ колонки category!
product_sites(product_type_id, site_id, priority DEFAULT 0, consecutive_failures DEFAULT 0)
approaches(id INTEGER PK AUTOINCREMENT, product_type_id, site_id, pattern TEXT, concrete TEXT,
           selectors_cache, param_slots, method, search_query,
           success_count DEFAULT 1, failures_count DEFAULT 0, consecutive_failures DEFAULT 0,
           cooldown_until, is_deprecated DEFAULT 0, last_success_date, last_failure_date,
           notes, created_at)                      -- НЕТ success_rate / status / total_successes
confirmed_prices(id PK, spec_text, product_type_id, site_id, price REAL, currency DEFAULT 'RUB',
                 url, confidence DEFAULT 0.95, source DEFAULT 'agent', reason, created_at)
hints(id PK, product_type_id, site_id, hint_text, priority DEFAULT 0.5, created_at)  -- НЕТ TTL/confidence/content
concepts(name PK, description, source, created_at)
concept_edges(child_name, parent_name, relation, weight, created_at)
pdf_corrections(id PK, original_text, corrected_text, correction_type, apply_count, created_at, updated_at)
```

Индексы, которые уже есть: `idx_approaches_product_site`, `idx_approaches_site`, `idx_confirmed_spec`, `idx_confirmed_product`, `idx_hints_product`.
**Вывод:** фаза 4 должна **не менять** существующие таблицы радикально, а добавлять TTL/success_rate **новыми колонками** или отдельными таблицами. НЕТ таблиц `revalidation_queue` — их создавать.

### 3. Конфигурация (`config/settings.yaml`)

- Секции: `browser`, `deps.playwright_mcp.version`, `limits`, `llm` (включая `retry`), `paths`, `pdf_parser`, `price`, `run` (max_rounds=50, max_rounds_per_site=15, max_study_rounds=50, study_temperature=0.5, main_temperature=0.3, fresh, summarize_max_chars=800), `ui.theme`, `vision_search`.
- **`vision_search` нигде в коде не читается** (мёртвая конфигурация).
- `deps.playwright_mcp.version` — пинается через UI «Зависимости».
- Загрузчик: `config_loader.py` — кэш `_SETTINGS_CACHE`, helpers `get_run_config`, `get_price_config`.

### 4. Ключевые несоответствия эскизов в фазах

| Где в плане | Что написано | Как в реальности |
|-------------|-------------|------------------|
| Фаза 1.1 | `class AgentLoop`, `_parse_llm_response` | Нет класса. LLM возвращает **нативные tool_calls**, не текстовое «решение». Валидировать нужно `parse_tool_calls`/`parse_final_response`, а не `AgentDecision`. |
| Фаза 1.3 | `MCPBridge.execute_tool(...)` | `async call_tool(tool_name, arguments)`. |
| Фаза 1.4 | `@retry_with_backoff` для LLM | retry уже есть внутри `LLMClient.chat` (перебор URL), но без пауз. `llm.retry` из yaml не подключён. |
| Фаза 2.1 | `graph.get_site`, `get_sites_for_category`, `get_default_site_id`, `site_info['url']` | Нет таких методов. Реально: `memory_manager.get_sites(product_type)` → `[{"id","name","base_url","priority","consecutive_failures"}]`. |
| Фаза 2.2 | `result.found`, `result.dict()` | Результаты — **dict**: `{"spec_text","price","confidence","url","site","reason","requires_review","elapsed","product_type"}`. |
| Фаза 2.3 | `site_profile['success_rate']`, `has_antibot`, `is_spa`, `avg_dom_depth` | В БД нет этих полей. Могут быть только производными от `consecutive_failures`/`success_count`. |
| Фаза 2.4 | `llm_client.chat(messages=..., temperature=..., max_tokens=...)` | Клиент НЕ принимает temperature/max_tokens на вызов. Требуется доработка клиента или пересоздание LLMClient с нужной температурой. |
| Фаза 3.2–3.5 | `mcp_bridge.execute_tool("browser_evaluate", {"expression": ...})` | Параметр называется `function`, метод `call_tool`. `browser_mouse_move` — НЕ существует в реальном наборе инструментов. |
| Фаза 4.1–4.4 | `self.db.get_approach`, `update_approach`, `insert_hint`, `get_approaches_for_site`, `graph.upsert_pattern`, `update_site_profile`, `success_rate` | Реально: `get_best_approach`, `update_approach_success/failure`, `save_hint`, `get_approaches_by_site`. |
| Фаза 4.4 | PRAGMA WAL/индексы | WAL **уже включён** (`build()`), индексы уже есть. Добавлять новые индексы на колонки `status`/`product_id` нельзя (их нет). |
| Фаза 5.1 | `self.llm.chat(messages=..., model=..., max_tokens=..., temperature=...)` | Нет per-call model/max_tokens/temperature. LLM в structurer отключён. |
| Фаза 5.2 | `PaddleOCR`, `fitz`, Tesseract | В requirements.txt НЕТ paddleocr/pymupdf/tesseract. Реальный PDF-бэкенд — `MinerUBackend`. |
| Фаза 5.3 | `extracted_data.get('rows')`, `row.get('cells')` | Реальный формат позиции: `{"pos","name","specs","code","manufacturer","qty","unit","weight"}`. |
| Фаза 6.2 | `graph_explorer.get_viewport()`, `draw_node`, `draw_edge`, `draw_point` | Нет таких методов. Реально — QGraphicsScene/View, `_update_edges` (с известным багом приоритета). |
| Фаза 7.2 | `AgentLoop(llm_client=..., mcp_bridge=...)`, `agent.run(...)`, `result.found` | Интеграционный тест должен вызывать `process_row(...)` с моками. |

### 5. Критичные технические факты

- **WAL mode уже включён** в `GraphEngine.build()` (`PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`).
- Агентный цикл: **три ветки маршрутизации**: `GRAPH_TOOL_NAMES` → `_execute_graph_tool(...)` (локально, без LLM), `browser_navigate`/`navigate` → MCP, всё остальное → MCP.
- Правило 8 (reuse): при `fresh=False` и `confidence>=0.9` цена берётся из БД без LLM — уже реализовано.
- Captcha-обработка уже есть: `CAPTCHA_KEYWORDS`, `_deprecate_site_approaches`, принудительный сброс `current_site`.
- Адаптивные лимиты раундов уже частично есть: `site_round_limits` (5 раундов при `consecutive_failures>=3`).
- `study_runner.py` дублирует `agent_loop.py` (~40%: GRAPH_TOOL_DEFS, маршрутизация, запись шагов, `_clean_snapshot`, саммаризация).
- База `pytest`: `python -m pytest -q` → **154 passed** (13 тестов в `test_mcp_bridge`/`test_pdf_parser` падают без `pytest-asyncio` в venv).
- Git: `main` = `b0151c7`, тег `v0.1.0`, untracked-файл — этот документ; `config/settings.yaml` — модифицирован (показать diff пользователю).
- `graph_explorer.py:310` — баг приоритета в `_update_edges` (bounds-check пропускается при `u == idx`).
- Тема захардкожена на `Theme.DARK` в `toast.py`, `widget_base.py`, `site_order_dialog`, dependency-диалоге.

---

## ✅ РЕГЛАМЕНТ КОММИТОВ И ОТКАТА (обязательный)

### Зачем
Чтобы иметь возможность откатиться к текущему состоянию проекта и к любой фазе в случае неудачного рефакторинга.

### Правила
1. **Точка отсчёта:** `main` = `b0151c7` (тег `v0.1.0`). В фазе 0 создаётся тег `v1.0-pre-refactor` — **полный откат = `git checkout v1.0-pre-refactor`** (или `git checkout main` до слияния фаз).
2. **Каждая фаза — отдельная ветка** от `refactor/v2.0`: `phase/0-prep`, `phase/1-core`, `phase/2-llm`, `phase/3-antidetect`, `phase/4-graph`, `phase/5-pdf`, `phase/6-gui`, `phase/7-tests`.
3. **Обязательный коммит ПОСЛЕ подтверждения пользователя.** Последовательность завершения фазы:
   - Реализация на ветке `phase/N-...` → прогон тестов (`python -m pytest -q`) → запись результата в `state.md`.
   - **Пользователь проверяет работу** (прогон 25 товаров / целевой тест фазы).
   - **Пользователь подтверждает** → `git add -A && git commit -m "phase(N): <краткое описание>"` → `git tag phase-N-done`.
   - Коммит **без подтверждения пользователя — запрещён** (фаза считается незавершённой).
4. **Откат к любой фазе:**
   - К текущему состоянию: `git checkout v1.0-pre-refactor` (или `main` до слияний).
   - К фазе N: `git checkout phase-N-done`.
   - После слияния ветки в `refactor/v2.0` — `git revert <commit>` или `git checkout phase-N-done` + cherry-pick.
5. **Слияние:** ветка фазы мержится в `refactor/v2.0` только после подтверждения и тега `phase-N-done` (fast-forward или merge --no-ff).
6. **Бэкап БД** перед каждой фазой: `copy data/pricer.db data/pricer_backup_<фаза>_<дата>.db` (БД не в git).
7. Commit-стиль: конвенциональный, по-английски, императив (например `feat(v2.0): add Pydantic schemas for LLM output validation`).

---

## ФАЗА 0: ПОДГОТОВКА И БЕЗОПАСНОСТЬ (1 день)

### Задачи:

**0.1. Аудит секретов**
- Проверить `settings.yaml`, `playwright-mcp.json`, все `.py` файлы на наличие захардкоженных ключей/паролей
- Вынести всё в `.env` файл
- Обновить `.gitignore`:
```gitignore
# Секреты
.env
*.key
*.pem

# Данные
data/pricer.db
data/output/

# Виртуальные окружения
.venv/
venv/

# IDE
.vscode/
.idea/
__pycache__/
*.pyc
```

**0.2. Создание `.env` шаблона**

> ⚠️ Реальность: текущий проект НЕ использует `.env` — настройки LLM лежат в `config/settings.yaml` (`llm.model: local-model`, детектируется из LM Studio/Ollama/llama.cpp). `.env` вводится впервые; LLMClient при этом не умеет читать env — нужен мост в `config_loader.py` или `llm_client.py`. Модель в эскизе ниже — пример, реальная задаётся в settings.yaml.

```env
# .env.example
LLM_BASE_URL=http://localhost:1234/v1/chat/completions
LLM_API_KEY=lm-studio
LLM_MODEL=local-model

MINERU_PYTHON_PATH=/path/to/mineru/python
MINERU_MODEL_PATH=/path/to/models

OUTPUT_DIR=./data/output
DB_PATH=./data/pricer.db
```

**0.3. Бэкап текущего состояния**
```bash
git checkout main
git tag v1.0-pre-refactor                     # точка отката к текущему состоянию
git checkout -b refactor/v2.0
copy data/pricer.db data\pricer_backup_20260816.db   # БД не в git — бэкапим вручную
git add -A && git commit -m "chore(v2.0): baseline before refactoring"   # ОБЯЗАТЕЛЬНЫЙ коммит точки отсчёта
```

**0.4. Валидация окружения**
- Проверить, что `stealth.js` загружается корректно
- Проверить, что MCP-мост стартует без ошибок
- Проверить, что LM Studio отвечает на тестовый запрос

### Критерий завершения:
- Нет секретов в git-репозитории
- `.env` файл создан и работает
- Все существующие функции работают как до рефакторинга

### Запись в `state.md`:
```
## 2026-08-16 — Подготовка к рефакторингу v2.0
- Проведён аудит секретов, всё вынесено в .env
- Создана ветка refactor/v2.0
- Бэкап БД создан
- Тег v1.0-pre-refactor установлен
```

---

## ФАЗА 1: СТАБИЛЬНОСТЬ И НАДЁЖНОСТЬ ЯДРА (Неделя 1)

### Цель: Исключить крэши, зависания и потерю данных. Точность 76% → 82%.

### Задачи:

**1.1. Pydantic-валидация вывода LLM**

> ⚠️ **Корректировка:** в реальном коде НЕТ класса `AgentLoop` и НЕТ текстовых «решений» LLM. Цикл — `async def process_row(...)` в `src/agent_loop.py`; LLM возвращает **нативные tool_calls** (парсятся `parse_tool_calls`/`parse_final_response` из `src/tool_parser.py`). Ниже — исправленные эскизы: схемы валидируют **финальный результат** (`ExtractionResult`) и **аргументы graph-инструментов**, а не «действие агента».

*Файлы:* `src/models/schemas.py` (новый), `src/agent_loop.py` (модификация)

```python
# src/models/schemas.py
from pydantic import BaseModel, Field, field_validator
from typing import Optional, List
from enum import Enum

class ActionType(str, Enum):
    NAVIGATE = "navigate"
    CLICK = "click"
    TYPE = "type"
    SCROLL = "scroll"
    EXTRACT = "extract"
    SWITCH_SITE = "switch_site"
    ASK_USER = "ask_user"

class AgentDecision(BaseModel):
    """Валидированное решение агента (для будущего рефакторинга на AgentDecision;
    сейчас цикл работает с tool_calls напрямую)."""
    reasoning: str = Field(..., min_length=10, description="Обоснование действия")
    action: ActionType
    target: Optional[str] = None
    value: Optional[str] = None
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator('target')
    @classmethod
    def validate_target(cls, v, info):
        action = info.data.get('action')
        if action in (ActionType.CLICK, ActionType.TYPE) and not v:
            raise ValueError(f"Action {action} requires target")
        return v

class ExtractedPrice(BaseModel):
    """Валидированная извлечённая цена"""
    product_name: str
    price: float = Field(..., gt=0)
    currency: str = "RUB"
    url: str
    confidence: float = Field(default=0.5, ge=0.0, le=1.0)
    raw_text: Optional[str] = None

    @field_validator('price')
    @classmethod
    def validate_price(cls, v):
        if v > 10_000_000:  # Защита от галлюцинаций (совпадает с PRICE_ANOMALY_HIGH в validator.py)
            raise ValueError(f"Unrealistic price: {v}")
        return round(v, 2)

class ExtractionResult(BaseModel):
    """Результат извлечения для одного товара — контракт process_row"""
    spec_text: str
    product_type: str = "unknown"
    found: bool
    price: Optional[float] = None
    confidence: float = 0.0
    url: str = ""
    site: str = ""
    reason: str = ""
    requires_review: bool = True
    error: Optional[str] = None
    elapsed: Optional[float] = None
```

*Интеграция в `agent_loop.py`:*
```python
from src.models.schemas import ExtractionResult

# Валидация финального результата перед return (после validate_result):
# process_row возвращает dict — переводим его в ExtractionResult для строгой схемы,
# но наружу отдаём .model_dump() чтобы не ломать MCPAgentRunner/ExcelWriter.
def _result_to_schema(result: dict) -> dict:
    try:
        model = ExtractionResult(
            spec_text=result.get("spec_text", ""),
            product_type=result.get("product_type", "unknown"),
            found=result.get("price") is not None,
            price=result.get("price"),
            confidence=result.get("confidence", 0.0),
            url=result.get("url", ""),
            site=result.get("site", ""),
            reason=result.get("reason", ""),
            requires_review=result.get("requires_review", True),
            error=result.get("error"),
            elapsed=result.get("elapsed"),
        )
        return model.model_dump()
    except Exception as e:
        logger.warning(f"Schema validation failed: {e}")
        return result
```

**1.2. StuckDetector — обнаружение зацикливания**

*Файл:* `src/stuck_detector.py` (новый)

```python
# src/stuck_detector.py
from collections import deque
from dataclasses import dataclass
from enum import Enum
from typing import List, Optional

class StuckLevel(Enum):
    OK = "ok"
    WARNING = "warning"       # Повторяющиеся действия
    CRITICAL = "critical"     # Полный тупик
    BLOCKED = "blocked"       # Обнаружена блокировка

@dataclass
class ActionRecord:
    action_type: str
    target: str
    result: str  # "success", "error", "no_change"
    timestamp: float

class StuckDetector:
    def __init__(self, window_size: int = 5, repeat_threshold: int = 3):
        self.history = deque(maxlen=window_size)
        self.repeat_threshold = repeat_threshold
        self.no_progress_count = 0
    
    def record_action(self, action_type: str, target: str, result: str):
        import time
        self.history.append(ActionRecord(
            action_type=action_type,
            target=target,
            result=result,
            timestamp=time.time()
        ))
        
        if result == "no_change":
            self.no_progress_count += 1
        else:
            self.no_progress_count = 0
    
    def detect(self) -> StuckLevel:
        if len(self.history) < self.repeat_threshold:
            return StuckLevel.OK
        
        # Проверка на блокировку (CAPTCHA, 403, Cloudflare)
        if self._detect_block():
            return StuckLevel.BLOCKED
        
        # Проверка на циклические действия
        recent = list(self.history)[-self.repeat_threshold:]
        action_signatures = [f"{a.action_type}:{a.target}" for a in recent]
        
        if len(set(action_signatures)) == 1:
            return StuckLevel.CRITICAL
        
        # Проверка на отсутствие прогресса
        if self.no_progress_count >= self.repeat_threshold:
            return StuckLevel.WARNING
        
        return StuckLevel.OK
    
    def _detect_block(self) -> bool:
        """Обнаружение блокировки сайта"""
        block_indicators = [
            "captcha", "verify", "blocked", "access denied",
            "403", "cloudflare", "attention required"
        ]
        for record in self.history:
            if any(ind in record.result.lower() for ind in block_indicators):
                return True
        return False
    
    def suggest_recovery(self, level: StuckLevel) -> List[str]:
        """Предложения по выходу из тупика"""
        strategies = {
            StuckLevel.WARNING: [
                "REFRESH_PAGE",
                "TRY_ALTERNATIVE_SELECTOR",
                "SCROLL_AND_RETRY"
            ],
            StuckLevel.CRITICAL: [
                "SWITCH_SITE",
                "ASK_USER_HINT",
                "SKIP_PRODUCT"
            ],
            StuckLevel.BLOCKED: [
                "WAIT_AND_RETRY",      # Подождать 30-60 сек
                "SWITCH_SITE",         # Немедленно сменить сайт
                "REPORT_BLOCK"         # Записать в граф как блокировку
            ]
        }
        return strategies.get(level, [])
```

*Интеграция в `agent_loop.py`:*
> ⚠️ **Корректировка:** в реальном цикле нет класса `AgentLoop`/`run(product)`. StuckDetector встраивается внутрь `async def process_row(...)`: вызывается после каждого MCP-шага, использует `tool_name`+`target` как `action_type`, результат шага — как `result` («success»/«no_change»). Блокировка определяется существующей captcha-логикой (`CAPTCHA_KEYWORDS` + `_deprecate_site_approaches`) — детектор её дублировать не должен.

```python
# В process_row: после выполнения каждого tool_calls (строка ~509, после steps.append)
stuck_detector.record_action(
    action_type=tool_name,
    target=str(tool_args.get("target") or tool_args.get("url") or ""),
    result="success" if not str(result).startswith("error:") else "no_change",
)

stuck_level = stuck_detector.detect()
if stuck_level == StuckLevel.BLOCKED:
    # уже обрабатывается CAPTCHA_KEYWORDS — не дублировать
    pass
elif stuck_level == StuckLevel.CRITICAL and rounds_on_site > 5:
    # принудительный уход с сайта (существующая логика site_round_limits)
    logger.warning("StuckDetector CRITICAL — forcing site switch")
    current_site = ""
    rounds_on_site = site_round_limits.get(_extract_domain(current_site), MAX_ROUNDS_PER_SITE) + 1
    stuck_detector.reset()  # сброс после принудительного переключения
```

**1.3. Circuit Breaker для MCP и LLM**

*Файл:* `src/resilience.py` (новый)

```python
# src/resilience.py
import time
import logging
from enum import Enum
from functools import wraps

logger = logging.getLogger(__name__)

class CircuitState(Enum):
    CLOSED = "closed"       # Нормальная работа
    OPEN = "open"           # Отказ, запросы блокируются
    HALF_OPEN = "half_open" # Проверка восстановления

class CircuitBreaker:
    def __init__(self, failure_threshold=5, recovery_timeout=60, 
                 expected_exception=Exception):
        self.failure_threshold = failure_threshold
        self.recovery_timeout = recovery_timeout
        self.expected_exception = expected_exception
        
        self.failure_count = 0
        self.last_failure_time = None
        self.state = CircuitState.CLOSED
    
    def call(self, func, *args, **kwargs):
        if self.state == CircuitState.OPEN:
            if time.time() - self.last_failure_time > self.recovery_timeout:
                self.state = CircuitState.HALF_OPEN
                logger.info("Circuit breaker: attempting recovery")
            else:
                raise CircuitBreakerOpenError(
                    f"Service unavailable. Retry after {self.recovery_timeout}s"
                )
        
        try:
            result = func(*args, **kwargs)
            self._on_success()
            return result
        except self.expected_exception as e:
            self._on_failure()
            raise
    
    def _on_success(self):
        self.failure_count = 0
        if self.state == CircuitState.HALF_OPEN:
            self.state = CircuitState.CLOSED
            logger.info("Circuit breaker: service recovered")
    
    def _on_failure(self):
        self.failure_count += 1
        self.last_failure_time = time.time()
        
        if self.failure_count >= self.failure_threshold:
            self.state = CircuitState.OPEN
            logger.error(f"Circuit breaker OPEN: {self.failure_count} failures")

class CircuitBreakerOpenError(Exception):
    pass

# Готовые инстансы
llm_circuit = CircuitBreaker(failure_threshold=3, recovery_timeout=30)
mcp_circuit = CircuitBreaker(failure_threshold=5, recovery_timeout=60)
```

*Интеграция:*
> ⚠️ **Корректировка:** реальный метод — `async call_tool(tool_name, arguments) -> str`, а не `execute_tool`. `call_tool` уже содержит `try/except` и возвращает `"error: ..."` строкой — Circuit Breaker оборачивает вызовы **снаружи**, не дублируя обработку. `_queue_request`/`ServiceUnavailableError` — новых концепций нет, используем готовые `health_check()`/`restart()` из MCPBridge.

```python
# В mcp_bridge.py
from src.resilience import mcp_circuit, CircuitBreakerOpenError

class MCPBridge:
    async def call_tool(self, tool_name, arguments):   # существующий метод
        try:
            return await mcp_circuit.call(
                self._call_tool_raw, tool_name, arguments   # переименовать тело существующего метода
            )
        except CircuitBreakerOpenError:
            logger.error("MCP unavailable, will restart...")
            await self.restart()     # вместо _queue_request
            raise ServiceUnavailableError("MCP server is recovering")

# В agent_loop.py (обёртка вокруг llm_client.chat)
from src.resilience import llm_circuit

async def _query_llm(llm_client, messages, tools):
    try:
        return await llm_circuit.call(llm_client.chat, messages=messages, tools=tools)
    except CircuitBreakerOpenError:
        logger.error("LLM unavailable, pausing agent...")
        await asyncio.sleep(30)
        raise
```

**1.4. Retry с exponential backoff**

*Файл:* `src/resilience.py` (дополнение)

```python
import random

def retry_with_backoff(max_retries=3, base_delay=1.0, max_delay=30.0,
                       exceptions=(Exception,)):
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            for attempt in range(max_retries):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_retries - 1:
                        raise
                    
                    delay = min(base_delay * (2 ** attempt), max_delay)
                    jitter = random.uniform(0, delay * 0.1)
                    wait_time = delay + jitter
                    
                    logger.warning(
                        f"Attempt {attempt + 1} failed: {e}. "
                        f"Retrying in {wait_time:.1f}s..."
                    )
                    time.sleep(wait_time)
            
            raise MaxRetriesExceeded(f"Failed after {max_retries} attempts")
        return wrapper
    return decorator

# Использование:
@retry_with_backoff(max_retries=3, exceptions=(TimeoutError, ConnectionError))
async def navigate_to_site(url):
    return await mcp_bridge.call_tool("browser_navigate", {"url": url})
```

**1.5. Audit Logging**

*Файл:* `src/audit_logger.py` (новый)

```python
# src/audit_logger.py
import json
import uuid
from datetime import datetime
from pathlib import Path

class AuditLogger:
    def __init__(self, log_dir="data/audit"):
        self.log_dir = Path(log_dir)
        self.log_dir.mkdir(parents=True, exist_ok=True)
        self.session_id = str(uuid.uuid4())[:8]
        self.log_file = self.log_dir / f"session_{self.session_id}.jsonl"
    
    def log(self, event_type: str, details: dict, **extra):
        entry = {
            "timestamp": datetime.now().isoformat(),
            "session_id": self.session_id,
            "event_type": event_type,
            "details": details,
            **extra
        }
        
        with open(self.log_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")
    
    def log_llm_request(self, messages, response, duration_ms):
        self.log("LLM_REQUEST", {
            "message_count": len(messages),
            "response_length": len(response),
            "duration_ms": duration_ms
        })
    
    def log_browser_action(self, action, target, result):
        self.log("BROWSER_ACTION", {
            "action": action,
            "target": target,
            "result": result
        })
    
    def log_extraction(self, product_name, found, price=None):
        self.log("EXTRACTION", {
            "product": product_name,
            "found": found,
            "price": price
        })
    
    def get_session_summary(self):
        """Генерирует сводку по сессии для state.md"""
        events = self._read_events()
        return {
            "total_llm_calls": sum(1 for e in events if e["event_type"] == "LLM_REQUEST"),
            "total_browser_actions": sum(1 for e in events if e["event_type"] == "BROWSER_ACTION"),
            "extractions": [e for e in events if e["event_type"] == "EXTRACTION"],
            "avg_llm_duration": self._calc_avg_duration(events)
        }
```

### Критерий завершения Фазы 1:
- [ ] Все ответы LLM валидируются через Pydantic
- [ ] StuckDetector обнаруживает циклы и блокировки
- [ ] Circuit Breaker предотвращает каскадные отказы
- [ ] Retry с backoff работает для сетевых операций
- [ ] Все действия логируются в audit trail
- [ ] **Тест:** Прогон 25 товаров без единого крэша, точность ≥ 82%

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 1: Стабильность ядра
- Добавлена Pydantic-валидация (schemas.py)
- StuckDetector: обнаружение циклов и блокировок
- Circuit Breaker для MCP и LLM
- Retry с exponential backoff
- Audit logging (data/audit/)
- Результат: точность 82%, 0 крэшей за прогон
```

---

## ФАЗА 2: ОПТИМИЗАЦИЯ АГЕНТНОГО ЦИКЛА ПОД ЛОКАЛЬНУЮ LLM (Неделя 2)

### Цель: Минимизировать количество запросов к LLM и объём контекста. Скорость +33%.

### Задачи:

**2.1. Умная группировка товаров по сайтам**

*Файл:* `src/task_scheduler.py` (новый)

```python
# src/task_scheduler.py
from collections import defaultdict
from dataclasses import dataclass
from typing import List

@dataclass
class ProcessingBatch:
    site_id: str
    site_url: str
    products: List[dict]
    priority: float  # Чем выше, тем раньше обрабатываем

class TaskScheduler:
    """
    Планировщик задач, оптимизированный для локальной LLM.
    Принцип: минимум переключений контекста браузера.
    Группировка по РЕАЛЬНЫМ сайтам из графа (memory_manager.get_sites(product_type)).
    """
    
    def __init__(self, memory_manager):
        self.mm = memory_manager
    
    def plan_processing_order(self, products: List[dict]) -> List[ProcessingBatch]:
        """
        Группирует товары по сайтам для минимизации переключений браузера.
        В реальном коде site для товара определяется внутри process_row из
        memory_manager.get_sites(product_type) + classify_product_type(spec_text).
        """
        # Группируем по целевым сайтам
        by_site = defaultdict(list)
        for product in products:
            site_id = self._determine_target_site(product)
            by_site[site_id].append(product)
        
        # Приоритизация сайтов
        batches = []
        for site_id, site_products in by_site.items():
            site_info = self._get_site_profile(site_id)
            
            priority = self._calculate_priority(
                site_info, 
                len(site_products)
            )
            
            batches.append(ProcessingBatch(
                site_id=site_id,
                site_url=site_info.get('base_url', f"https://{site_id}"),
                products=site_products,
                priority=priority
            ))
        
        # Сортировка: приоритетные сайты первыми
        batches.sort(key=lambda b: b.priority, reverse=True)
        return batches
    
    def _get_site_profile(self, site_id: str) -> dict:
        """Реальный профиль сайта: в БД нет success_rate/has_antibot — берём
        из sites + считаем success_rate из approaches (success_count)."""
        site = self.mm.get_all_sites().get(site_id, {})
        approaches = self.mm.get_approaches_by_site(site_id)
        total_ok = sum(a.get("success_count", 0) for a in approaches)
        total_fail = sum(a.get("failures_count", 0) for a in approaches)
        success_rate = total_ok / max(total_ok + total_fail, 1)
        site = dict(site)
        site["success_rate"] = success_rate
        site["has_antibot"] = False      # позже — из SiteAnalyzer (фаза 3)
        site["speed_score"] = 0.5
        return site
    
    def _calculate_priority(self, site_info, product_count):
        """Приоритет сайта (эскиз; поля success_rate/has_antibot — производные)."""
        success_rate = site_info.get('success_rate', 0.5)
        has_antibot = site_info.get('has_antibot', False)
        
        priority = (
            success_rate * 0.4 +           # Надёжность сайта
            min(product_count / 10, 1.0) * 0.3 +  # Объём работы
            (0 if has_antibot else 1) * 0.2 +     # Простота
            site_info.get('speed_score', 0.5) * 0.1  # Скорость
        )
        return priority
    
    def _determine_target_site(self, product):
        """Определяем целевой сайт для товара.
        В реальности: graph_engine.classify_product_type(spec_text) → product_type,
        затем memory_manager.get_sites(product_type) → берём сайт с лучшей эффективностью."""
        spec_text = product.get('text', '') or product.get('name', '')
        product_type = self.mm._engine.classify_product_type(spec_text)
        sites = self.mm.get_sites(product_type)
        if sites:
            return max(sites, key=lambda s: s.get('priority', 2) * 1.0 - s.get('consecutive_failures', 0) * 0.5)['id']
        return "yandex.ru"   # fallback — поисковик (как в SYSTEM_PROMPT правило 12)
```

*Интеграция в `agent_loop.py`:*
> ⚠️ **Корректировка:** в реальности пайплайн — `MCPAgentRunner(QThread)` в `src/mcp_agent_runner.py`, который итерирует `self.specs` и вызывает `process_row(...)` по одному товару. TaskScheduler встраивается **перед** циклом в `_run_async()`: сначала `plan_processing_order(specs)`, затем процесс в порядке батчей. `_navigate_to_site/_process_single_product/_update_knowledge/_cleanup_after_batch` — новых методов нет, вместо них существующие: `process_row`, `save_confirmed_price`, `save_approach`.

```python
# В src/mcp_agent_runner.py, в _run_async() перед циклом по specs:
from src.task_scheduler import TaskScheduler

scheduler = TaskScheduler(mm)
batches = scheduler.plan_processing_order(self.specs)
ordered = [spec for b in batches for spec in b.products]   # порядок батчей

# ... далее существующий цикл: for i, spec in enumerate(ordered): result = await process_row(...)
```

**2.2. Семантический кэш между задачами**

*Файл:* `src/semantic_cache.py` (новый)

```python
# src/semantic_cache.py
import hashlib
import json
import time
from typing import Optional, Tuple
from pathlib import Path

class SemanticCache:
    """
    Кэш результатов для похожих товаров.
    Не использует embedding-модели (экономия ресурсов),
    вместо этого — нормализация и хеширование.
    """
    
    def __init__(self, cache_file="data/semantic_cache.json"):
        self.cache_file = Path(cache_file)
        self.cache = self._load()
    
    def get_similar(self, product_name: str, 
                    threshold: float = 0.7) -> Optional[dict]:
        """
        Ищет похожий товар в кэше.
        Возвращает кэшированный результат или None.
        """
        normalized = self._normalize(product_name)
        
        for cached_key, cached_data in self.cache.items():
            similarity = self._calculate_similarity(
                normalized, cached_data['normalized_name']
            )
            
            if similarity >= threshold:
                return {
                    **cached_data['result'],
                    'cache_hit': True,
                    'similarity': similarity,
                    'original_query': cached_data['original_name']
                }
        
        return None
    
    def store(self, product_name: str, result: dict):
        """Сохраняет результат в кэш"""
        normalized = self._normalize(product_name)
        key = hashlib.md5(normalized.encode()).hexdigest()
        
        self.cache[key] = {
            'original_name': product_name,
            'normalized_name': normalized,
            'result': result,
            'timestamp': time.time()
        }
        
        # Ограничиваем размер кэша
        if len(self.cache) > 1000:
            self._evict_oldest()
        
        self._save()
    
    def _normalize(self, name: str) -> str:
        """Нормализация названия товара"""
        import re
        # Убираем артикулы, размеры в скобках
        name = re.sub(r'\(.*?\)', '', name)
        name = re.sub(r'\b\d+\s?(мм|м|кг|г|шт)\b', '', name)
        # Приводим к нижнему регистру, убираем лишние пробелы
        name = ' '.join(name.lower().split())
        return name
    
    def _calculate_similarity(self, s1: str, s2: str) -> float:
        """Простая метрика схожести на основе общих слов"""
        words1 = set(s1.split())
        words2 = set(s2.split())
        
        if not words1 or not words2:
            return 0.0
        
        intersection = words1 & words2
        union = words1 | words2
        
        return len(intersection) / len(union)  # Jaccard similarity
    
    def _load(self) -> dict:
        if self.cache_file.exists():
            try:
                with open(self.cache_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except json.JSONDecodeError:
                return {}
        return {}
    
    def _save(self):
        # Исправлено: режим 'w', а не 'r+' (файл может не существовать при первом store)
        with open(self.cache_file, 'w', encoding='utf-8') as f:
            json.dump(self.cache, f, ensure_ascii=False, indent=2)
    
    def _evict_oldest(self):
        """Удаляем 20% самых старых записей"""
        sorted_items = sorted(
            self.cache.items(), 
            key=lambda x: x[1]['timestamp']
        )
        to_remove = len(self.cache) // 5
        for key, _ in sorted_items[:to_remove]:
            del self.cache[key]
```

*Интеграция в `agent_loop.py`:*
> ⚠️ **Корректировка:** результат `process_row` — **dict**, а не объект с `.found`/`.dict()`. Кэш проверяется в начале `process_row` (после rule-8, рядом с `get_relevant_prices`). Сравнение словаря результата с кэшированным должно опираться на ключи `spec_text`/`price`/`confidence`.

```python
# В process_row (после правила 8 reuse): проверяем кэш ПЕРЕД запросом к LLM
cached = semantic_cache.get_similar(spec_text)
if cached and cached.get('confidence', 0) > 0.8 and cached.get('price') is not None:
    logger.info(f"Cache hit for '{spec_text[:40]}' (similarity: {cached['similarity']:.2f})")
    return {
        "spec_text": spec_text,
        "product_type": product_type,
        "price": cached["price"], "confidence": cached["confidence"],
        "url": cached.get("url", ""), "site": cached.get("site", ""),
        "reason": f"semantic_cache hit ({cached['similarity']:.2f})",
        "requires_review": False,
        "elapsed": 0.0,
    }

# После успешного нахождения цены (в _save_price_and_approach или при return):
result = validate_result(final_attempt, spec_text)
if result.get("price") is not None:
    semantic_cache.store(spec_text, result)   # result — dict, не .dict()
```

**2.3. Адаптивные лимиты раундов**

*Файл:* `src/adaptive_limits.py` (новый)

```python
# src/adaptive_limits.py
class AdaptiveRoundManager:
    """
    Динамическое управление лимитами раундов
    на основе сложности задачи и сайта.
    """

> ⚠️ **Корректировка:** поля `is_spa`/`has_antibot`/`avg_dom_depth`/`success_rate` в БД НЕТ (см. «Сверку», таблица sites). До фазы 3 они отсутствуют; `_assess_complexity` должен опираться на доступные данные: `consecutive_failures`, `success_count`, число approaches, факт captcha (`CAPTCHA_KEYWORDS`). После фазы 3 (SiteAnalyzer) профиль сайта можно обогатить. В реальном цикле лимиты уже регулируются через `MAX_ROUNDS_PER_SITE` и `site_round_limits` (5 раундов при `consecutive_failures>=3`) — этот класс надстраивается над ними, а не заменяет.

    BASE_ROUNDS = 10
    MIN_ROUNDS = 5
    MAX_ROUNDS = 30
    
    def calculate_limit(self, site_profile: dict, 
                        product_complexity: float) -> int:
        """Вычисляет оптимальный лимит раундов"""
        complexity_factor = self._assess_complexity(site_profile)
        
        limit = int(
            self.BASE_ROUNDS * 
            complexity_factor * 
            (1 + product_complexity * 0.5)
        )
        
        return max(self.MIN_ROUNDS, min(limit, self.MAX_ROUNDS))
    
    def _assess_complexity(self, site_profile: dict) -> float:
        """Оценка сложности сайта (1.0 = простой, 3.0 = очень сложный)"""
        factors = []
        
        # SPA сложнее SSR
        if site_profile.get('is_spa', False):
            factors.append(1.5)
        else:
            factors.append(1.0)
        
        # Антибот увеличивает сложность
        if site_profile.get('has_antibot', False):
            factors.append(1.8)
        
        # История успеха (ниже success rate = сложнее)
        success_rate = site_profile.get('success_rate', 0.5)
        factors.append(1.0 / max(success_rate, 0.1))
        
        # Глубина DOM
        dom_depth = site_profile.get('avg_dom_depth', 10)
        factors.append(min(dom_depth / 10, 2.0))
        
        return sum(factors) / len(factors)
    
    def should_extend(self, current_round: int, 
                      progress_score: float) -> bool:
        """
        Решаем, стоит ли продлить лимит.
        Если есть прогресс — да, если нет — нет.
        """
        if progress_score > 0.3:  # Есть прогресс
            return True
        return False
```

**2.4. Управление температурой по фазам**

> ⚠️ **Корректировка:** `LLMClient.chat()` НЕ принимает `temperature`/`max_tokens` на вызов — температура задаётся в конструкторе, `max_tokens` захардкожен (8192). Ниже — два допустимых варианта: (а) расширить `LLMClient` необязательными kwargs с сохранением совместимости; (б) фабрика клиентов с разной температурой. Схема `AgentLoop` не существует — обёртка применяется в `process_row`.

*Файл:* `src/agent_loop.py` (модификация), `src/llm_client.py` (опционально)

```python
# Вариант А: добавить в LLMClient необязательные параметры (обратно совместимо)
#   async def chat(self, messages, tools=None, force_json=False,
#                  *, temperature: Optional[float] = None,
#                  max_tokens: Optional[int] = None):
#       temp = temperature if temperature is not None else self.temperature
#       tok = max_tokens if max_tokens is not None else self.max_tokens
#       ... (использовать temp/tok в payload)

# Вариант Б: фабрика клиентов в agent_loop.py (без изменения llm_client.py)
from src.llm_client import LLMClient

TEMP_EXPLORATION = 0.7   # Исследование сайта
TEMP_NAVIGATION = 0.3    # Навигация по элементам
TEMP_EXTRACTION = 0.1    # Извлечение данных (макс. точность)
TEMP_RECOVERY = 0.5      # Выход из тупика

def _client_for(phase: str, base: LLMClient) -> LLMClient:
    """Возвращает клиент с нужной температурой (если совпадает — исходный)."""
    temp = {"exploration": TEMP_EXPLORATION,
            "navigation": TEMP_NAVIGATION,
            "extraction": TEMP_EXTRACTION,
            "recovery": TEMP_RECOVERY}.get(phase, 0.3)
    if abs(temp - base.temperature) < 1e-6:
        return base
    return LLMClient(base_url=base.base_url, api_key=base.api_key,
                     model=base.model, temperature=temp)
```

**2.5. Оптимизация контекстного окна**

> ⚠️ **Корректировка:** реальный лимит контекста Qwen 2.5 ~32K, безопасный бюджет при max_tokens=8192 — ~16K токенов. `_summarize_entries` в проекте уже реализована как `_summarize_history`/`_summarize_large_text` в `agent_loop.py` с `SUMMARIZE_MAX_CHARS` (settings `run.summarize_max_chars=800`) — не дублировать.

### Критерий завершения Фазы 2:
- [ ] Товары группируются по сайтам перед обработкой
- [ ] Семантический кэш работает (cache hit rate > 20% на повторных прогонах)
- [ ] Лимиты раундов адаптивны
- [ ] Температура меняется по фазам
- [ ] Контекст не превышает 8000 токенов
- [ ] **Тест:** 25 товаров за ≤ 20 минут, точность ≥ 85%

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 2: Оптимизация LLM-цикла
- TaskScheduler: группировка по сайтам
- SemanticCache: переиспользование результатов
- AdaptiveRoundManager: динамические лимиты
- Температура по фазам (exploration=0.7, extraction=0.1)
- ContextOptimizer: лимит 8000 токенов
- Результат: 25 товаров за 18 мин, точность 85%
```

---

## ФАЗА 3: АНТИДЕТЕКТ И БРАУЗЕРНАЯ АВТОМАТИЗАЦИЯ (Неделя 3)

### Цель: Снизить частоту банов на 60%, расширить покрытие сайтов.

### Задачи:

**3.1. Расширенный stealth.js**

> ⚠️ **Корректировка:** в существующем `config/stealth.js` уже **12 патчей**, причём патч **11 — MediaDevices Protection** (`navigator.mediaDevices.enumerateDevices` → обнуление deviceId/label). Изначальный «Патч 17: MediaDevices» в плане дублировал его — заменён на WebGL-маскировку. Проверять нумерацию: новые патчи нумеровать с 13 и вставлять в конец файла. Патчи 13–17 ниже добавляются к существующим 12, а НЕ перезаписывают их.

*Файл:* `config/stealth.js` (дополнение)

```javascript
// === ДОПОЛНИТЕЛЬНЫЕ ПАТЧИ (добавить к существующим 12) ===

// Патч 13: Canvas Fingerprint Randomization
(function() {
    const originalToDataURL = HTMLCanvasElement.prototype.toDataURL;
    const originalToBlob = HTMLCanvasElement.prototype.toBlob;
    const originalGetImageData = CanvasRenderingContext2D.prototype.getImageData;
    
    function addNoise(canvas) {
        try {
            const ctx = canvas.getContext('2d');
            if (!ctx) return;
            const imageData = originalGetImageData.call(ctx, 0, 0, canvas.width, canvas.height);
            const data = imageData.data;
            for (let i = 0; i < data.length; i += 4) {
                data[i] = Math.max(0, Math.min(255, data[i] + (Math.random() * 6 - 3)));
                data[i+1] = Math.max(0, Math.min(255, data[i+1] + (Math.random() * 6 - 3)));
                data[i+2] = Math.max(0, Math.min(255, data[i+2] + (Math.random() * 6 - 3)));
            }
            ctx.putImageData(imageData, 0, 0);
        } catch(e) {}
    }
    
    HTMLCanvasElement.prototype.toDataURL = function(type) {
        addNoise(this);
        return originalToDataURL.call(this, type);
    };
    
    HTMLCanvasElement.prototype.toBlob = function(callback, type, quality) {
        addNoise(this);
        return originalToBlob.call(this, callback, type, quality);
    };
})();

// Патч 14: AudioContext Fingerprint
(function() {
    const OriginalAudioContext = window.AudioContext || window.webkitAudioContext;
    if (OriginalAudioContext) {
        const originalCreateOscillator = OriginalAudioContext.prototype.createOscillator;
        const originalCreateAnalyser = OriginalAudioContext.prototype.createAnalyser;
        
        OriginalAudioContext.prototype.createOscillator = function() {
            const osc = originalCreateOscillator.call(this);
            const originalConnect = osc.connect;
            osc.connect = function(dest) {
                return originalConnect.call(this, dest);
            };
            return osc;
        };
    }
})();

// Патч 15: WebRTC Leak Prevention
(function() {
    if (window.RTCPeerConnection) {
        const OriginalRTC = window.RTCPeerConnection;
        window.RTCPeerConnection = function(...args) {
            const pc = new OriginalRTC(...args);
            const origAddIce = pc.addIceCandidate;
            pc.addIceCandidate = function(candidate) {
                if (candidate && candidate.candidate && 
                    (candidate.candidate.includes('srflx') || 
                     candidate.candidate.includes('host'))) {
                    return Promise.resolve();
                }
                return origAddIce.apply(this, arguments);
            };
            return pc;
        };
        window.RTCPeerConnection.prototype = OriginalRTC.prototype;
    }
})();

// Патч 16: Font Enumeration Protection
(function() {
    const originalFonts = document.fonts;
    if (originalFonts && originalFonts.check) {
        const originalCheck = originalFonts.check.bind(originalFonts);
        document.fonts.check = function(font, text) {
            // Ограничиваем список "доступных" шрифтов
            const allowedFonts = ['Arial', 'Times New Roman', 'Courier New', 
                                  'Verdana', 'Helvetica', 'Georgia'];
            const fontFamily = font.split(' ')[0].replace(/['"]/g, '');
            if (!allowedFonts.includes(fontFamily)) {
                return false;
            }
            return originalCheck(font, text);
        };
    }
})();

// Патч 17: WebGL Vendor/Renderer Masking
(function() {
    const getParameter = WebGLRenderingContext.prototype.getParameter;
    WebGLRenderingContext.prototype.getParameter = function(parameter) {
        // UNMASKED_VENDOR_WEBGL = 0x9245, UNMASKED_RENDERER_WEBGL = 0x9246
        if (parameter === 0x9245) return 'Google Inc. (Intel)';
        if (parameter === 0x9246) return 'ANGLE (Intel, Intel(R) UHD Graphics 620 Direct3D11 vs_5_0 ps_5_0)';
        return getParameter.call(this, parameter);
    };
})();
```

**3.2. Имитация человеческого поведения**

> ⚠️ **Корректировка:** метод вызова MCP — `call_tool`, а не `execute_tool`; `browser_evaluate` принимает `{"function": ...}`, а НЕ `{"expression": ...}`. Инструмента `browser_mouse_move` в реальном наборе @playwright/mcp **нет** — движение мыши можно эмулировать через `browser_evaluate` (события mouseover/mousemove) или добавить кастомный инструмент. Пример ниже — исправленный.

*Файл:* `src/human_behavior.py` (новый)

```python
# src/human_behavior.py
import random
import asyncio
from typing import Optional

class HumanBehavior:
    """Имитация человеческого поведения при работе с браузером"""
    
    @staticmethod
    async def human_click(page, selector: str, mcp_bridge):
        """Человеческий клик с движением мыши"""
        # Получаем координаты элемента
        element_info = await mcp_bridge.call_tool(
            "browser_snapshot", {}
        )
        
        # Извлекаем bounding box элемента
        bbox = await mcp_bridge.call_tool(
            "browser_evaluate", 
            {"function": f"""
                (() => {{
                    const el = document.querySelector('{selector}');
                    if (!el) return null;
                    const rect = el.getBoundingClientRect();
                    return {{x: rect.x, y: rect.y, w: rect.width, h: rect.height}};
                }})()
            """}
        )
        
        if not bbox:
            return await mcp_bridge.call_tool("browser_click", {"element": selector})
        
        # Случайная точка внутри элемента (не центр!)
        x = bbox['x'] + random.uniform(bbox['w'] * 0.2, bbox['w'] * 0.8)
        y = bbox['y'] + random.uniform(bbox['h'] * 0.2, bbox['h'] * 0.8)
        
        # Эмуляция движения мыши (browser_mouse_move НЕ существует в @playwright/mcp)
        await mcp_bridge.call_tool("browser_evaluate", {
            "function": f"() => {{ const el = document.elementFromPoint({x}, {y}); "
                        f"if (el) el.dispatchEvent(new MouseEvent('mousemove', {{bubbles: true}})); }}"
        })
        
        # Пауза перед кликом
        await asyncio.sleep(random.uniform(0.1, 0.4))
        
        # Клик
        return await mcp_bridge.call_tool("browser_click", {
            "element": selector
        })
    
    @staticmethod
    async def human_type(page, selector: str, text: str, mcp_bridge):
        """Человеческая печать с переменной скоростью"""
        await mcp_bridge.call_tool("browser_click", {"element": selector})
        
        for char in text:
            await mcp_bridge.call_tool("browser_type", {
                "element": selector,
                "text": char
            })
            
            # Переменная задержка между символами
            delay = random.uniform(0.05, 0.2)
            
            # Иногда "раздумье"
            if random.random() < 0.03:
                delay += random.uniform(0.3, 1.0)
            
            await asyncio.sleep(delay)
    
    @staticmethod
    async def human_scroll(page, direction: str = "down", 
                           distance: int = 300, mcp_bridge=None):
        """Человеческий скролл с рывками"""
        steps = random.randint(3, 7)
        step_distance = distance // steps
        
        for _ in range(steps):
            delta = step_distance if direction == "down" else -step_distance
            await mcp_bridge.call_tool("browser_evaluate", {
                "function": f"() => window.scrollBy(0, {delta})"
            })
            await asyncio.sleep(random.uniform(0.05, 0.15))
        
        # Финальная пауза
        await asyncio.sleep(random.uniform(0.5, 1.5))
    
    @staticmethod
    async def random_pause(min_sec: float = 0.5, max_sec: float = 2.0):
        """Случайная пауза между действиями"""
        await asyncio.sleep(random.uniform(min_sec, max_sec))
    
    @staticmethod
    def get_random_viewport() -> dict:
        """Случайное разрешение экрана"""
        viewports = [
            {"width": 1920, "height": 1080},
            {"width": 1366, "height": 768},
            {"width": 1536, "height": 864},
            {"width": 1440, "height": 900},
            {"width": 1280, "height": 720},
        ]
        return random.choice(viewports)
```

**3.3. Rate Limiting per Domain**

*Файл:* `src/rate_limiter.py` (новый)

```python
# src/rate_limiter.py
import time
from collections import defaultdict
from urllib.parse import urlparse

class DomainRateLimiter:
    """Ограничение частоты запросов к каждому домену"""
    
    def __init__(self, min_interval: float = 2.0, 
                 max_requests_per_minute: int = 20):
        self.min_interval = min_interval  # Мин. интервал между запросами
        self.max_rpm = max_requests_per_minute
        self.request_history = defaultdict(list)  # domain -> [timestamps]
        self.last_request = defaultdict(float)    # domain -> timestamp
    
    async def wait_if_needed(self, url: str):
        """Ждём, если нужно, перед запросом к домену"""
        domain = urlparse(url).netloc
        
        # Проверяем минимальный интервал
        elapsed = time.time() - self.last_request[domain]
        if elapsed < self.min_interval:
            await asyncio.sleep(self.min_interval - elapsed)
        
        # Проверяем лимит запросов в минуту
        self._cleanup_old_requests(domain)
        if len(self.request_history[domain]) >= self.max_rpm:
            wait_time = 60 - (time.time() - self.request_history[domain][0])
            if wait_time > 0:
                await asyncio.sleep(wait_time)
        
        # Записываем запрос
        now = time.time()
        self.request_history[domain].append(now)
        self.last_request[domain] = now
    
    def _cleanup_old_requests(self, domain: str):
        """Удаляем записи старше 60 секунд"""
        cutoff = time.time() - 60
        self.request_history[domain] = [
            ts for ts in self.request_history[domain] if ts > cutoff
        ]
    
    def get_stats(self, domain: str) -> dict:
        """Статистика по домену"""
        self._cleanup_old_requests(domain)
        return {
            "requests_last_minute": len(self.request_history[domain]),
            "seconds_since_last": time.time() - self.last_request[domain]
        }
```

**3.4. Детекция типа сайта (SPA/SSR)**

> ⚠️ **Корректировка:** `"window.__NUXT__"` — **невалидный CSS-селектор** (`querySelector` упадёт с SyntaxError). Проверка должна использовать `typeof window.__NUXT__ !== 'undefined'` через `browser_evaluate`. Метод вызова — `call_tool`, параметр `browser_evaluate` — `function`. Результат анализа можно сохранять в профиль сайта (таблицы `sites` нет под эти поля — держать в памяти/JSON, не в БД).

*Файл:* `src/site_analyzer.py` (новый)

```python
# src/site_analyzer.py
class SiteAnalyzer:
    """Определяет тип сайта и оптимальную стратегию навигации"""
    
    SPA_INDICATORS = [
        "window.__NUXT__",           # Nuxt.js
        "window.__NEXT_DATA__",      # Next.js
        "ng-version",                # Angular
        "data-reactroot",            # React
        "__vue__",                   # Vue.js
        "id='root'",                 # SPA root
        "id='app'",                  # SPA root
    ]
    
    ANTIBOT_INDICATORS = [
        "cloudflare",
        "cf-browser-verification",
        "captcha",
        "recaptcha",
        "hcaptcha",
        "datadome",
        "perimeterx",
        "px-captcha",
        "challenge-form",
    ]
    
    async def analyze_site(self, page_url: str, mcp_bridge) -> dict:
        """Анализирует сайт и возвращает профиль"""
        # Загружаем страницу
        await mcp_bridge.call_tool("browser_navigate", {"url": page_url})
        await asyncio.sleep(2)  # Ждём загрузки
        
        # Определяем тип рендеринга
        is_spa = await self._detect_spa(mcp_bridge)
        
        # Определяем наличие антибота
        has_antibot = await self._detect_antibot(mcp_bridge)
        
        # Оцениваем сложность DOM
        dom_stats = await self._get_dom_stats(mcp_bridge)
        
        return {
            "url": page_url,
            "is_spa": is_spa,
            "has_antibot": has_antibot,
            "dom_depth": dom_stats.get("max_depth", 10),
            "dom_elements": dom_stats.get("total_elements", 0),
            "recommended_strategy": self._recommend_strategy(is_spa, has_antibot)
        }
    
    async def _detect_spa(self, mcp_bridge) -> bool:
        """Определяет, является ли сайт SPA.
        Исправлено: window.__NUXT__ — не CSS-селектор, проверяем глобальный объект."""
        for indicator in self.SPA_INDICATORS:
            if indicator.startswith("window."):
                # Проверка глобальной переменной
                check = f"() => typeof {indicator} !== 'undefined'"
            else:
                # Проверка DOM-элемента по CSS-селектору
                check = f"() => !!document.querySelector('{indicator}')"
            result = await mcp_bridge.call_tool("browser_evaluate", {"function": check})
            if result:
                return True
        return False
    
    async def _detect_antibot(self, mcp_bridge) -> bool:
        """Определяет наличие антибот-защиты"""
        page_source = await mcp_bridge.call_tool("browser_snapshot", {})
        page_lower = str(page_source).lower()
        
        return any(ind in page_lower for ind in self.ANTIBOT_INDICATORS)
    
    async def _get_dom_stats(self, mcp_bridge) -> dict:
        """Получает статистику DOM"""
        result = await mcp_bridge.call_tool("browser_evaluate", {
            "function": """
                (() => {
                    function getDepth(el) {
                        let depth = 0;
                        while (el.parentElement) {
                            depth++;
                            el = el.parentElement;
                        }
                        return depth;
                    }
                    
                    const allElements = document.querySelectorAll('*');
                    let maxDepth = 0;
                    for (let el of allElements) {
                        maxDepth = Math.max(maxDepth, getDepth(el));
                    }
                    
                    return {
                        total_elements: allElements.length,
                        max_depth: maxDepth
                    };
                })()
            """
        })
        return result or {"total_elements": 0, "max_depth": 0}
    
    def _recommend_strategy(self, is_spa: bool, has_antibot: bool) -> str:
        """Рекомендует стратегию навигации"""
        if has_antibot:
            return "CAUTIOUS"      # Медленно, с паузами, без резких действий
        elif is_spa:
            return "SPA_AWARE"     # Ждать рендеринга, использовать waitForSelector
        else:
            return "STANDARD"      # Обычная навигация
```

**3.5. CAPTCHA Detection (без автоматического решения)**

*Файл:* `src/captcha_detector.py` (новый)

```python
# src/captcha_detector.py
from enum import Enum

class CaptchaType(Enum):
    NONE = "none"
    RECAPTCHA_V2 = "recaptcha_v2"
    RECAPTCHA_V3 = "recaptcha_v3"
    HCAPTCHA = "hcaptcha"
    CLOUDFLARE = "cloudflare"
    IMAGE = "image"
    UNKNOWN = "unknown"

class CaptchaDetector:
    """
    Обнаруживает CAPTCHA и рекомендует действие.
    В v2.0 НЕ решает CAPTCHA автоматически — 
    только детектирует и сообщает агенту.
    """
    
    DETECTION_SELECTORS = {
        CaptchaType.RECAPTCHA_V2: [
            "iframe[src*='recaptcha']",
            ".g-recaptcha",
            "#recaptcha"
        ],
        CaptchaType.HCAPTCHA: [
            "iframe[src*='hcaptcha']",
            ".h-captcha"
        ],
        CaptchaType.CLOUDFLARE: [
            ".cf-turnstile",
            "#challenge-form",
            "iframe[src*='challenges.cloudflare.com']"
        ],
        CaptchaType.IMAGE: [
            "[class*='captcha']",
            "#captcha-image",
            "img[src*='captcha']"
        ]
    }
    
    async def detect(self, page_snapshot: str) -> CaptchaType:
        """Определяет тип CAPTCHA по снимку страницы"""
        snapshot_lower = page_snapshot.lower()
        
        for captcha_type, selectors in self.DETECTION_SELECTORS.items():
            for selector in selectors:
                if selector.lower() in snapshot_lower:
                    return captcha_type
        
        # Проверка по ключевым словам
        captcha_keywords = ["captcha", "verify you are human", 
                           "i'm not a robot", "подтвердите"]
        if any(kw in snapshot_lower for kw in captcha_keywords):
            return CaptchaType.UNKNOWN
        
        return CaptchaType.NONE
    
    def get_recommendation(self, captcha_type: CaptchaType) -> str:
        """Рекомендация для агента при обнаружении CAPTCHA"""
        recommendations = {
            CaptchaType.NONE: "PROCEED",
            CaptchaType.RECAPTCHA_V2: "SWITCH_SITE",
            CaptchaType.RECAPTCHA_V3: "WAIT_AND_RETRY",
            CaptchaType.HCAPTCHA: "SWITCH_SITE",
            CaptchaType.CLOUDFLARE: "WAIT_60S_AND_RETRY",
            CaptchaType.IMAGE: "ASK_USER",
            CaptchaType.UNKNOWN: "SWITCH_SITE"
        }
        return recommendations.get(captcha_type, "SWITCH_SITE")
```

### Критерий завершения Фазы 3:
- [ ] stealth.js расширен до 17+ патчей
- [ ] HumanBehavior имитирует человека (клики, печать, скролл)
- [ ] Rate limiter ограничивает запросы per domain
- [ ] SiteAnalyzer определяет SPA/SSR и антибот
- [ ] CaptchaDetector обнаруживает и рекомендует действие
- [ ] **Тест:** Прогон на 3+ сайтах с антиботом без блокировки

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 3: Антидетект и браузер
- stealth.js: 17 патчей (+Canvas, WebRTC, Fonts, MediaDevices)
- HumanBehavior: человеческие клики, печать, скролл
- DomainRateLimiter: 20 req/min per domain
- SiteAnalyzer: детекция SPA/SSR/антибота
- CaptchaDetector: обнаружение без автрешения
- Результат: 0 банов на тестовом прогоне
```

---

## ФАЗА 4: ЭВОЛЮЦИЯ ГРАФА ЗНАНИЙ (Неделя 4)

### Цель: Самообучение системы, актуальность знаний, +5% точность.

### Задачи:

**4.1. Версионирование подходов и effectiveness scoring**

> ⚠️ **Корректировка:** реальные методы — `update_approach_success(approach_id)` / `update_approach_failure(approach_id)` (уже есть в GraphEngine, обновляют `success_count`/`failures_count`/`consecutive_failures`/`last_success_date`/`last_failure_date` и деактивируют при `consecutive_failures >= 3`). Таблиц `revalidation_queue` и полей `success_rate`/`status`/`total_successes` в БД НЕТ — эффективность вычисляется **на лету** из `success_count/(success_count+failures_count)`. `get_effective_approaches` использует существующий `memory_manager.get_approaches_by_site(site_id)`.

*Файл:* `src/memory_manager.py` (модификация)

```python
# Дополнение к MemoryManager

class ApproachVersioning:
    """Управление версиями и эффективностью подходов.
    Надстройка над существующими update_approach_success/update_approach_failure."""

    def __init__(self, engine):
        self.engine = engine   # GraphEngine

    def update_effectiveness(self, approach_id: int, success: bool):
        """Обновляет эффективность подхода после использования
        (делегирует в существующие методы GraphEngine)."""
        if success:
            self.engine.update_approach_success(approach_id)
        else:
            self.engine.update_approach_failure(approach_id)

    def get_effective_approaches(self, site_id: str, limit: int = 5) -> list:
        """Возвращает наиболее эффективные подходы для сайта.
        success_rate вычисляется на лету — колонки в БД нет."""
        approaches = self.engine.get_approaches_by_site(site_id)

        def _score(a: dict) -> float:
            ok = a.get("success_count", 0)
            fail = a.get("failures_count", 0)
            base = ok / max(ok + fail, 1)
            if a.get("is_deprecated"):
                base *= 0.5
            return base

        active = [a for a in approaches if not a.get("is_deprecated")]
        active.sort(key=_score, reverse=True)
        return active[:limit]
```

**4.2. TTL для хинтов**

> ⚠️ **Корректировка:** реальная таблица `hints(id, product_type_id, site_id, hint_text, priority, created_at)` — колонок `content`/`confidence`/`expires_at`/`status`/`validation_count`/`last_validated` НЕТ. Сохранять хинт — существующий `memory_manager.save_hint(...)` / `add_hint(...)`. TTL можно добавить новой колонкой `expires_at` (миграция ALTER TABLE) или фильтровать в коде. Реальный `get_hints(product_type, site)` фильтрует по `product_type_id`+`site_id`, а не только по site.

*Файл:* `src/memory_manager.py` (дополнение)

```python
class HintManager:
    """Управление хинтами с TTL (надстройка над существующим save_hint/get_hints).
    ВНИМАНИЕ: колонок content/confidence/status в БД нет — нужна миграция
    (ALTER TABLE hints ADD COLUMN expires_at TEXT) либо фильтрация в коде."""

    DEFAULT_TTL_DAYS = 90

    def create_hint(self, product_type_id: str, site_id: str, text: str,
                    priority: float = 0.5, ttl_days: int = None) -> dict:
        """Создаёт хинт (при отсутствии expires_at в схеме — TTL фильтруется в коде)"""
        ttl = ttl_days or self.DEFAULT_TTL_DAYS
        hint = {
            "product_type_id": product_type_id,
            "site_id": site_id,
            "hint_text": text,
            "priority": priority,
            "expires_at": (datetime.now() + timedelta(days=ttl)).isoformat(),
        }
        self.engine.save_hint(product_type_id, site_id, text, priority)
        return hint

    def get_active_hints(self, product_type_id: str, site_id: str) -> list:
        """Возвращает активные хинты (TTL фильтруем в коде, пока нет колонки expires_at)"""
        hints = self.engine.get_hints(product_type_id, site_id)
        now = datetime.now()
        return [
            h for h in hints
            if not h.get("expires_at") or datetime.fromisoformat(h["expires_at"]) > now
        ]

    def cleanup_expired(self):
        """Удаляет просроченные хинты (только если колонка expires_at добавлена)"""
        now = datetime.now().isoformat()
        self.engine._db.execute("DELETE FROM hints WHERE expires_at <= ?", (now,))
```

**4.3. Learning Loop — автообучение из результатов**

> ⚠️ **Корректировка:** `consolidate_after_run` вызывается после завершения прогона в `MCPAgentRunner` (после цикла `process_row` по всем спецификациям), а не внутри `process_row`. Методы классов исправлены на существующие API (`save_approach`, `add_hint`, `update_approach_success/failure`) — см. код ниже.

*Файл:* `src/learning_loop.py` (новый)

```python
# src/learning_loop.py
from datetime import datetime

class LearningLoop:
    """
    Замкнутый цикл обучения:
    Прогон → Анализ результатов → Обновление графа → Улучшение будущих прогонов
    """
    
    def __init__(self, graph_engine, memory_manager):
        self.graph = graph_engine
        self.memory = memory_manager
    
    def consolidate_after_run(self, run_results: list):
        """
        Вызывается после завершения прогона.
        Анализирует результаты и обновляет знания.
        """
        # 1. Обновляем эффективность подходов
        self._update_approach_effectiveness(run_results)
        
        # 2. Извлекаем новые паттерны из успешных результатов
        new_patterns = self._extract_patterns(run_results)
        
        # 3. Генерируем новые хинты
        new_hints = self._generate_hints(run_results)
        
        # 4. Обновляем профили сайтов
        self._update_site_profiles(run_results)
        
        # 5. Сохраняем статистику прогона
        self._save_run_statistics(run_results)
        
        return {
            "approaches_updated": len(run_results),
            "new_patterns": len(new_patterns),
            "new_hints": len(new_hints)
        }
    
    def _update_approach_effectiveness(self, results):
        """Обновляет эффективность подходов.
        Реально: update_approach_success/failure уже вызываются в process_row
        (при found/не found). Здесь — только агрегация статистики."""
        for result in results:
            if result.get('approach_id'):
                success = bool(result.get('price'))
                if success:
                    self.memory._engine.update_approach_success(result['approach_id'])
                else:
                    self.memory._engine.update_approach_failure(result['approach_id'])
    
    def _extract_patterns(self, results):
        """Извлекает успешные паттерны для переиспользования.
        НЕТ upsert_pattern — сохраняем подход через memory.save_approach(...)
        (pattern/concrete/selectors_cache из реального result dict)."""
        successful = [r for r in results if r.get('price')]
        patterns = []
        
        for result in successful:
            # Если товар найден через определённый подход — запоминаем
            approach_id = result.get('approach_id')
            if approach_id:
                pattern = {
                    "product_type_id": result.get('product_type'),
                    "site_id": result.get('site_id') or result.get('site'),
                    "pattern": result.get('selectors', {}).get('pattern', ''),
                    "concrete": result.get('selectors', {}).get('concrete', ''),
                    "selectors_cache": result.get('selectors', {}),
                    "method": "search",
                }
                patterns.append(pattern)
        
        for pattern in patterns:
            self.memory.save_approach(pattern)   # существующий метод MemoryManager
        
        return patterns
    
    def _generate_hints(self, results):
        """Генерирует хинты из результатов.
        Реально: memory.add_hint(product_type_id, site_id, hint_text, priority)."""
        hints = []
        
        for result in results:
            # Если были трудности с поиском — записываем хинт
            if result.get('elapsed', 0) > 60 and result.get('price') is not None:
                hint_content = (
                    f"Товар '{result.get('spec_text')}' найден после долгого поиска "
                    f"({result['elapsed']:.0f}s). "
                    f"Использован подход: {result.get('approach_id')}"
                )
                hints.append({
                    "product_type_id": result.get('product_type'),
                    "site_id": result.get('site'),
                    "hint_text": hint_content,
                    "priority": 0.3
                })
        
        for hint in hints:
            self.memory.add_hint(**hint)   # существующий метод MemoryManager
        
        return hints
    
    def _update_site_profiles(self, results):
        """Обновляет профили сайтов на основе результатов.
        Реально: поля success_rate/has_antibot в БД отсутствуют — агрегацию
        храним в памяти/JSON и передаём в TaskScheduler/AdaptiveRoundManager,
        а в БД обновляем только product_sites.consecutive_failures (уже есть)."""
        site_stats = {}
        
        for result in results:
            site_id = result.get('site') or result.get('site_id')
            if not site_id:
                continue
            
            if site_id not in site_stats:
                site_stats[site_id] = {
                    "total": 0, "success": 0, 
                    "total_attempts": 0, "blocks": 0
                }
            
            site_stats[site_id]["total"] += 1
            site_stats[site_id]["total_attempts"] += result.get('elapsed', 0)
            
            if result.get('price'):
                site_stats[site_id]["success"] += 1
            
            if result.get('reason') and 'captcha' in str(result.get('reason', '')).lower():
                site_stats[site_id]["blocks"] += 1
        
        # Обновляем in-memory профили (НЕ БД — колонок нет)
        for site_id, stats in site_stats.items():
            success_rate = stats["success"] / max(stats["total"], 1)
            avg_attempts = stats["total_attempts"] / max(stats["total"], 1)
            
            self.site_profiles[site_id] = {
                "success_rate": success_rate,
                "avg_attempts": avg_attempts,
                "block_count": stats["blocks"],
                "last_updated": datetime.now().isoformat()
            }

    def _save_run_statistics(self, results):
        """Сохраняет статистику прогона (in-memory + лог; таблицы статистики в БД нет)."""
        total = len(results)
        found = sum(1 for r in results if r.get("price"))
        self.last_run_stats = {
            "total": total,
            "found": found,
            "success_rate": found / max(total, 1),
            "ts": datetime.now().isoformat(),
        }
        logger.info(f"Run stats: {self.last_run_stats}")
```

**4.4. Оптимизация SQLite**

> ⚠️ **Корректировка:** реальный GraphEngine уже включает **WAL mode, foreign_keys=ON и busy_timeout** в `build()` (`PRAGMA journal_mode=WAL`). Ниже — НЕ для повторного включения, а для **проверки/дополнения** недостающих прагм. Индексы из прежнего эскиза ссылались на несуществующие колонки (`approaches(status)`, `hints(expires_at)`, `confirmed_prices(product_id)`, `sites(category)`) — они не создадутся. Реальные индексы уже есть: `idx_approaches_product_site`, `idx_approaches_site`, `idx_confirmed_spec`, `idx_confirmed_product`, `idx_hints_product`.

*Файл:* `src/graph_engine.py` (проверка в `build()`)

```python
# В GraphEngine.build() — ДОБАВИТЬ только недостающее (не дублировать WAL/busy_timeout):
class GraphEngine:
    def _apply_pragmas(self):
        """Дополнение к существующим PRAGMA (WAL и foreign_keys уже включены)."""
        with self._lock:
            cur = self._conn.cursor()
            # WAL уже включён в build(); здесь только доп. прагмы производительности
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA cache_size=-64000")   # 64MB кэш
            cur.execute("PRAGMA temp_store=MEMORY")
            # Проверка (уже созданы в build()): idx_approaches_product_site, idx_approaches_site,
            # idx_confirmed_spec, idx_confirmed_product, idx_hints_product
            self._conn.commit()
```

### Критерий завершения Фазы 4:
- [ ] Подходы имеют success_rate и версионирование
- [ ] Хинты имеют TTL и автоматическую деактивацию
- [ ] Learning Loop обновляет граф после каждого прогона
- [ ] SQLite оптимизирован (WAL mode, индексы)
- [ ] **Тест:** Повторный прогон показывает +5% точность за счёт обучения

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 4: Эволюция графа знаний
- ApproachVersioning: success_rate, деградация, деактивация
- HintManager: TTL 90 дней, ревалидация раз в месяц
- LearningLoop: автообучение из результатов прогона
- SQLite: WAL mode, индексы, busy_timeout
- Результат: повторный прогон +5% точность
```

---

## ФАЗА 5: МОДЕРНИЗАЦИЯ PDF-ПАРСЕРА (Неделя 5)

### Цель: Покрытие PDF-форматов 60% → 95%, минимизация ручного ревью.

### Задачи:

**5.1. Lightweight LLM Structurer (малая модель)**

> ⚠️ **Корректировка:** реальный `SpecStructurer.structure(raw_text) -> list[dict]` возвращает **список позиций** с ключами `{pos, name, specs, code, manufacturer, qty, unit, weight}` — НЕ `{"columns", "rows"}`. LLM-ветка **в настоящий момент отключена** (вызывается только `_fallback_parse`, LLMClient хранится как `self._llm` без использования). Реальный `chat()` НЕ принимает `model`/`max_tokens`/`temperature` на вызов (см. Сверку). Ниже — исправленный эскиз: включаем LLM-ветку как ОПЦИЮ (конфиг `pdf_parser.use_llm`), сохраняя существующий fallback.

*Файл:* `src/pdf_parser/structurer.py` (модификация)

```python
# src/pdf_parser/structurer.py
class LightweightStructurer:
    """
    Структурирование таблиц через малую модель (ОПЦИЯ).
    Реальный контракт: structure() -> list[dict] с ключами
    {pos, name, specs, code, manufacturer, qty, unit, weight}.
    LLM-ветка включается конфигом; без неё работает существующий _fallback_parse.
    """

    def __init__(self, llm_client):
        self.llm = llm_client
        self.use_llm = False   # из settings.yaml pdf_parser.use_llm; сейчас False

    async def structure_table(self, raw_text: str) -> list[dict]:
        """Преобразует сырой текст таблицы в список позиций.
        Контракт совпадает с существующим SpecStructurer.structure()."""
        if not self.use_llm:
            return self._fallback_parse(raw_text)

        truncated = raw_text[:3000]
        prompt = f"""Преобразуй текст таблицы в JSON список позиций.
Только результат, без пояснений и рассуждений.

Формат каждой позиции:
{{"pos": 1, "name": "Кабель ВВГнг", "specs": "3х2.5", "code": "A001",
  "manufacturer": "ООО Кабель", "qty": 100.0, "unit": "м", "weight": 0.0}}

Текст таблицы:
{truncated}

JSON:"""

        response = self.llm.chat(
            messages=[{"role": "user", "content": prompt}],
            force_json=True,        # единственный доступный доп. параметр chat()
        )
        return self._safe_parse_items(response)

    def _safe_parse_items(self, response: dict) -> list[dict]:
        """Парсит список позиций из ответа LLM (chat() возвращает dict)."""
        try:
            content = response.get("content", "")
            start, end = content.find('['), content.rfind(']') + 1
            if start >= 0 and end > start:
                items = json.loads(content[start:end])
                if isinstance(items, list):
                    return [self._normalize_item(it) for it in items]
        except (json.JSONDecodeError, AttributeError):
            pass
        return self._fallback_parse(str(response))

    def _normalize_item(self, it: dict) -> dict:
        """Приводит позицию к реальному контракту SpecStructurer."""
        return {
            "pos": int(it.get("pos", 0) or 0),
            "name": str(it.get("name", "")).strip(),
            "specs": str(it.get("specs", "")),
            "code": str(it.get("code", "")),
            "manufacturer": str(it.get("manufacturer", "")),
            "qty": float(it.get("qty", 0) or 0),
            "unit": str(it.get("unit", "")),
            "weight": float(it.get("weight", 0) or 0),
        }

    def _fallback_parse(self, text: str) -> list[dict]:
        """Резервный парсинг — делегирует существующему SpecStructurer."""
        return self._existing_parse(text)   # реальный _fallback_parse из structurer.py
```

**5.2. OCR Fallback для сканированных PDF**

> ⚠️ **Корректировка:** в проекте НЕТ PaddleOCR/PyMuPDF/Tesseract (в `requirements.txt` только PySide6, httpx, mcp, openpyxl, PyYAML, pytest, networkx, numpy). Реальный OCR-бэкенд — **`MinerUBackend`** (`src/pdf_parser/mineru_backend.py`): изолированный `mineru_venv` (Python 3.11), запуск `mineru.exe` subprocess'ом. MinerU сам обрабатывает сканы (встроенный OCR). Отдельный OCRFallback нужен только как доп. резерв — предлагается реализовать через MinerU-режим, а не новые зависимости.

*Файл:* `src/pdf_parser/ocr_fallback.py` (новый, опционально)

```python
# src/pdf_parser/ocr_fallback.py
from pathlib import Path

class OCRFallback:
    """OCR для сканированных PDF без текстового слоя.
    РЕАЛЬНЫЙ бэкенд — MinerUBackend (mineru_venv), НЕ PaddleOCR/Tesseract."""

    MIN_TEXT_LENGTH = 100  # Минимум символов для "нормального" PDF

    def __init__(self, mineru_backend=None):
        self.mineru_backend = mineru_backend   # src.pdf_parser.mineru_backend.MinerUBackend
        self.ocr_engine = None                 # (для будущего; сейчас не используется)

    def needs_ocr(self, extracted_text: str) -> bool:
        """Определяет, нужен ли OCR"""
        return len(extracted_text.strip()) < self.MIN_TEXT_LENGTH

    async def extract_with_ocr(self, pdf_path: str) -> str:
        """Извлекает текст через MinerU (обрабатывает и сканы, и текстовые PDF).
        Реальный вызов: mineru_backend.extract(pdf_path) -> dict с полем 'content'."""
        if not self.mineru_backend:
            return ""
        result = await self.mineru_backend.extract(pdf_path)
        return result.get("content", "") if isinstance(result, dict) else str(result)
```

**5.3. Smart Review с Confidence Scoring**

*Файл:* `src/pdf_parser/review.py` (новый)

```python
# src/pdf_parser/review.py
class SmartReview:
    """Полуавтоматическое ревью с confidence scoring.
    Вход: list[dict] позиций из SpecStructurer (pos/name/specs/code/manufacturer/qty/unit/weight)."""

    CONFIDENCE_THRESHOLD = 0.8

    def __init__(self):
        self.auto_approved = []
        self.needs_review = []

    def process_extraction(self, items: list[dict]) -> tuple:
        """
        Разделяет позиции на авто-утверждённые и требующие ревью.
        Возвращает (auto_approved, needs_review)
        """
        self.auto_approved = []
        self.needs_review = []

        for row in items:
            confidence = self._calculate_confidence(row)
            row["confidence"] = confidence

            if confidence >= self.CONFIDENCE_THRESHOLD:
                self.auto_approved.append(row)
            else:
                self.needs_review.append(row)

        return self.auto_approved, self.needs_review

    def _calculate_confidence(self, row: dict) -> float:
        """Вычисляет уверенность в корректности позиции.
        Контракт позиции не содержит цены — цена не участвует в скорринге."""
        score = 0.0

        name = str(row.get("name", "")).strip()
        code = str(row.get("code", "")).strip()
        manufacturer = str(row.get("manufacturer", "")).strip()
        specs = str(row.get("specs", "")).strip()
        qty = row.get("qty", 0) or 0
        unit = str(row.get("unit", "")).strip()

        # Ключевые поля заполнены
        if name:
            score += 0.4
        if qty and qty > 0:
            score += 0.2
        if unit:
            score += 0.1
        if code or manufacturer:
            score += 0.2
        if specs:
            score += 0.1

        return min(score, 1.0)

    def _extract_name(self, row: dict) -> str:
        """Извлекает название товара из позиции"""
        return str(row.get("name", "")).strip()
```

### Критерий завершения Фазы 5:
- [ ] Lightweight Structurer использует малую модель (не reasoning) — **опция**, по умолчанию fallback
- [ ] OCR fallback работает для сканированных PDF (через MinerUBackend, не новые зависимости)
- [ ] Smart Review автоматически утверждает >70% результатов
- [ ] **Тест:** 5 разных PDF-форматов обрабатываются без ручного вмешательства

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 5: PDF-парсер
- LightweightStructurer: малая модель (опция), fallback сохранён
- OCRFallback: через MinerUBackend (mineru_venv), без PaddleOCR/Tesseract
- SmartReview: confidence scoring по реальному контракту позиций, авто-утверждение >70%
- Результат: 5/5 тестовых PDF обработаны
```

---

## ФАЗА 6: GUI И МОНИТОРИНГ (Неделя 6)

### Цель: Real-time мониторинг агента, оптимизация графа, улучшение UX.

### Задачи:

**6.1. Real-time мониторинг агента**

*Файл:* `gui/agent_monitor.py` (новый)

```python
# gui/agent_monitor.py
from PySide6.QtWidgets import QWidget, QVBoxLayout, QLabel, QListWidget, QProgressBar
from PySide6.QtCore import Signal, Slot
from PySide6.QtGui import QPixmap

class AgentMonitorPanel(QWidget):
    """Панель real-time мониторинга агента"""
    
    # Сигналы для обновления из агентного цикла
    action_updated = Signal(str)      # Текущее действие
    progress_updated = Signal(int)    # Прогресс (0-100)
    screenshot_updated = Signal(bytes)  # Скриншот текущей страницы
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
    
    def _init_ui(self):
        layout = QVBoxLayout(self)
        
        # Текущее действие
        self.action_label = QLabel("Ожидание запуска...")
        self.action_label.setStyleSheet("font-size: 14px; font-weight: bold;")
        layout.addWidget(self.action_label)
        
        # Прогресс-бар
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 100)
        layout.addWidget(self.progress_bar)
        
        # Скриншот текущей страницы
        self.screenshot_label = QLabel("Нет скриншота")
        self.screenshot_label.setMaximumHeight(200)
        layout.addWidget(self.screenshot_label)
        
        # История действий
        self.history_list = QListWidget()
        self.history_list.setMaximumHeight(150)
        layout.addWidget(self.history_list)
        
        # Подключаем сигналы
        self.action_updated.connect(self._update_action)
        self.progress_updated.connect(self._update_progress)
        self.screenshot_updated.connect(self._update_screenshot)
    
    @Slot(str)
    def _update_action(self, action_text):
        self.action_label.setText(action_text)
        self.history_list.addItem(f"[{datetime.now().strftime('%H:%M:%S')}] {action_text}")
        self.history_list.scrollToBottom()
    
    @Slot(int)
    def _update_progress(self, value):
        self.progress_bar.setValue(value)
    
    @Slot(bytes)
    def _update_screenshot(self, image_data):
        pixmap = QPixmap()
        pixmap.loadFromData(image_data)
        self.screenshot_label.setPixmap(
            pixmap.scaled(400, 200, Qt.KeepAspectRatio, Qt.SmoothTransformation)
        )
```

**6.2. Оптимизация визуализации графа**

> ⚠️ **Корректировка:** в реальном `gui/graph_explorer.py` НЕТ методов `get_viewport()`, `draw_node()`, `draw_edge()`, `draw_point()`. Реальная архитектура — QGraphicsScene/View: `GraphNode` (QGraphicsItem), `NodeItem`, `GraphScene` (наследует QGraphicsScene), `GraphCanvas` (QGraphicsView), `GraphExplorerWidget`; граф строится через `_build_graph`/`_update_edges(idx)` (в `_update_edges` есть известный баг приоритета на ~стр. 308–310). LOD/culling в QGraphicsScene не нужны — scene уже отрисовывает только видимую область. Реальная оптимизация — **пороговый `update()`/`refresh()` и отложенный пересчёт позиций**. Ниже — исправленный эскиз на существующих QGraphics-классах.

*Файл:* `gui/graph_explorer.py` (модификация)

```python
# Дополнение к GraphExplorerWidget/GraphScene

class OptimizedGraphRenderer:
    """Оптимизированный рендеринг графа.
    Реальные объекты: GraphScene (QGraphicsScene), GraphCanvas (QGraphicsView).
    QGraphicsScene сам отрисовывает только видимую область — LOD/culling не нужны."""

    LOD_THRESHOLD = 500   # После этого числа нод — отключаем подписи/физ.симуляцию
    CULLING_MARGIN = 100

    def __init__(self, scene: 'GraphScene'):
        self.scene = scene

    def render(self, nodes, edges):
        """Рендеринг с порогом детализации (QGraphicsView отрисует видимое сам)."""
        if len(nodes) > self.LOD_THRESHOLD:
            self.scene.set_labels_visible(False)   # новый флаг — подписи off
            self.scene.set_physics(False)          # отключить непрерывную симуляцию
        else:
            self.scene.set_labels_visible(True)
            self.scene.set_physics(True)
        self.scene.invalidate()                    # перерисовать сцену
```

**6.3. Панель метрик прогона**

*Файл:* `gui/metrics_panel.py` (новый)

```python
# gui/metrics_panel.py
from PySide6.QtWidgets import QWidget, QGridLayout, QLabel, QGroupBox

class MetricsPanel(QWidget):
    """Панель метрик текущего/последнего прогона"""
    
    def __init__(self, parent=None):
        super().__init__(parent)
        self._init_ui()
    
    def _init_ui(self):
        layout = QGridLayout(self)
        
        # Метрики
        self.metrics = {
            "total_products": self._create_metric("Всего товаров", "0"),
            "processed": self._create_metric("Обработано", "0"),
            "found": self._create_metric("Найдено", "0"),
            "success_rate": self._create_metric("Успешность", "0%"),
            "llm_calls": self._create_metric("Запросов к LLM", "0"),
            "avg_llm_time": self._create_metric("Ср. время LLM", "0s"),
            "cache_hits": self._create_metric("Попаданий в кэш", "0"),
            "stuck_events": self._create_metric("Застреваний", "0"),
            "blocks": self._create_metric("Блокировок", "0"),
        }
        
        for i, (key, widget) in enumerate(self.metrics.items()):
            row, col = divmod(i, 3)
            layout.addWidget(widget, row, col)
    
    def _create_metric(self, label, value):
        """Создаёт виджет метрики"""
        group = QGroupBox(label)
        group_layout = QVBoxLayout(group)
        value_label = QLabel(value)
        value_label.setStyleSheet("font-size: 24px; font-weight: bold;")
        group_layout.addWidget(value_label)
        group._value_label = value_label
        return group
    
    def update_metrics(self, stats: dict):
        """Обновляет метрики из статистики прогона"""
        if "total_products" in stats:
            self.metrics["total_products"]._value_label.setText(str(stats["total_products"]))
        if "processed" in stats:
            self.metrics["processed"]._value_label.setText(str(stats["processed"]))
        if "found" in stats:
            self.metrics["found"]._value_label.setText(str(stats["found"]))
        if "success_rate" in stats:
            self.metrics["success_rate"]._value_label.setText(f"{stats['success_rate']:.0%}")
        if "llm_calls" in stats:
            self.metrics["llm_calls"]._value_label.setText(str(stats["llm_calls"]))
        if "avg_llm_time" in stats:
            self.metrics["avg_llm_time"]._value_label.setText(f"{stats['avg_llm_time']:.1f}s")
        if "cache_hits" in stats:
            self.metrics["cache_hits"]._value_label.setText(str(stats["cache_hits"]))
        if "stuck_events" in stats:
            self.metrics["stuck_events"]._value_label.setText(str(stats["stuck_events"]))
        if "blocks" in stats:
            self.metrics["blocks"]._value_label.setText(str(stats["blocks"]))
```

### Критерий завершения Фазы 6:
- [ ] AgentMonitorPanel показывает действия агента в реальном времени
- [ ] Визуализация графа оптимизирована (LOD, culling)
- [ ] MetricsPanel отображает статистику прогона
- [ ] **Тест:** GUI не тормозит при 1000+ нодах графа

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 6: GUI и мониторинг
- AgentMonitorPanel: real-time действия агента
- Оптимизация графа: LOD, culling, упрощённый рендеринг
- MetricsPanel: статистика прогона
- Результат: GUI стабилен при 1000+ нодах
```

---

## ФАЗА 7: ТЕСТИРОВАНИЕ И ДОКУМЕНТАЦИЯ (Неделя 7)

### Цель: Покрытие критичных модулей тестами, обновление документации.

### Задачи:

**7.1. Юнит-тесты для критичных модулей**

*Структура:*
```
tests/
├── __init__.py
├── test_schemas.py           # Pydantic-валидация
├── test_stuck_detector.py    # Обнаружение зацикливания
├── test_semantic_cache.py    # Кэширование
├── test_context_optimizer.py # Оптимизация контекста
├── test_rate_limiter.py      # Rate limiting
├── test_learning_loop.py     # Автообучение
└── test_smart_review.py      # PDF ревью
```

*Пример теста:*
```python
# tests/test_stuck_detector.py
import pytest
from src.stuck_detector import StuckDetector, StuckLevel

def test_detects_cyclic_actions():
    detector = StuckDetector(repeat_threshold=3)
    
    # Записываем одинаковые действия
    for _ in range(3):
        detector.record_action("click", "#search-btn", "no_change")
    
    assert detector.detect() == StuckLevel.CRITICAL

def test_detects_block():
    detector = StuckDetector()
    detector.record_action("navigate", "site.com", "403 Forbidden - captcha required")
    
    assert detector.detect() == StuckLevel.BLOCKED

def test_no_false_positive():
    detector = StuckDetector()
    detector.record_action("navigate", "site.com", "success")
    detector.record_action("click", "#search", "success")
    detector.record_action("type", "#query", "success")
    
    assert detector.detect() == StuckLevel.OK
```

**7.2. Интеграционные тесты**

> ⚠️ **Корректировка:** агентный цикл — функция `async def process_row(...)`, а НЕ класс `AgentLoop` с методом `run()`. Результат — **dict**, а не объект с атрибутом `.found`. Тест должен вызывать `process_row(...)` с моками и проверять ключи словаря.

```python
# tests/integration/test_agent_flow.py
import pytest
from unittest.mock import Mock, AsyncMock

from src.agent_loop import process_row


@pytest.mark.asyncio
class TestAgentFlow:
    """Интеграционные тесты полного цикла агента (process_row)"""

    @pytest.fixture
    def mocks(self):
        llm = Mock()
        llm.chat = AsyncMock(return_value={
            "content": "",
            "tool_calls": [],
            "final": {"price": 100.0, "confidence": 0.95,
                      "url": "https://site.ru/p", "site": "site.ru"},
        })
        mcp = AsyncMock()
        mcp.call_tool = AsyncMock(return_value='{"text": "ok"}')
        engine = Mock()
        mm = Mock()
        stop = Mock()
        return llm, mcp, engine, mm, stop

    def test_full_extraction_flow(self, mocks):
        """Тест полного цикла: process_row возвращает dict с ценой"""
        llm, mcp, engine, mm, stop = mocks
        result = asyncio.run(process_row(
            spec_text="Тестовый товар", llm_client=llm, mcp_bridge=mcp,
            graph_engine=engine, memory_manager=mm, stop_event=stop,
            status_callback=lambda s: None, fresh=False, spec_meta=None,
        ))

        # Результат — dict с ключами контракта
        assert result.get("price") is not None
        assert result.get("confidence", 0) > 0.8
        assert result.get("url") == "https://site.ru/p"
```

**7.3. Обновление документации**

*Файл:* `SPEC_V32.md` (обновлённая спецификация)

```markdown
# Pricer Vision v2.0 — Спецификация

## Архитектурные решения

### Локальная LLM как единая точка
- Все запросы к LLM строго последовательны
- Никакого параллелизма на уровне LLM
- Оптимизация через кэширование и группировку

### Обработка товаров
1. TaskScheduler группирует товары по сайтам
2. SemanticCache проверяет кэш перед запросом к LLM
3. `process_row` (agent_loop) обрабатывает товары последовательно
4. LearningLoop обновляет граф после прогона

### Антидетект
- stealth.js: 17 патчей
- HumanBehavior: имитация человека
- DomainRateLimiter: 20 req/min per domain
- CaptchaDetector: обнаружение без автрешения

### Граф знаний
- ApproachVersioning: success_rate, деградация
- HintManager: TTL 90 дней
- LearningLoop: автообучение из результатов
- SQLite: WAL mode, индексы

## Метрики
- Точность: ≥ 92%
- Время обработки 25 товаров: ≤ 20 мин
- Стабильность: ≥ 99%
- Cache hit rate: ≥ 20% на повторных прогонах
```

### Критерий завершения Фазы 7:
- [ ] Юнит-тесты покрывают критичные модули (>80% coverage)
- [ ] Интеграционные тесты проходят
- [ ] SPEC_V32.md обновлён
- [ ] readme.md и state.md актуальны
- [ ] **Финальный тест:** Полный прогон 25 товаров с метриками

### Запись в `state.md`:
```
## 2026-08-XX — Фаза 7: Тесты и документация
- Юнит-тесты: schemas, stuck_detector, cache, rate_limiter
- Интеграционные тесты: agent flow, stuck recovery
- SPEC_V32.md обновлён
- Финальный прогон: 92% точность, 18 мин, 0 крэшей
```

---

## 📊 ИТОГОВАЯ ТАБЛИЦА ФАЗ

| Фаза | Название | Длительность | Ключевые файлы | Метрика |
|------|----------|-------------|----------------|---------|
| 0 | Подготовка | 1 день | `.env`, `.gitignore`, тег `v1.0-pre-refactor` | 0 секретов в git, точка отката |
| 1 | Стабильность ядра | 1 неделя | `schemas.py`, `stuck_detector.py`, `resilience.py` | Точность 82%, 0 крэшей |
| 2 | Оптимизация LLM-цикла | 1 неделя | `task_scheduler.py`, `semantic_cache.py`, `context_optimizer.py` | 25 товаров за 18 мин |
| 3 | Антидетект | 1 неделя | `stealth.js` (+5 патчей к 12), `human_behavior.py`, `rate_limiter.py` | 0 банов |
| 4 | Граф знаний | 1 неделя | `learning_loop.py`, `memory_manager.py` | +5% точность на повторе |
| 5 | PDF-парсер | 1 неделя | `structurer.py`, `ocr_fallback.py`, `review.py` | 95% покрытие PDF |
| 6 | GUI и мониторинг | 1 неделя | `agent_monitor.py`, `metrics_panel.py` | GUI стабилен при 1000+ нодах |
| 7 | Тесты и документация | 1 неделя | `tests/`, `SPEC_V32.md` | 80% test coverage |

---

## ⚠️ РИСКИ И MITIGATION

| Риск | Вероятность | Влияние | Mitigation |
|------|------------|---------|------------|
| Локальная LLM не справляется с контекстом | Средняя | Высокое | ContextOptimizer, лимит 8000 токенов |
| Stealth.js детектируется новыми антиботами | Средняя | Высокое | Регулярное обновление патчей, мониторинг |
| SQLite bottleneck при масштабировании | Низкая | Среднее | WAL mode, при >10k товаров — PostgreSQL |
| OCR работает медленно | Средняя | Низкое | Кэширование результатов, параллельная обработка страниц |
| GUI тормозит при большом графе | Средняя | Среднее | QGraphicsScene отрисовывает только видимое; порог детализации (labels/physics off при >500 нод) |

---

## ✅ КРИТЕРИЙ УСПЕХА РЕФАКТОРИНГА

После завершения всех 7 фаз система должна:

1. **Обрабатывать 25 товаров за ≤ 20 минут** (было 30+)
2. **Достигать точности ≥ 92%** (было 76%)
3. **Работать без крэшей** (стабильность ≥ 99%)
4. **Не получать баны** на основных сайтах
5. **Переиспользовать знания** между прогонами (cache hit ≥ 20%)
6. **Самообучаться** после каждого прогона
7. **Обрабатывать 95% PDF-форматов** без ручного вмешательства

