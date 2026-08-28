import pytest
from PySide6.QtWidgets import QApplication

from gui.agent_monitor import AgentMonitorPanel


@pytest.fixture(scope="module")
def qapp():
    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("QApplication не может быть создан (нет display)")
    yield app


class TestAgentMonitorPanel:
    def test_reset(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "action", "text": "x"})
        panel.reset()
        assert panel.history_list.count() == 0
        assert panel.progress_bar.value() == 0

    def test_start_event(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "start", "total": 5})
        assert "5" in panel.row_label.text()
        assert panel.progress_bar.maximum() == 5

    def test_action_event_adds_history(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "action", "text": "Открываю site.ru"})
        assert panel.history_list.count() == 1
        assert "site.ru" in panel.action_label.text()

    def test_row_done_updates_progress(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "start", "total": 3})
        panel.handle_event({"type": "row_done", "idx": 2, "total": 3})
        assert panel.progress_bar.value() == 2

    def test_done_event(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "done", "total": 3, "found": 2, "errors": 0})
        assert "2" in panel.action_label.text()

    def test_stop_event(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "stop"})
        assert "Остановлен" in panel.action_label.text()

    def test_history_capped(self, qapp):
        panel = AgentMonitorPanel()
        for i in range(600):
            panel.handle_event({"type": "action", "text": f"step {i}"})
        assert panel.history_list.count() <= panel.MAX_HISTORY

    def test_clear_history(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "action", "text": "x"})
        panel.clear_history()
        assert panel.history_list.count() == 0

    def test_all_event_types_no_crash(self, qapp):
        panel = AgentMonitorPanel()
        for event in [
            {"type": "start", "total": 4},
            {"type": "row", "idx": 1, "total": 4, "preview": "ВВГ 3x1.5"},
            {"type": "action", "text": "🌐 Открываю https://site.ru", "idx": 1, "total": 4},
            {"type": "row_done", "idx": 1, "total": 4},
            {"type": "done", "total": 4, "found": 3, "errors": 1},
            {"type": "stop"},
        ]:
            panel.handle_event(event)

    def test_position_label_shows_current_position(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "row", "idx": 3, "total": 10,
                            "preview": "ВВГ 3x1.5", "position": "Кабель ВВГ 3x1.5 ГОСТ 16442-80"})
        assert "Позиция 3/10" in panel.position_label.text()
        assert "Кабель ВВГ 3x1.5" in panel.position_label.text()

    def test_position_label_falls_back_to_preview(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "row", "idx": 1, "total": 5, "preview": "ВВГ 3x1.5"})
        assert "ВВГ 3x1.5" in panel.position_label.text()

    def test_position_label_persists_across_actions(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "row", "idx": 2, "total": 5, "position": "Кран шаровой Ду15"})
        panel.handle_event({"type": "action", "text": "🌐 Открываю https://site.ru", "idx": 2, "total": 5})
        assert "Кран шаровой Ду15" in panel.position_label.text()

    def test_position_label_reset(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "row", "idx": 1, "total": 5, "position": "Товар X"})
        panel.reset()
        assert "Позиция: —" in panel.position_label.text()

    def test_position_label_cleared_on_done(self, qapp):
        panel = AgentMonitorPanel()
        panel.handle_event({"type": "row", "idx": 1, "total": 2, "position": "Товар X"})
        panel.handle_event({"type": "done", "total": 2, "found": 1, "errors": 0})
        assert "Позиция: —" in panel.position_label.text()
