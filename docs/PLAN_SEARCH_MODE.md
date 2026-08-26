# ПЛАН: Интеграция «Режим поиска» (три флага памяти) + рейтинг сайтов

Ветка: `feat/search-mode` (от `fix/session-site-learn`)
Статус: **РЕАЛИЗОВАНО (2026-08-26)** — 875 passed, 2 skipped. Остались ручной смоук и A/B-проверка (критерии 2-4 ниже).

---

## 1. Цель

Сгруппировать в UI единый блок из трёх независимых переключателей режима поиска
(память агента) и подключить обобщённый рейтинг сайтов «тип+бренд → сайт»,
который сокращает время поиска: агент стартует с сайтов, где данный тип товара
находился быстрее и успешнее всего.

Пользователю нужен **чистый безрейтинговый режим** для нагрузочного тестирования —
в любой момент можно выключить все три механизма и получить последовательный
поиск по белому списку без памяти.

## 2. Три флага (согласованная семантика)

| Флажок (UI, рус.) | Конфиг-ключ | Положительная семантика | Инверсия/legacy |
|---|---|---|---|
| ☑ Цены из памяти | `run.reuse_price` (по умолч. true) | Переиспользовать подтверждённые цены и семантический кэш (rule-8) | **Инверсия** существующего `run.fresh`: `fresh = not reuse_price`. `run.fresh` остаётся legacy (читается, если `reuse_price` отсутствует) |
| ☑ Подходы | `run.use_approaches` (по умолч. true) | Подсказывать агенту сохранённые шаги поиска из графа | — |
| ☑ Рейтинг сайтов | `run.use_site_ranking` (по умолч. true) | Ранжировать сайты по статистике успешности типа+бренда | — |

Все флаги — **положительные** («использовать X»), единый ментальный паттерн.
Сняты все три = «чистый поиск» (без цен, подходов и рейтинга).

## 3. UI-дизайн (одобрен пользователем)

Отдельная строка-панель **«Режим поиска»** (QFrame) между тулбаром (`btn_frame`,
`main.py:599`) и спиннером (`fb_frame`, `main.py:601`). Высота ~58px, две строки:

```
┌──────────────────────────────────────────────────────────────────────────────┐
│  РЕЖИМ ПОИСКА:                                                              │
│  ☑ Цены из памяти   ☑ Подходы   ☑ Рейтинг сайтов                            │
│  ⓘ Чистый поиск: снять все три флажка                                       │
└──────────────────────────────────────────────────────────────────────────────┘
```

- Заголовок «РЕЖИМ ПОИСКА» (QLabel, стиль темы).
- Три `QCheckBox` с русскими метками и tooltip-подсказками.
- Подсказка-строка «ⓘ Чистый поиск: снять все три флажка» (QLabel muted).
- Изменение на лету (как свежий `fresh`): применяется со следующей позиции,
  перезапуск прогона не нужен.

### Tooltip'ы флажков
- **Цены из памяти**: «Переиспользовать ранее подтверждённые цены (rule-8 и кэш). Снимите для чистого поиска»
- **Подходы**: «Использовать сохранённые шаги поиска по сайтам (граф-память)»
- **Рейтинг сайтов**: «Начинать поиск с сайтов, где этот тип товара находился быстрее всего»

## 4. Поток данных (end-to-end)

```
UI (3 чекбокса, рус.) 
  → main.py: _on_run_mode_toggle() → config_loader.save_run_flags(reuse_price, use_approaches, use_site_ranking)
  → start_processing: MCPAgentRunner(fresh=not reuse_price, use_approaches=..., use_site_ranking=...)
  → mcp_agent_runner._run_async: process_row(..., use_approaches, use_site_ranking)
  → agent_loop.process_row:
       use_approaches=False   → approaches = [] (подходы скрыты из контекста)
       use_site_ranking=False → _build_context._sort_key без рейтинга (порядок белого списка)
       fresh=True             → уже реализовано (rule-8/кэш выключены)
  → live: runner.set_use_approaches() / runner.set_use_site_ranking() со следующей позиции
```

## 5. Конфигурация (`config/settings.yaml → run:`)

```yaml
run:
  fresh: false            # legacy: читается только если reuse_price отсутствует
  reuse_price: true       # НОВОЕ: использование цен из памяти (инверсия fresh)
  use_approaches: true    # НОВОЕ: подходы из графа
  use_site_ranking: true  # НОВОЕ: рейтинг сайтов по статистике
  row_idle_timeout_seconds: 180
  row_max_seconds: 900
  # ... остальные ключи без изменений
```

## 6. Реализация по файлам

### 6.1 `src/config_loader.py`
- `save_run_flags(reuse_price, use_approaches, use_site_ranking)` — запись трёх ключей в `run.*` (единая функция, переиспользует паттерн `save_fresh`).
- `get_run_config("reuse_price", None)`: если ключа нет — fallback `not get_run_config("fresh", True)` (legacy-совместимость).
- `save_fresh` оставить (legacy, вызывает `save_run_flags` с инверсией при необходимости).

### 6.2 `main.py`
- Панель «Режим поиска»: QFrame + QLabel «РЕЖИМ ПОИСКА» + 3 QCheckBox + QLabel «ⓘ Чистый поиск…», вставка между btn_frame и fb_frame.
- Инициализация из `get_run_config`.
- `_on_run_mode_toggle()` → `save_run_flags` + лог режима + live-проброс в runner.
- `start_processing`: `fresh = not self.reuse_price_cb.isChecked()`, проброс `use_approaches`, `use_site_ranking` в `MCPAgentRunner`.
- Лог при старте прогона: «Режим: цены=вкл, подходы=вкл, рейтинг=вкл».
- Убрать старый `fresh_cb` из тулбара (заменяется панелью) ИЛИ оставить как legacy-алиас — решить при реализации (рекомендация: убрать из тулбара, чтобы не дублировать).

