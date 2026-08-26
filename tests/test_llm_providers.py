"""Тесты src/llm_providers.py: реестр провайдеров, динамический резолв креденшиалов,
парсер /models, JSONC-оверрайд base URL, кэш, фабрика LLMClient."""

import json
import time

import pytest

import src.llm_providers as lp


@pytest.fixture(autouse=True)
def _clean_manual_keys():
    lp._MANUAL_KEYS.clear()
    with lp._CACHE_LOCK:
        lp._MODELS_CACHE.clear()
    yield
    lp._MANUAL_KEYS.clear()
    with lp._CACHE_LOCK:
        lp._MODELS_CACHE.clear()


class TestRegistry:
    def test_required_providers_present(self):
        for pid in ("opencode", "routerai", "lmstudio", "ollama", "llamacpp"):
            assert pid in lp.PROVIDERS

    def test_opencode_base_url(self):
        assert lp.PROVIDERS["opencode"].base_url == "https://opencode.ai/zen/v1"

    def test_routerai_base_url(self):
        assert lp.PROVIDERS["routerai"].base_url == "https://routerai.ru/api/v1"

    def test_local_providers_keyless(self):
        for pid in ("lmstudio", "ollama", "llamacpp"):
            assert not lp.PROVIDERS[pid].requires_key

    def test_unknown_provider_falls_back_to_lmstudio(self):
        assert lp.get_provider("no-such").id == "lmstudio"


class TestCompletionsUrl:
    def test_appends_path(self):
        assert lp.completions_url("https://x.ru/api/v1") == "https://x.ru/api/v1/chat/completions"

    def test_strips_trailing_slash(self):
        assert lp.completions_url("https://x.ru/api/v1/") == "https://x.ru/api/v1/chat/completions"

    def test_keeps_existing_path(self):
        assert lp.completions_url("https://x.ru/v1/chat/completions") == "https://x.ru/v1/chat/completions"


