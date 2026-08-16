# Pricer Vision v2.0 — Спецификация

**Version:** 32.0 (пост-рефакторинг v2.0, фазы 1–7)
**Browser:** @playwright/mcp (Playwright MCP over stdio)
**LLM Strategy:** Agent-based — LLM автономно навигирует по сайтам через MCP tools + граф знаний
**Status:** Refactored — стабильный core, 434 теста

---

## 1. Архитектурные решения

### Локальная LLM как единая точка
- Все запросы к LLM строго последовательны (никакого параллелизма на уровне LLM)
- `llm_client.py` — HTTP-клиент к LM Studio/Ollama/llama.cpp с retry и exponential backoff
- `_query_llm` в `agent_loop.py` — единая обёртка: circuit breaker (`llm_circuit` 3/30s), температуры фаз, контекстный бюджет 8000 токенов, замер времени (`monitor_callback("llm_call", elapsed)`)
- Температуры по фазам: `TEMP_EXPLORATION=0.7`, `TEMP_NAVIGATION=0.3`, `TEMP_EXTRACTION=0.1`, `TEMP_RECOVERY=0.5`

### Стабильность ядра (Фаза 1)
- Pydantic-валидация контракта `process_row` через `ExtractionResult` (`src/models/schemas.py`); `model_validator(mode="after")` (pydantic 2.13 не запускает `field_validator` на дефолтах)
- `CircuitBreaker` (`src/resilience.py`): `llm_circuit` (3/30s), `mcp_circuit` (5/60s); `retry_with_backoff`
- `StuckDetector` (`src/stuck_detector.py`): CRITICAL → принудительный уход с сайта при `rounds_on_site > 5`
- `AuditLogger` (`src/audit_logger.py`): JSONL в `data/audit/session_*.jsonl`

### Оптимизация под локальную LLM (Фаза 2)
- `TaskScheduler` — группировка товаров по целевым сайтам, приоритизация батчей
- `SemanticCache` — Jaccard-кэш похожих товаров (`data/semantic_cache.json`, лимит 1000)
- `AdaptiveRoundManager` — динамические лимиты раундов per-site
- Контекстный бюджет: `_estimate_tokens` (≈len/4), `_trim_messages_for_budget` (8000 токенов)

### Антидетект (Фаза 3)
- `config/stealth.js` — 17 патчей (webdriver, WebGL, Canvas, AudioContext, WebRTC, Fonts и др.)
- `HumanBehavior` — человеческие клики/печать/скролл
- `DomainRateLimiter` — 20 req/min per domain (настраивается)
- `SiteAnalyzer` — детекция SPA/SSR/антибота
- `CaptchaDetector` — детекция без авторешения; рекомендации: SWITCH_SITE/WAIT_60S_AND_RETRY/ASK_USER

### Граф знаний (Фаза 4)
- `ApproachVersioning` — эффективность подходов: `score = success_rate*0.7 + freshness*0.3`
- `HintManager` — TTL хинтов (по умолчанию 90 дней), `cleanup_expired()`
- `LearningLoop` — автообучение из результатов прогона, профили сайтов в `data/site_profiles.json`
- SQLite: WAL, `synchronous=NORMAL`, кэш 64MB, `temp_store=MEMORY`

### PDF-парсер (Фаза 5)
- MinerU (изолированный Python 3.11) → OCR-fallback для сканов → `SpecStructurer` (LLM-опция `use_llm` → fallback pipe-парсинг) → `SmartReview` (confidence scoring, авто-утверждение ≥0.8) → ReviewDialog

### GUI и мониторинг (Фаза 6)
- `AgentMonitorPanel` — real-time действия агента (вкладка «Мониторинг»)
- `MetricsPanel` — 9 метрик прогона (найдено/успешность/LLM/кэш/застревания/блокировки)
- Оптимизация графа: LOD (>500 нод — подписи и физика off), троттлинг физики ~30fps, лимит 1000 нод

## 2. Обработка товаров

1. `TaskScheduler` группирует товары по сайтам (`ordered_specs`)
2. `SemanticCache` проверяет кэш перед запросом к LLM (если не `fresh`)
3. `process_row` (`agent_loop.py`) обрабатывает товары последовательно (контракт `ExtractionResult`)
4. `LearningLoop` обновляет граф после прогона (`consolidate_after_run`)

### Контракт `process_row`
```
async def process_row(
    spec_text, llm_client, mcp_bridge, graph_engine, memory_manager,
    stop_event=None, status_callback=None, fresh=True, spec_meta=None,
    semantic_cache=None, monitor_callback=None,
) -> dict
```
Результат — dict с ключами: `spec_text`, `product_type`, `price`, `confidence`, `url`, `site`, `reason`, `requires_review`, `error`, `elapsed`.

### Мониторинг-события `monitor_callback(event_type, value)`
- `("llm_call", elapsed_seconds)` — каждый успешный LLM-запрос
- `("cache_hit", similarity)` — попадание в semantic cache
- `("stuck", None)` — StuckDetector CRITICAL (зацикливание)
- `("block", captcha_type)` — обнаружена captcha/блокировка

## 3. Антидетект

- stealth.js: 17 патчей
- HumanBehavior: имитация человека
- DomainRateLimiter: 20 req/min per domain
- CaptchaDetector: обнаружение без авторешения

## 4. Граф знаний

- ApproachVersioning: success_rate, деградация
- HintManager: TTL 90 дней
- LearningLoop: автообучение из результатов
- SQLite: WAL mode, прагмы производительности

## 5. Тестирование

- 434 теста: 9 интеграционных (агентный цикл `process_row` с моками), юнит-тесты критичных модулей
- Критичные модули покрыты >80%: schemas (96%), stuck_detector (100%), semantic_cache (95%), context_optimizer (100%), rate_limiter (100%), learning_loop (89%), smart_review (100%), config_loader (100%), excel_writer (97%)
- Запуск: `python -m pytest -q`
- Покрытие: `python -m coverage run --source=src -m pytest tests -q && python -m coverage report`

## 6. Метрики

- Точность: ≥ 92%
- Время обработки 25 товаров: ≤ 20 мин
- Стабильность: ≥ 99%
- Cache hit rate: ≥ 20% на повторных прогонах
- GUI: стабилен при 1000+ нодах графа (LOD)

---

## Приложение. Карта фаз рефакторинга v2.0

| Фаза | Название | Ключевые файлы | Тег |
|------|----------|----------------|-----|
| 0 | Подготовка | `.gitignore`, тег `v1.0-pre-refactor` | `v1.0-pre-refactor` |
| 1 | Стабильность ядра | `schemas.py`, `stuck_detector.py`, `resilience.py`, `audit_logger.py` | `phase-1-done` |
| 2 | Оптимизация LLM-цикла | `task_scheduler.py`, `semantic_cache.py`, `adaptive_limits.py` | `phase-2-done` |
| 3 | Антидетект | `stealth.js`, `human_behavior.py`, `rate_limiter.py`, `site_analyzer.py`, `captcha_detector.py` | `phase-3-done` |
| 4 | Граф знаний | `learning_loop.py`, `memory_manager.py` | `phase-4-done` |
| 5 | PDF-парсер | `structurer.py`, `ocr_fallback.py`, `review.py` | `phase-5-done` |
| 6 | GUI и мониторинг | `agent_monitor.py`, `metrics_panel.py`, `graph_explorer.py` | `phase-6-done` |
| 7 | Тесты и документация | `tests/`, `SPEC_V32.md` | `phase-7-done` |
