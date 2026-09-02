# REFACTORING_PLAN.md — План рефакторинга агента (сентябрь 2026)

## Контекст

Прогон 2026-09-02: 41 строка обработана, найдено 2 цены (4.88%). Ключевые проблемы:
- Navigate blocker ловит агента на ложных ценах-кандидатах (22 минуты на одной строке)
- vseinstrumenti.ru всегда падает (type timeout)
- Context trim убивает память агента
- Playwright/nodriver запускаются при выбранном camoufox
- Нет механизма «товар не существует» — агент тратит 15-20 раундов на заведомо пустой поиск

---

## Проблема 1: Слабое определение соответствия товара (P0)

### Диагноз

`price_candidate_seen` ставится на ЛЮБОЙ странице с ценой + токеном из спецификации (`_price_is_relevant`), даже если товар НЕ подходит. Navigate blocker потом не даёт уйти. Цепочка:

1. Агент на странице результатов поиска → видит «1 234 руб» + слово «клапан» → `price_candidate_seen = True`
2. Агент хочет уйти на другой сайт → navigate blocker: «Цена-кандидат найдена»
3. Агент пытается сохранить → `product_name_matches` = False → advisory → флаг сброшен
4. Агент на том же сайте → новый snapshot → снова «цена» → флаг снова True → цикл повторяется

**Код:** `agent_loop.py:1314-1318` — `price_candidate_seen` ставится без проверки `_is_product_card_url`.

### Исправления

| # | Что | Файл:строка | Описание |
|---|-----|-------------|----------|
| 1a | `price_candidate_seen` только на карточках | `agent_loop.py:1314` | Добавить `_is_product_card_url(current_site)` в условие |
| 1b | Reset при всех rejection-путях | `agent_loop.py:1374-1480` | `price_candidate_seen = False` после: нет product_name, family page, confirm rejected (low confidence), brand_mismatch save |
| 1c | Escape hatch navigate blocker | `agent_loop.py:939-962` | Если `facts.navblocks >= 2` и `_is_product_card_url(current_site)` — разрешить уход |
| 1d | Уточнить сообщение blocker | `agent_loop.py:945-950` | После 2+ блоков: «Если товар не подходит — verни null» |

### Ожидаемый эффект
- -80% времени на navigate blocker (с 22 мин до ~3 мин на строку)
- Агент не застревает на чужих карточках

---

## Проблема 2: vseinstrumenti.ru всегда падает (P0)

### Диагноз

3 причины:

1. **Snapshot ref баг:** LLM передаёт `[e706]` (с скобками), regex `_SNAPSHOT_REF_RE = r"^e\d+$"` матчит только `e706`. Result: CSS-селектор `[e706]` → timeout 10с.
   - **Код:** `browser_server.py:334` — `_SNAPSHOT_REF_RE`
   - **Доказательство:** Логи показывают `waiting for locator("[e706")` — скобки не сняты

2. **JS fallback не покрывает placeholder:** `_SEARCH_INPUT_FILL_JS` ищет `input[placeholder*="Поиск"]`, а у vseinstrumenti placeholder = `"Оригинальные товары для стройки, ремонта, производства"`.
   - **Код:** `browser_server.py:340-347` — SELECTORS массив
   - **Доказательство:** Логи показывают `hasForm: false, value: "Муфта соединительная 32"` — fallback не нашёл input

3. **Агрессивный rate limit:** 900с cooldown, 6с interval — после одного failed type сайт блокируется на минуты.
   - **Код:** `settings.yaml:8-11` — `site_overrides.vseinstrumenti.ru`

### Исправления

| # | Что | Файл:строка | Описание |
|---|-----|-------------|----------|
| 2a | Strip brackets из ref | `browser_server.py:~408` | `re.sub(r'^\[(e\d+)\]$', r'\1', t)` в `_resolve_action_target` |
| 2b | Добавить placeholder vseinstrumenti | `browser_server.py:340` | `'input[placeholder*="Оригинальные" i]'` в SELECTORS |
| 2c | Last-resort fallback | `browser_server.py:365` | Первый видимый `<input>` на странице если все селекторы не сработали |
| 2d | Снизить cooldown | `settings.yaml:9` | `cooldown_seconds: 900` → `300` |