### 6.3 `src/mcp_agent_runner.py`
- Конструктор: параметры `use_approaches: bool = True`, `use_site_ranking: bool = True`.
- `set_use_approaches(bool)`, `set_use_site_ranking(bool)` — live-обновление (как `set_fresh`).
- Передача в `process_row`.

### 6.4 `src/agent_loop.py`
- `process_row(..., use_approaches=True, use_site_ranking=True)`.
- `use_approaches=False` → `approaches = []` (скрыть подходы из контекста и получения).
- `_build_context(..., use_site_ranking=True)`: при False `_sort_key` игнорирует рейтинг-профили, порядок = `price_sites → success_sites → approach_sites → priority`.

### 6.5 `src/site_analyzer.py` / `src/learning_loop.py` (рейтинг сайтов)
- **Расширение профиля** до гранулярности `(product_type, brand) → site`:
  - ключ `f"{product_type}|{brand}"` (brand из `SpecItem.brand`; пустой → только тип).
  - поля: `success_rate`, `avg_attempts`, `block_count`, `total_runs`.
- **Учёт неудач по типу**: фиксировать failure в профиле (сайт+тип+бренд) при безуспешном поиске, не только при успехе (дополнить `_update_site_profiles` в `learning_loop.py`).
- **Порог достоверности** `MIN_SAMPLES = 3`: профиль влияет на ранжирование только при `total_runs >= 3` (защита от холодного старта и «пузыря успеха»).
- **Ротация**: растущий `block_count` понижает сайт (анти-пузырь).

### 6.6 Ранжирование (`_build_context._sort_key` + `_determine_target_site`)
- При `use_site_ranking=True`: сайты с профилем `(type, brand)` и `total_runs >= MIN_SAMPLES` ранжируются по
  `score = success_rate*0.5 − avg_attempts/300*0.3 − block_count*0.2` — выше `approach_sites`.
- При `use_site_ranking=False`: текущий порядок без изменений.

## 7. Логирование

- Старт прогона: `Режим: цены=вкл, подходы=вкл, рейтинг=вкл` (для сопоставимости A/B).
- Live-переключение: `Режим: подходы выкл (со следующей позиции)`.

## 8. Тесты

| Файл | Тест |
|---|---|
| `tests/test_config_loader.py` | `save_run_flags` round-trip; legacy `fresh` fallback → `reuse_price`; сохранение других ключей |
| `tests/test_row_idle_timeout.py` | без изменений |
| `tests/test_agent_loop.py` | `use_approaches=False` → approaches скрыты; `use_site_ranking=False` → порядок без рейтинга |
| `tests/test_learning_loop.py` | профиль `(type, brand)`; учёт неудач; `MIN_SAMPLES`; ротация по block_count |
| `tests/integration/test_agent_flow.py` | `use_approaches=False` — агент не получает подходы; live-переключение |
| `tests/test_main.py` (новый) | панель «Режим поиска»: инициализация из конфига, инверсия fresh/reuse_price |

## 9. Критерии готовности

1. `python -m pytest -q` — полный зелёный (ожидается 865+ passed).
2. Ручной смоук: запуск приложения — панель «Режим поиска» на месте, три флажка читаются из конфига.
3. A/B-проверка на vtk_spec_v2.xlsx:
   - все три вкл (по умолчанию) — поведение как сейчас;
   - все три выкл — «чистый поиск», подходы/цены/рейтинг не участвуют, лог фиксирует режим;
   - только «Рейтинг сайтов» — проверить, что первый сайт выбирается по профилю `(тип, бренд)`.
4. Live-переключение флажков во время прогона применяется со следующей позиции (без краша).
5. Ветка: `feat/search-mode` от `fix/session-site-learn`, коммиты конвенциональные (`feat:`, `test:`).

## 10. Риски и митигация

| Риск | Митигация |
|---|---|
| Грубый тип (`valves_armature` включает краны/клапаны/вентили) | Профиль по `(тип, бренд)`, не только типу |
| «Пузырь успеха» (success_rate=1.0 при 1 попытке) | `MIN_SAMPLES=3` до доверия профилю |
| Нет негативных сигналов по типу | Учёт failures в профиле (сайт+тип+бренд) |
| Бренд извлекается слабо (`_brand_of` требует маркер) | Брать из `SpecItem.brand` (уже есть), не из spec_text |
| Инверсия fresh сломает сохранённые конфиги | Legacy fallback: `reuse_price` отсутствует → `not fresh` |
| Переполнение тулбара | Отдельная строка-панель, тулбар не раздувается |

## 11. Порядок реализации

1. `config_loader` + `settings.yaml` (ключи, fallback, тесты)
2. `agent_loop.process_row` + `_build_context` (флаги, тесты)
3. `mcp_agent_runner` (проброс + live-set, тесты)
4. `main.py` (панель «Режим поиска» + стартовый лог, тесты)
5. `learning_loop`/`site_analyzer` (профиль тип+бренд, учёт неудач, MIN_SAMPLES, ротация)
6. `_sort_key`/`_determine_target_site` (рейтинг под флагом)
7. Полный прогон тестов + ручной смоук + A/B
