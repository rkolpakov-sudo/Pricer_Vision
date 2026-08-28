"""Smoke-тест панели «Режим поиска» (main.py) в offscreen-режиме.

Создаёт MainWindow с замоканными тяжёлыми зависимостями (БД, YAML, тема,
PDF-runner) и проверяет, что три флажка режима поиска инициализируются из
конфига, а переключение инвертирует fresh.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtWidgets import QApplication


@pytest.fixture
def qapp():
    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("QApplication не может быть создан")
    yield app


class _FakeEngine:
    _all_products = {}
    _conn = None

    def build(self):
        pass

    def load_yaml_seed(self, path):
        pass

    def classify_product_type(self, t):
        return "unknown"


class _FakeExcelWriter:
    def __init__(self, cfg):
        pass


def _monkey_window(monkeypatch, tmp_path, cfg_text):
    """Мокает зависимости MainWindow и создаёт окно."""
    import src.config_loader as cl
    import src.approach_relevance as ar
    import main as main_mod

    target = tmp_path / "config" / "settings.yaml"
    target.parent.mkdir(parents=True)
    target.write_text(cfg_text, encoding="utf-8")
    monkeypatch.setattr(cl.os.path, "dirname", lambda p: str(tmp_path / "proj"))
    monkeypatch.setattr(cl, "_SETTINGS_CACHE", None)

    monkeypatch.setattr(main_mod, "GraphEngine", lambda *a, **k: _FakeEngine())
    monkeypatch.setattr(main_mod, "ExcelWriter", _FakeExcelWriter)
    monkeypatch.setattr(main_mod, "detect_system_theme", lambda: "dark")
    monkeypatch.setattr(ar, "load_rules", lambda *a, **k: {})
    return main_mod.MainWindow()


def test_panel_initialized_from_config(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path,
                         "run:\n  reuse_price: false\n  use_approaches: false\n  use_site_ranking: false\n")
    try:
        assert win.reuse_price_cb.isChecked() is False
        assert win.use_approaches_cb.isChecked() is False
        assert win.use_site_ranking_cb.isChecked() is False
    finally:
        win.close()


def test_panel_defaults_on(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        assert win.reuse_price_cb.isChecked() is True
        assert win.use_approaches_cb.isChecked() is True
        assert win.use_site_ranking_cb.isChecked() is True
    finally:
        win.close()


def test_toggle_reuse_price_writes_config(qapp, monkeypatch, tmp_path):
    import src.config_loader as cl
    win = _monkey_window(monkeypatch, tmp_path, "run:\n  reuse_price: true\n  use_approaches: true\n  use_site_ranking: true\n")
    try:
        win.reuse_price_cb.setChecked(False)
        flags = cl.get_run_flags()
        assert flags["reuse_price"] is False
        assert flags["use_approaches"] is True
        assert flags["use_site_ranking"] is True
    finally:
        win.close()


def test_ductwork_checkbox_default_off(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        assert win.ductwork_cb.isChecked() is False
    finally:
        win.close()


def test_ductwork_checkbox_initialized_from_config(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path,
                         "ductwork:\n  enabled: true\nrun: {}\n")
    try:
        assert win.ductwork_cb.isChecked() is True
    finally:
        win.close()


def test_ductwork_toggle_writes_config(qapp, monkeypatch, tmp_path):
    import src.config_loader as cl
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        assert cl.get_ductwork_enabled() is False
        win.ductwork_cb.setChecked(True)
        assert cl.get_ductwork_enabled() is True
    finally:
        win.close()