### Ожидаемый эффект
- vseinstrumenti.ru работает стабильно
- Snapshot ref баг исправлен для ВСЕХ сайтов (не только vseinstrumenti)

---

## Проблема 3: Внешняя память агента (P1)

### Диагноз

Context trim убивает историю. После round 25 контекст обрезается с 25000 до 9000 токенов. Агент теряет:
- Какие сайты посетил и результаты
- Какие запросы пробовал
- Текущую стратегию поиска

`RowFacts.to_prompt_block()` выводит ~300 токенов — недостаточно.

### 3-уровневое решение

#### Уровень 1: Расширенный `to_prompt_block()` (главный фикс)

**Файл:** `session_facts.py` — класс `RowFacts`

Новые поля:
```python
_queries_per_site: dict[str, list[dict]]  # запросы и результаты по сайтам
_strategy_phase: str                       # exploration/yandex_fallback/save_analog
_confirmed_price: dict | None              # сохранённая цена
_candidate_prices: list[dict]              # виденные кандидаты
_current_site: str                         # текущий домен
_rounds_on_site: int                       # глубина на текущем сайте
```

Новые методы:
```python
def record_query_result(domain, query, result, round_num)
def set_strategy_phase(phase)
def record_confirmed_price(price, site, confidence)
def record_candidate_price(price, site, hint)
def set_current_site(domain, rounds_on_site)
def _recommendation() -> str  # детерминированная рекомендация
```

Расширенный вывод (~600 токенов):
```
ПАМЯТЬ СТРОКИ (переживает обрезку контекста):
РАУНД 25/60 | сайт: santech.ru (8 шагов) | фаза: exploration
ЖУРНАЛ САЙТОВ:
  🟡 santech.ru: посещён | запросы: «LEMAX C10 500»→пусто, «Premium C10»→пусто
  🔴 vseinstrumenti.ru: timeout | запросы: «LEMAX C10»→ошибка
  🟡 lunda.ru: посещён | запросы: «LEMAX Premium»→есть карточки
🔍 КАНДИДАТЫ: 12 450₽@lunda.ru — НЕ СОХРАНЕНЫ
💡 РЕКОМЕНДАЦИЯ: 3+ сайта без цены — попробуй Яндекс
```

#### Уровень 2: Сжатие старых сообщений

**Файл:** `agent_loop.py` — функция `_trim_messages_for_budget()`

Новая функция `_compress_old_messages()`:
- Извлекает ключевые действия (navigate, type, save) из выбрасываемых сообщений
- Вставляет компактную строку ~100 токенов: `[ИСТОРИЯ: navigate→santech, query«LEMAX C10»→пусто, ...]`

#### Уровень 3: Трекер стратегии

**Файл:** `session_facts.py` — класс `StrategyTracker`

Детерминированная фаза поиска:
- `exploration` — прямые сайты
- `yandex_fallback` — поиск через Яндекс
- `save_analog` — сохранение лучшего найденного
- `finished` — поиск завершён

### Ожидаемый эффект
- Агент помнит контекст после context trim
- Меньше повторных запросов на тех же сайтах
- Стратегические решения принимаются быстрее

---

## Проблема 4: Playwright/nodriver при camoufox (P1)

### Диагноз

`resolve_backends()` всегда возвращает `["camoufox", "playwright", "nodriver"]` из `browser.backends`. `MCPBridge.start()` итерирует весь список. `set_backend()` только меняет порядок, не фильтрует. `_restart_safe()` → `restart()` → `start()` — полный re-run failover chain.

**Код:**
- `settings.yaml:14-17` — `backends: [camoufox, playwright, nodriver]`
- `mcp_bridge.py:22-45` — `resolve_backends()` всегда все 3
- `mcp_bridge.py:262-284` — `start()` итерирует цепочку
- `mcp_bridge.py:515-519` — `restart()` → `start()` = re-run

