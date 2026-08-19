import pytest
from PySide6.QtWidgets import QApplication, QTableWidgetItem

from src import approach_relevance as ar
from gui.rules_editor import RulesEditorDialog


@pytest.fixture(scope="module")
def qapp():
    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("QApplication не может быть создан (нет display)")
    yield app


@pytest.fixture
def dialog(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(ar, "_DEFAULT_RULES_PATH", tmp_path / "mr.yaml")
    ar.load_rules()
    return RulesEditorDialog()


class TestContextRulesSave:
    def test_valid_context_row_saved(self, dialog, tmp_path):
        r = dialog.context_table.rowCount()
        dialog.context_table.insertRow(r)
        dialog.context_table.setItem(r, 0, QTableWidgetItem("Вентиль латунный"))
        dialog.context_table.setItem(r, 1, QTableWidgetItem("итого"))
        dialog._on_save()
        loaded = ar.load_rules(tmp_path / "mr.yaml")
        ctx = loaded.get("context_insignificant", [])
        assert any(c["base"] == "Вентиль латунный" and c["drop"] == "итого" for c in ctx)
        assert "Сохранено" in dialog.status_label.text()

    def test_incomplete_row_flagged(self, dialog):
        r = dialog.context_table.rowCount()
        dialog.context_table.insertRow(r)
        dialog.context_table.setItem(r, 1, QTableWidgetItem("фраза без base"))
        assert dialog._incomplete_context_rows()
        dialog._on_save()
        assert "неполных строк" in dialog.status_label.text()

    def test_incomplete_row_does_not_break_valid_save(self, dialog, tmp_path):
        r = dialog.context_table.rowCount()
        dialog.context_table.insertRow(r)
        dialog.context_table.setItem(r, 0, QTableWidgetItem("Вентиль латунный"))
        dialog.context_table.setItem(r, 1, QTableWidgetItem("итого"))
        r = dialog.context_table.rowCount()
        dialog.context_table.insertRow(r)
        dialog.context_table.setItem(r, 1, QTableWidgetItem("без base"))
        dialog._on_save()
        loaded = ar.load_rules(tmp_path / "mr.yaml")
        ctx = loaded.get("context_insignificant", [])
        assert any(c["base"] == "Вентиль латунный" for c in ctx)
