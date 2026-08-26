# План: облачные LLM-провайдеры (opencode, routerai) + переработка окна настроек

**Ветка:** `feat/cloud-providers` (от `refactor/v2.0`)
**Статус:** в реализации
**Дата:** 2026-08-25

## Цели

1. Добавить провайдеры **opencode** (OpenCode Zen) и **routerai** — те же, что подключены
   к opencode на этой системе.
2. Актуальный список моделей извлекается из `/models` каждого провайдера (не хардкод).
3. Креденшиалы подтягиваются **при каждом запуске из системы** (env → opencode auth.json →
   hermes .env). Ноль секретов в коде/конфиге/git. Перенос проекта на другую систему
   подхватывает ключи новой системы автоматически.
4. Модальное окно настроек LLM серьёзно переработано: комбобоксы, проверка подключения,
   обновление списка моделей, улучшенный дизайн.

## Проверенные факты (живыми запросами)

| Провайдер | Base URL | /models | Моделей | Формат |
|---|---|---|---|---|
| opencode (Zen) | `https://opencode.ai/zen/v1` | `…/zen/v1/models` | 64 | OpenAI `{data:[{id,…}]}` |
| routerai | `https://routerai.ru/api/v1` | `…/api/v1/models` | 466 | OpenAI + name/context_length/pricing |

Ключи в системе: `~/.local/share/opencode/auth.json` (`opencode` → sk-BK4M…,
`routerai` → sk-L_8W…), `~/.hermes/.env` (`ROUTER_API_KEY`). Референс реализации —
hermes (`providers/base.py::fetch_models`, плагины `model-providers/opencode-*`).

## Шаги

### Шаг 1. `src/llm_providers.py` — новый Qt-free модуль
- `Provider` (frozen dataclass): id, name, base_url, api_key_envs, default_model,
  description, requires_key, auth_service_ids.
- Registry `PROVIDERS`: **opencode**, **routerai**, lmstudio, ollama, llamacpp.
- Резолв ключа `resolve_api_key()` — цепочка приоритета:
  1. ручной override сессии (только память процесса);
  2. переменные окружения (`ROUTERAI_API_KEY`, `OPENCODE_ZEN_API_KEY`, …);
  3. `~/.local/share/opencode/auth.json` (serviceID → credential.key);
  4. `~/.hermes/.env` (строки KEY=VALUE).
- `resolve_base_url_override()` — переопределение baseURL из
  `~/.config/opencode/opencode.jsonc` (JSONC, срезание комментариев).
- `parse_models_payload()` — чистый парсер ответа `/models`
  (OpenAI-shape, список строк, мусор); `fetch_models()` — httpx.get + Bearer +
  `User-Agent: PricerVision/2.0`.
- Кэш моделей: память сессии + диск `data/llm_providers_cache.json` (TTL 6 ч).
- `key_fingerprint()` — маскирование (`sk-BK4M…Ewx`) для логов/UI.
- Фабрика `create_llm_client(config, temperature=None)` — единая точка сборки
  LLMClient из конфига (fallback-цепочка только для локальных провайдеров).

### Шаг 2. `src/llm_client.py`
- Новые параметры `api_key`, `headers`; Bearer-заголовок в `_try_chat` и `detect_model`.

### Шаг 3. Конфигурация
```yaml
llm:
  provider: lmstudio        # активный провайдер (дефолт сохраняет текущее поведение)
  model: local-model
  temperature: 0.3
  timeout: 150
  providers:
    opencode: {base_url: https://opencode.ai/zen/v1}
    routerai: {base_url: https://routerai.ru/api/v1}
    lmstudio: {base_url: http://localhost:1234/v1}
    ollama:   {base_url: http://localhost:11434/v1}
    llamacpp: {base_url: http://localhost:8080/v1}
```
- В settings.yaml НЕ хранятся ключи. `config_loader`: `get_llm_config()`,
  `save_llm_settings(provider, model, temperature, timeout, base_urls)`.

### Шаг 4. Переработка `SettingsDialog` (main.py)
- QComboBox «Провайдер» (+ описание), QComboBox «Модель» (editable, заполняется из /models).
- Base URL (editable, дефолт от провайдера/override), API-ключ (password echo)
  + бейдж источника («из системы: opencode auth.json · sk-L_8W…pJo») + кнопка «🔑 Из системы».
- «🔄 Обновить модели», «🔌 Проверить подключение» — через `ModelsFetchWorker(QThread)`,
  UI не блокируется; статус-метка цветами темы.
- Temperature/timeout спинбоксы. Сохранение через `save_llm_settings`;
  ручной ключ живёт только в памяти сессии.

### Шаг 5. Потребители
- `main.py:start_processing`, `_load_pdf` и `study_runner.py` переходят на
  фабрику `create_llm_client`. Fallback на локальные URL — только когда провайдер локальный.

### Шаг 6. Тесты
- Новый `tests/test_llm_providers.py`: реестр, completions_url, цепочка резолва
  (tmp home + monkeypatch), приоритеты, fingerprint, parse_models_payload,
  JSONC-оверрайд, кэш TTL/диск.
- `test_llm_client.py`: Authorization при наличии ключа / отсутствие без него,
  слияние кастомных заголовков.
- `test_config_loader.py`: round-trip save_llm_settings/get_llm_config.

## Ограничения и решения
- Дефолтный провайдер — `lmstudio`: текущее поведение не меняется молча; переключение
  одним кликом в диалоге.
- Облачные провайдеры без fallback на localhost (маскирование ошибок и неожиданные
  расходы недопустимы).
- Ручной ключ не персистится — соответствует требованию «креденшиалсы каждый раз из системы».