### Исправления

| # | Что | Файл:строка | Описание |
|---|-----|-------------|----------|
| 4a | Добавить `browser.failover` | `settings.yaml` | `failover: false` по умолчанию |
| 4b | В `start()`: если failover=False | `mcp_bridge.py:262-284` | Только `[primary]`, без цепочки |
| 4c | В `restart()`: сохранять backend | `mcp_bridge.py:515-519` | Restart = тот же backend, не полная цепочка |
| 4d | В `resolve_backends()`: failover=False → `[primary]` | `mcp_bridge.py:22-45` | Цепочка = 1 элемент |

### Ожидаемый эффект
- Запускается только camoufox
- Нет накладных расходов на playwright/nodriver
- Restart не переключает backend

---

## Проблема 5: Механизм «товар не существует» (P2)

### Диагноз

Нет агрегации пустых результатов по сайтам. `empty_probe_streak` сбрасывается при смене домена. `_session_no_product` записывает факт, но не завершает текущую строку. Для новых товаров (МКСК трубы) агент тратит 15-20 раундов.

### Компоненты

| # | Что | Файл | Описание |
|---|-----|------|----------|
| 5a | `_global_empty_probes` | `agent_loop.py` | Глобальный счётчик пустых зондов по ВСЕМ сайтам |
| 5b | `_global_empty_sites` | `agent_loop.py` | Множество доменов с пустыми результатами |
| 5c | Hard finish при ≥5 зондов + ≥2 сайтов | `agent_loop.py` | Раннее завершение строки |
| 5d | Guidance при ≥3 зондах | `agent_loop.py` | «Товар вероятно не существует» |
| 5e | `SessionFacts.all_sites_exhausted()` | `session_facts.py` | Кросс-строчное обучение |
| 5f | Fast path в `process_row()` | `agent_loop.py` | Если ≥4 сайта no_product — завершить сразу |
| 5g | `NegativeCache` для UNKNOWN_PT | `agent_loop.py` | `NOT_FOUND_LIMIT = 1` вместо 2 |

### Ожидаемый эффект
- -60% раундов для несуществующих товаров (с 15-20 до 5-8)
- Кросс-строчное обучение: второй раз товар не ищется

---

## Порядок реализации

| Приоритет | Проблема | Ожидаемый эффект | Объём кода | Статус |
|-----------|----------|------------------|-----------|--------|
| **P0** | 1a+1b: price_candidate_seen только на карточках + reset | -80% time на navigate blocker | ~30 строк | 📋 Запланировано |
| **P0** | 2a+2b: bracket strip + vseinstrumenti placeholder | vseinstrumenti работает | ~10 строк | 📋 Запланировано |
| **P1** | 4a+4b: failover=false | Только camoufox | ~15 строк | 📋 Запланировано |
| **P1** | 3: Enhanced to_prompt_block | Агент помнит контекст | ~120 строк | 📋 Запланировано |
| **P2** | 5a-5c: global empty probes | -60% rounds для несуществующих | ~40 строк | 📋 Запланировано |
| **P2** | 2c+2d: last-resort input + rate limit | Устойчивость к новым сайтам | ~15 строк | 📋 Запланировано |

---

## Тесты

После реализации каждого пункта:
1. `venv\Scripts\python.exe -m py_compile <files>` — проверка синтаксиса
2. `venv\Scripts\python.exe -m pytest -q` — все тесты должны пройти
3. Ручной прогон на 2-3 товарах для проверки поведения

---

## Файлы для изменения

| Файл | Изменения |
|------|-----------|
| `src/agent_loop.py` | 1a, 1b, 1c, 1d, 4b, 4c, 5a-5d, 5f, 5g |
| `src/session_facts.py` | 3: расширенный RowFacts, StrategyTracker, all_sites_exhausted |
| `mcp_servers/browser_server.py` | 2a, 2b, 2c |
| `mcp_bridge.py` | 4b, 4c, 4d |
| `config/settings.yaml` | 2d, 4a |
