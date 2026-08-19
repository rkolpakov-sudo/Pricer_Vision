import pytest
from PySide6.QtWidgets import QApplication, QComboBox

from gui.graph_assistant import _combo_value


@pytest.fixture(scope="module")
def qapp():
    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("QApplication не может быть создан (нет display)")
    yield app


def make_combo(qapp, items):
    cb = QComboBox()
    cb.setEditable(True)
    for name, sid in items:
        cb.addItem(name, sid)
    cb.setCurrentIndex(0)
    return cb


class TestComboValue:
    def test_default_selected_returns_data(self, qapp):
        cb = make_combo(qapp, [("abbro.ru", "abbro.ru"), ("santech.ru", "santech.ru")])
        assert _combo_value(cb) == "abbro.ru"

    def test_typed_new_text_ignores_stale_data(self, qapp):
        cb = make_combo(qapp, [("abbro.ru", "abbro.ru"), ("santech.ru", "santech.ru")])
        cb.lineEdit().setText("moy-site.ru")
        assert _combo_value(cb) == "moy-site.ru"

    def test_display_name_differs_from_id(self, qapp):
        cb = make_combo(qapp, [("Сантехкомплект", "santech.ru"), ("abbro.ru", "abbro.ru")])
        cb.setCurrentIndex(0)
        assert _combo_value(cb) == "santech.ru"

    def test_empty_text(self, qapp):
        cb = make_combo(qapp, [("abbro.ru", "abbro.ru")])
        cb.lineEdit().setText("")
        assert _combo_value(cb) == ""