class TestResolveApiKey:
    @staticmethod
    def _write_auth_json(path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding="utf-8")
        return path

    @staticmethod
    def _write_env_file(path, pairs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("\n".join(f"{k}={v}" for k, v in pairs.items()) + "\n", encoding="utf-8")
        return path

    def test_env_wins_over_files(self, tmp_path, monkeypatch):
        auth = self._write_auth_json(tmp_path / "a" / "auth.json",
                                     {"opencode": {"credential": {"key": "sk-auth"}}})
        monkeypatch.setattr(lp, "opencode_auth_candidates", lambda: [auth])
        monkeypatch.setattr(lp, "hermes_env_candidates", lambda: [])
        key, src = lp.resolve_api_key("opencode", env={"OPENCODE_ZEN_API_KEY": "sk-env"})
        assert key == "sk-env"
        assert src == lp.SOURCE_ENV

    def test_opencode_auth_json(self, tmp_path, monkeypatch):
        auth = self._write_auth_json(tmp_path / "a" / "auth.json", {
            "opencode": {"type": "api", "key": "sk-zen"},
            "routerai": {"type": "api", "key": "sk-ra"},
        })
        monkeypatch.setattr(lp, "opencode_auth_candidates", lambda: [auth])
        monkeypatch.setattr(lp, "hermes_env_candidates", lambda: [])
        key, src = lp.resolve_api_key("opencode", env={})
        assert (key, src) == ("sk-zen", lp.SOURCE_OPENCODE)
        assert lp.resolve_api_key("routerai", env={})[0] == "sk-ra"

    def test_hermes_env_file(self, tmp_path, monkeypatch):
        env_file = self._write_env_file(tmp_path / "h" / ".env", {"ROUTERAI_API_KEY": "sk-hermes"})
        monkeypatch.setattr(lp, "opencode_auth_candidates", lambda: [])
        monkeypatch.setattr(lp, "hermes_env_candidates", lambda: [env_file])
        key, src = lp.resolve_api_key("routerai", env={})
        assert (key, src) == ("sk-hermes", lp.SOURCE_HERMES)

    def test_priority_env_then_auth_then_hermes(self, tmp_path, monkeypatch):
        auth = self._write_auth_json(tmp_path / "a" / "auth.json", {"routerai": {"key": "sk-a"}})
        env_file = self._write_env_file(tmp_path / "h" / ".env", {"ROUTERAI_API_KEY": "sk-h"})
        monkeypatch.setattr(lp, "opencode_auth_candidates", lambda: [auth])
        monkeypatch.setattr(lp, "hermes_env_candidates", lambda: [env_file])

        k1, s1 = lp.resolve_api_key("routerai", env={"ROUTERAI_API_KEY": "sk-e"})
        k2, s2 = lp.resolve_api_key("routerai", env={})
        assert (k1, s1) == ("sk-e", lp.SOURCE_ENV)
        assert (k2, s2) == ("sk-a", lp.SOURCE_OPENCODE)

    def test_manual_override_beats_everything(self, monkeypatch):
        lp.set_manual_key("routerai", "sk-manual")
        key, src = lp.resolve_api_key("routerai", env={"ROUTERAI_API_KEY": "sk-env"})
        assert (key, src) == ("sk-manual", lp.SOURCE_OVERRIDE)

    def test_not_found_returns_empty(self, monkeypatch):
        monkeypatch.setattr(lp, "opencode_auth_candidates", lambda: [])
        monkeypatch.setattr(lp, "hermes_env_candidates", lambda: [])
        key, src = lp.resolve_api_key("opencode", env={})
        assert key == ""
        assert src == lp.SOURCE_NONE


class TestFingerprint:
    def test_long_key_masked(self):
        fp = lp.key_fingerprint("sk-BK4MvZBW4MUjE9TTwXZ8M7yGltAzdELOJqYgUzs6IBJDwLvNnvY3sRZHF6fJiEwx")
        assert fp.startswith("sk-BK4")
        assert fp.endswith("Ewx")
        assert len(fp) < 15

    def test_short_key_hidden(self):
        assert lp.key_fingerprint("short") == "***"

    def test_empty(self):
        assert lp.key_fingerprint("") == "—"


class TestParseModelsPayload:
    def test_openai_shape(self):
        payload = {"data": [{"id": "gpt-5", "object": "model"}, {"id": "claude-5"}]}
        models = lp.parse_models_payload(payload)
        assert [m["id"] for m in models] == ["gpt-5", "claude-5"]

    def test_routerai_rich_shape(self):
        payload = {"data": [{
            "id": "z-ai/glm-4.6",
            "name": "Z.ai: GLM 4.6",
            "context_length": 204800,
        }]}
        models = lp.parse_models_payload(payload)
        assert models[0] == {"id": "z-ai/glm-4.6", "name": "Z.ai: GLM 4.6", "context_length": 204800}

    def test_bare_list_of_strings(self):
        models = lp.parse_models_payload(["m1", "m2"])
        assert [m["id"] for m in models] == ["m1", "m2"]

    def test_dedup_and_garbage_skipped(self):
        payload = {"data": [{"id": "a"}, {"id": "a"}, {}, {"id": ""}, "b"]}
        models = lp.parse_models_payload(payload)
        assert [m["id"] for m in models] == ["a", "b"]

    def test_garbage_payload_returns_empty(self):
        assert lp.parse_models_payload(None) == []
        assert lp.parse_models_payload({"unexpected": 1}) == []


class TestJsoncOverride:
    def _write_config(self, path, content):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def test_reads_baseurl(self, tmp_path, monkeypatch):
        cfg = tmp_path / "cfg"
        self._write_config(cfg / "opencode" / "opencode.jsonc", json.dumps({
            "provider": {"routerai": {"options": {"baseURL": "https://mirror.routerai.ru/api/v1"}}},
        }))
        monkeypatch.setattr(lp, "opencode_config_candidates", lambda: [cfg / "opencode" / "opencode.jsonc"])
        assert lp.resolve_base_url_override("routerai") == "https://mirror.routerai.ru/api/v1"

    def test_jsonc_comments_stripped(self, tmp_path, monkeypatch):
        cfg = tmp_path / "cfg"
        content = '''{
          // комментарий
          "provider": { /* блок */ "lmstudio": {"options": {"baseURL": "http://127.0.0.1:1234/v1"}} }
        }'''
        self._write_config(cfg / "opencode" / "opencode.jsonc", content)
        monkeypatch.setattr(lp, "opencode_config_candidates", lambda: [cfg / "opencode" / "opencode.jsonc"])
        assert lp.resolve_base_url_override("lmstudio") == "http://127.0.0.1:1234/v1"

    def test_missing_file_returns_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(lp, "opencode_config_candidates", lambda: [tmp_path / "none.jsonc"])
        assert lp.resolve_base_url_override("routerai") == ""


class TestModelsCache:
    def test_store_and_hit(self):
        lp.store_models_cache("opencode", [{"id": "m1", "name": "", "context_length": None}])
        cached = lp.cached_models("opencode")
        assert cached and cached[0]["id"] == "m1"

    def test_ttl_expiry(self, monkeypatch):
        lp.store_models_cache("routerai", [{"id": "m", "name": "", "context_length": None}])
        real_time = time.time
        monkeypatch.setattr(lp.time, "time", lambda: real_time() + lp.CACHE_TTL_SECONDS + 10)
        assert lp.cached_models("routerai") is None

    def test_disk_roundtrip(self, tmp_path, monkeypatch):
        disk = tmp_path / "cache.json"
        monkeypatch.setattr(lp, "_disk_cache_path", lambda: disk)
        with lp._CACHE_LOCK:
            lp._MODELS_CACHE.clear()
        lp.store_models_cache("opencode", [{"id": "dm", "name": "n", "context_length": None}])
        assert disk.is_file()
        raw = json.loads(disk.read_text(encoding="utf-8"))
        assert raw["opencode"]["models"][0]["id"] == "dm"
        with lp._CACHE_LOCK:
            lp._MODELS_CACHE.clear()
        cached = lp.cached_models("opencode")
        assert cached and cached[0]["id"] == "dm"


class TestCreateLLMClient:
    def _config(self, provider="routerai"):
        return {"llm": {
            "provider": provider,
            "model": "deepseek/deepseek-v4-flash",
            "temperature": 0.3,
            "timeout": 150,
            "providers": {"routerai": {"base_url": "https://routerai.ru/api/v1"}},
        }}

    def test_cloud_client_gets_system_key_and_headers(self, monkeypatch):
        monkeypatch.setattr(lp, "resolve_api_key", lambda p: ("sk-live", lp.SOURCE_OPENCODE))
        client = lp.create_llm_client(self._config())
        assert client.api_key == "sk-live"
        assert client.headers.get("User-Agent") == lp.USER_AGENT
        assert "Bearer sk-live" == client._request_headers()["Authorization"]
        assert client._fallback_urls == []

    def test_local_client_gets_fallbacks_no_key(self):
        client = lp.create_llm_client({"llm": {"provider": "lmstudio", "model": "qwen"}})
        assert client.api_key == ""
        assert len(client._fallback_urls) >= 3

    def test_temperature_override(self, monkeypatch):
        monkeypatch.setattr(lp, "resolve_api_key", lambda p: ("", lp.SOURCE_NONE))
        client = lp.create_llm_client(self._config(), temperature=0.1)
        assert client.temperature == 0.1

    def test_opencode_attribution_headers_merged(self, monkeypatch):
        monkeypatch.setattr(lp, "resolve_api_key", lambda p: ("sk-live", lp.SOURCE_OPENCODE))
        client = lp.create_llm_client(self._config("opencode"))
        assert client.headers.get("HTTP-Referer") == "https://opencode.ai"
        assert client.headers.get("X-Title") == "Pricer Vision"
        assert client.headers.get("User-Agent") == lp.USER_AGENT
