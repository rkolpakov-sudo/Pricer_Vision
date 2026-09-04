"""Тесты менеджера сессий и сериализации кэшей."""

import json
from pathlib import Path

import pytest

from src.session_manager import (
    save_session, load_session, list_sessions, has_current_session,
    load_current_session, archive_current_session, delete_session,
    SESSION_VERSION,
)
from src.session_cache import NegativeCache, SiteBlacklist
from src.session_facts import SessionFacts
from src.skip_registry import SkipRegistry


class TestSessionManager:
    def test_save_and_load(self, tmp_path):
        path = str(tmp_path / "test.json")
        state = {
            "spec_path": "data/input/spec.xlsx",
            "total_rows": 50,
            "results": [{"spec_text": "Кран", "price": 100.0, "excel_row": 2}],
            "run_flags": {"reuse_price": True},
        }
        save_session(path, state)
        loaded = load_session(path)
        assert loaded["version"] == SESSION_VERSION
        assert loaded["spec_path"] == "data/input/spec.xlsx"
        assert loaded["total_rows"] == 50
        assert loaded["processed_count"] == 1
        assert loaded["found_count"] == 1
        assert len(loaded["results"]) == 1
        assert loaded["results"][0]["price"] == 100.0
        assert loaded["run_flags"]["reuse_price"] is True

    def test_save_empty_results(self, tmp_path):
        path = str(tmp_path / "empty.json")
        save_session(path, {"spec_path": "x.xlsx", "results": []})
        loaded = load_session(path)
        assert loaded["processed_count"] == 0
        assert loaded["found_count"] == 0

    def test_load_nonexistent_returns_empty(self):
        result = load_session("/nonexistent/path.json")
        assert result == {}

    def test_list_sessions_sorted(self, tmp_path):
        for i, name in enumerate(["a.json", "b.json", "c.json"]):
            p = str(tmp_path / name)
            save_session(p, {"spec_path": f"file{i}.xlsx", "results": [],
                             "saved_at": f"2026-08-{29-i:02d}T10:00:00"})
        sessions = list_sessions(str(tmp_path))
        assert len(sessions) == 3
        # sorted by saved_at descending — newest first
        assert sessions[0]["spec_name"] == "file0"
        assert sessions[2]["spec_name"] == "file2"

    def test_list_sessions_skips_current(self, tmp_path):
        save_session(str(tmp_path / "_current.json"), {"spec_path": "x.xlsx", "results": []})
        save_session(str(tmp_path / "real.json"), {"spec_path": "y.xlsx", "results": []})
        sessions = list_sessions(str(tmp_path))
        assert len(sessions) == 1
        assert sessions[0]["spec_name"] == "y"

    def test_list_sessions_skips_private_and_backup(self, tmp_path):
        """Служебные/бэкап-файлы (_current_backup_*.json) не должны светиться
        в «Выборе сессии» как дубль текущей сессии."""
        save_session(str(tmp_path / "_current.json"), {"spec_path": "x.xlsx", "results": []})
        save_session(str(tmp_path / "_current_backup_20260903_171811.json"),
                     {"spec_path": "x.xlsx", "results": []})
        save_session(str(tmp_path / "real.json"), {"spec_path": "y.xlsx", "results": []})
        sessions = list_sessions(str(tmp_path))
        names = [s["spec_name"] for s in sessions]
        assert names == ["y"]
        assert len(sessions) == 1

    def test_delete_session(self, tmp_path):
        path = str(tmp_path / "del.json")
        save_session(path, {"spec_path": "x.xlsx", "results": []})
        assert delete_session(path) is True
        assert not Path(path).exists()

    def test_delete_nonexistent_returns_false(self):
        assert delete_session("/nonexistent/file.json") is False

    def test_has_current_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session_manager._sessions_dir", lambda: tmp_path)
        assert has_current_session() is False
        save_session(str(tmp_path / "_current.json"), {"spec_path": "x.xlsx", "results": []})
        assert has_current_session() is True

    def test_archive_current_session(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session_manager._sessions_dir", lambda: tmp_path)
        current = str(tmp_path / "_current.json")
        save_session(current, {"spec_path": "data/input/vtk.xlsx", "results": []})
        result = archive_current_session("data/input/vtk.xlsx")
        assert result is not None
        assert "vtk" in result
        assert not Path(current).exists()
        assert Path(result).exists()

    def test_archive_no_current_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("src.session_manager._sessions_dir", lambda: tmp_path)
        assert archive_current_session() is None

    def test_restore_set_from_list(self, tmp_path):
        path = str(tmp_path / "test.json")
        state = {
            "spec_path": "x.xlsx",
            "results": [],
            "site_blacklist": {"successful": ["site1", "site2"]},
            "skip_registry": {"excluded": ["key1"]},
        }
        save_session(path, state)
        loaded = load_session(path)
        assert isinstance(loaded["site_blacklist"]["successful"], set)
        assert "site1" in loaded["site_blacklist"]["successful"]
        assert isinstance(loaded["skip_registry"]["excluded"], set)
        assert "key1" in loaded["skip_registry"]["excluded"]


class TestNegativeCacheSerialization:
    def test_round_trip(self):
        cache = NegativeCache()
        cache.record("item1")
        cache.record("item1")
        cache.record("item2")
        d = cache.to_dict()
        cache2 = NegativeCache()
        cache2.from_dict(d)
        assert cache2.count("item1") == 2
        assert cache2.count("item2") == 1
        assert cache2.is_blocked("item1") is True

    def test_empty_round_trip(self):
        cache = NegativeCache()
        d = cache.to_dict()
        cache2 = NegativeCache()
        cache2.from_dict(d)
        assert len(cache2) == 0


class TestSiteBlacklistSerialization:
    def test_round_trip(self):
        bl = SiteBlacklist()
        for _ in range(bl._limit):
            bl.strike("s1", "timeout")
        bl.mark_success("s2")
        d = bl.to_dict()
        bl2 = SiteBlacklist()
        bl2.from_dict(d)
        assert bl2.is_blocked("s1") is True
        assert bl2.is_blocked("s2") is False
        assert bl2._successful == {"s2"}

    def test_empty_round_trip(self):
        bl = SiteBlacklist()
        d = bl.to_dict()
        bl2 = SiteBlacklist()
        bl2.from_dict(d)
        assert len(bl2) == 0


class TestSessionFactsSerialization:
    def test_round_trip(self):
        sf = SessionFacts()
        sf.record_success("plumbing_heating_radiators", "LEMAX", "mircli.ru",
                          url="https://mircli.ru/radiator", query="LEMAX C10")
        d = sf.to_dict()
        sf2 = SessionFacts()
        sf2.from_dict(d)
        # _status keys are ((product_type|brand), site)
        key = sf2._key("plumbing_heating_radiators", "LEMAX")
        site = sf2._norm_site("mircli.ru")
        assert sf2._status.get((key, site)) == "has_product"
        assert sf2._working.get(site, {}).get("queries") == ["LEMAX C10"]

    def test_empty_round_trip(self):
        sf = SessionFacts()
        d = sf.to_dict()
        sf2 = SessionFacts()
        sf2.from_dict(d)
        assert len(sf2._status) == 0


class TestSkipRegistrySerialization:
    def test_round_trip(self):
        sr = SkipRegistry()
        sr.mark("Кран Ду15", "LEMAX")
        sr.mark("Клапан Ду20")
        d = sr.to_dict()
        sr2 = SkipRegistry()
        sr2.from_dict(d)
        assert sr2.is_skipped("Кран Ду15", "LEMAX") is True
        assert sr2.is_skipped("Клапан Ду20") is True
        assert sr2.is_skipped("Несуществующий товар") is False

    def test_empty_round_trip(self):
        sr = SkipRegistry()
        d = sr.to_dict()
        sr2 = SkipRegistry()
        sr2.from_dict(d)
        assert len(sr2) == 0


class TestFullSessionRoundTrip:
    def test_full_state_round_trip(self, tmp_path):
        path = str(tmp_path / "full.json")
        nc = NegativeCache()
        nc.record("item")
        nc.record("item")
        bl = SiteBlacklist()
        for _ in range(bl._limit):
            bl.strike("s1", "timeout")
        sf = SessionFacts()
        sf.record_success("plumbing_heating_radiators", "LEMAX", "mircli.ru")
        sr = SkipRegistry()
        sr.mark("Товар X", "BrandY")

        state = {
            "spec_path": "data/input/spec.xlsx",
            "total_rows": 10,
            "results": [
                {"spec_text": "Товар X", "price": 500.0, "excel_row": 2,
                 "site": "s1", "confidence": 0.9},
                {"spec_text": "Товар Y", "price": None, "excel_row": 3,
                 "error": "not_found"},
            ],
            "negative_cache": nc.to_dict(),
            "site_blacklist": bl.to_dict(),
            "session_facts": sf.to_dict(),
            "skip_registry": sr.to_dict(),
            "run_flags": {"reuse_price": True, "use_approaches": False},
            "log_entries": [{"level": "INFO", "phase": "init", "msg": "test"}],
        }
        save_session(path, state)
        loaded = load_session(path)

        assert loaded["processed_count"] == 2
        assert loaded["found_count"] == 1
        assert loaded["results"][0]["price"] == 500.0

        nc2 = NegativeCache()
        nc2.from_dict(loaded["negative_cache"])
        assert nc2.count("item") == 2

        bl2 = SiteBlacklist()
        bl2.from_dict(loaded["site_blacklist"])
        assert bl2.is_blocked("s1") is True

        sf2 = SessionFacts()
        sf2.from_dict(loaded["session_facts"])
        key = sf2._key("plumbing_heating_radiators", "LEMAX")
        site = sf2._norm_site("mircli.ru")
        assert sf2._status.get((key, site)) == "has_product"

        sr2 = SkipRegistry()
        sr2.from_dict(loaded["skip_registry"])
        assert sr2.is_skipped("Товар X", "BrandY") is True

        assert loaded["run_flags"]["reuse_price"] is True
        assert len(loaded["log_entries"]) == 1
