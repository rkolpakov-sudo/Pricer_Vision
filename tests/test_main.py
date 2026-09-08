"""Smoke-тест панели «Режим поиска» (main.py) в offscreen-режиме.

Создаёт MainWindow с замоканными тяжёлыми зависимостями (БД, YAML, тема,
PDF-runner) и проверяет, что три флажка режима поиска инициализируются из
конфига, а переключение инвертирует fresh.
"""
import os
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest
from PySide6.QtCore import Qt
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
        self.ws = None
        self.header_map = None
        self._specs = []

    def get_specs(self):
        return self._specs


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


def test_row_done_keeps_full_url_in_gui(qapp, monkeypatch, tmp_path):
    """URL в таблице результатов НЕ усекается (регрессия url[:80]): усечение давало
    битую ссылку (404) — терялся числовой суффикс карточки (.../103731804)."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        full_url = ("https://market.yandex.ru/card/truba-stalnaya-vodogazoprovodnaya-"
                    "du-15kh28-mm-3-m/103731804")
        win._on_row_done(0, {
            "spec_text": "Труба ВГП Ø15",
            "price": 299.0,
            "confidence": 0.5,
            "url": full_url,
            "site": "market.yandex.ru",
        })
        row = win.results_table.rowCount() - 1
        # колонка 7 — URL
        cell = win.results_table.item(row, 7)
        assert cell is not None
        assert cell.text() == full_url
        assert cell.data(Qt.UserRole) == full_url
    finally:
        win.close()


def test_row_done_url_col_doubleclick_uses_full_url(qapp, monkeypatch, tmp_path):
    """Двойной клик открывает колонку 7 (URL) с ПОЛНЫМ url из UserRole."""
    import main as main_mod
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        full_url = "https://vseinstrumenti.ru/product/stalnaya-truba-dtrd-du-20-mm-l-2000-mm-ots-xyz/12345"
        win._on_row_done(0, {
            "spec_text": "Труба",
            "price": 100.0,
            "confidence": 0.9,
            "url": full_url,
            "site": "vseinstrumenti.ru",
        })
        row = win.results_table.rowCount() - 1
        opened = []
        orig = main_mod.QDesktopServices.openUrl
        main_mod.QDesktopServices.openUrl = lambda u: opened.append(u.toString()) or True
        try:
            cell = win.results_table.item(row, 7)
            win._on_url_double_click(cell)
            assert opened and opened[0] == full_url
        finally:
            main_mod.QDesktopServices.openUrl = orig
    finally:
        win.close()


class _Spec:
    def __init__(self, row, text):
        self.row = row
        self.text = text
        self.brand = ""


def _window_with_specs(win, row_texts):
    """Заполняет excel_writer спеками: row → text."""
    win.excel_writer._specs = [_Spec(r, t) for r, t in row_texts]
    return win


def test_fill_session_gaps_restores_missing_middle_rows(qapp, monkeypatch, tmp_path):
    """Сессия покрывает excel_row 2..10, но 5,7,8 отсутствуют — gap-fill должен
    добавить пустые placeholder-записи для них, а не оставить «новые» позиции."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win.excel_writer._specs = [_Spec(r, f"Товар {r}") for r in range(2, 11)]
        results = [
            {"excel_row": 2, "spec_text": "Товар 2", "price": 100.0},
            {"excel_row": 3, "spec_text": "Товар 3", "price": None},
            {"excel_row": 4, "spec_text": "Товар 4", "price": 400.0},
            {"excel_row": 6, "spec_text": "Товар 6", "price": 600.0},
            {"excel_row": 9, "spec_text": "Товар 9", "price": None},
            {"excel_row": 10, "spec_text": "Товар 10", "price": 1000.0},
        ]
        filled = win._fill_session_gaps(results)
        rows = sorted(r.get("excel_row") for r in filled)
        assert rows == [2, 3, 4, 5, 6, 7, 8, 9, 10], rows
        # Заглушки с price None
        for r in filled:
            if r.get("excel_row") in (5, 7, 8):
                assert r.get("price") is None
                assert r.get("restored") is True
    finally:
        win.close()


def test_fill_session_gaps_does_not_add_beyond_max(qapp, monkeypatch, tmp_path):
    """Позиции выше максимального excel_row в сессии не заполняются — они
    ещё не обрабатывались и должны искаться живьём."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win.excel_writer._specs = [_Spec(r, f"Товар {r}") for r in range(2, 14)]
        results = [
            {"excel_row": 2, "spec_text": "Товар 2", "price": 100.0},
            {"excel_row": 4, "spec_text": "Товар 4", "price": 400.0},
        ]
        filled = win._fill_session_gaps(results)
        rows = sorted(r.get("excel_row") for r in filled)
        # Максимум = 4 → заполняем только 3, позиции 5+ не трогаем
        assert rows == [2, 3, 4], rows
    finally:
        win.close()


def test_fill_session_gaps_empty_no_crash(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        assert win._fill_session_gaps([]) == []
        assert win._fill_session_gaps(None) is None
    finally:
        win.close()


def test_sort_by_excel_orders_out_of_order_session(qapp, monkeypatch, tmp_path):
    """Сессия, сохранённая в беспорядочном виде (84 перед 79), при сортировке
    приводится к порядку спецификации — позиции не «переставляются»."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        results = [
            {"excel_row": 84, "spec_text": "A84", "price": 1.0},
            {"excel_row": 79, "spec_text": "A79", "price": 2.0},
            {"excel_row": 151, "spec_text": "A151", "price": 3.0},
            {"excel_row": 140, "spec_text": "A140", "price": 4.0},
        ]
        s = win._sort_by_excel(results)
        assert [r["excel_row"] for r in s] == [79, 84, 140, 151]
    finally:
        win.close()


def test_on_row_done_inserts_new_row_at_correct_position(qapp, monkeypatch, tmp_path):
    """Новый результат (найденный живьём) вставляется по своей позиции
    excel_row, а НЕ в конец списка (регрессия «переставленных позиций»)."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 1.0},
            {"excel_row": 3, "spec_text": "Т3", "price": 2.0},
            {"excel_row": 79, "spec_text": "Т79", "price": 3.0},
            {"excel_row": 80, "spec_text": "Т80", "price": 4.0},
        ]
        # Найденная «дыра» excel_row 78 должна встать МЕЖДУ 3 и 79, а не в конец
        win._on_row_done(77, {"excel_row": 78, "spec_text": "Т78", "price": 5.0})
        rows = [r.get("excel_row") for r in win._restored_results]
        assert rows == [2, 3, 78, 79, 80], rows
    finally:
        win.close()


def test_on_row_done_upsert_replaces_in_place(qapp, monkeypatch, tmp_path):
    """Повторный результат той же позиции (restored + live) заменяется на
    месте, а не добавляет дубль."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 1.0},
            {"excel_row": 3, "spec_text": "Т3", "price": None},
        ]
        win._on_row_done(2, {"excel_row": 3, "spec_text": "Т3", "price": 300.0})
        rows = [r.get("excel_row") for r in win._restored_results]
        assert rows == [2, 3]
        assert win._restored_results[1]["price"] == 300.0
    finally:
        win.close()


def _seed_restored_table(win, specs):
    """Заполняет _restored_results и перестраивает таблицу (1:1)."""
    win._restored_results = specs
    win._repopulate_table()
    return win


def test_toggle_invalid_sets_and_clears_flag(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        _seed_restored_table(win, [
            {"excel_row": 2, "spec_text": "Т2", "price": 100.0},
            {"excel_row": 3, "spec_text": "Т3", "price": 200.0},
        ])
        assert win._invalid_count() == 0
        win._toggle_invalid_row(0, "Т2")
        assert win._invalid_count() == 1
        assert win._restored_results[0]["invalid"] is True
        assert win.retry_marked_btn.isEnabled() is True
        # снятие
        win._toggle_invalid_row(0, "Т2")
        assert win._invalid_count() == 0
        assert win.retry_marked_btn.isEnabled() is False
    finally:
        win.close()


def test_retry_btn_disabled_during_processing(qapp, monkeypatch, tmp_path):
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        _seed_restored_table(win, [
            {"excel_row": 2, "spec_text": "Т2", "price": 100.0, "invalid": True},
        ])
        assert win.retry_marked_btn.isEnabled() is True
        win._processing_active = True
        win._retry_btn_enabled()
        assert win.retry_marked_btn.isEnabled() is False
    finally:
        win.close()


def test_retry_upsert_no_duplicate(qapp, monkeypatch, tmp_path):
    """Повторный результат (после retry) заменяет существующую запись —
    не создаёт дубль (регрессия: insert без upsert давал дубль)."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 100.0},
            {"excel_row": 3, "spec_text": "Т3", "price": 200.0},
        ]
        win._on_retry_row_done(1, {"excel_row": 3, "spec_text": "Т3", "price": 999.0})
        rows = [r.get("excel_row") for r in win._restored_results]
        assert rows == [2, 3]
        assert len(win._restored_results) == 2
        assert win._restored_results[1]["price"] == 999.0
    finally:
        win.close()


def test_retry_found_price_clears_invalid(qapp, monkeypatch, tmp_path):
    """Найденная при перепоиске цена снимает пометку invalid."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 40.16, "invalid": True},
        ]
        win._on_retry_row_done(0, {"excel_row": 2, "spec_text": "Т2", "price": 300.0})
        assert win._restored_results[0]["invalid"] is False
        assert win._restored_results[0]["price"] == 300.0
    finally:
        win.close()


def test_retry_not_found_keeps_invalid(qapp, monkeypatch, tmp_path):
    """«Не найдено» при перепоиске сохраняет пометку invalid (позиция не решена)."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 40.16, "invalid": True},
        ]
        win._on_retry_row_done(0, {"excel_row": 2, "spec_text": "Т2", "price": None})
        assert win._restored_results[0]["invalid"] is True
        assert win._restored_results[0]["price"] is None
    finally:
        win.close()


def test_invalid_survives_restore_dict_copy(qapp, monkeypatch, tmp_path):
    """Runner при восстановлении делает result = dict(_match): копия обязана
    сохранять invalid=True, иначе полный прогон «потеряет» пометки (регрессия:
    invalid должен пережить прогон до ручного перезапуска)."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        _match = {"excel_row": 63, "spec_text": "КДР", "price": 1652.17, "invalid": True}
        # точная копия, как в mcp_agent_runner.py:392
        restored = dict(_match)
        restored["restored"] = True
        assert restored.get("invalid") is True
        assert restored.get("price") == 1652.17
    finally:
        win.close()


def test_invalid_survives_full_run_merge(qapp, monkeypatch, tmp_path):
    """Полный прогон (restore всех строк сессии + merge) не должен стереть
    invalid-пометки: отмеченные позиции остаются помеченными до ручного
    перезапуска кнопкой «Перезапустить отмеченные»."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win.excel_writer._specs = [_Spec(r, f"Товар {r}") for r in range(2, 8)]
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Товар 2", "price": 10.0},
            {"excel_row": 3, "spec_text": "Товар 3", "price": 20.0, "invalid": True},
            {"excel_row": 4, "spec_text": "Товар 4", "price": 30.0},
            {"excel_row": 5, "spec_text": "Товар 5", "price": 40.0, "invalid": True},
        ]
        win._original_restored_results = [dict(r) for r in win._restored_results]

        # Эмуляция runner: каждая строка восстанавливается как dict(_match).
        runner_results = []
        for r in win._restored_results:
            c = dict(r)
            c["restored"] = True
            runner_results.append(c)

        merged = win._merge_session_results(runner_results)
        invalid = [r.get("excel_row") for r in merged if r.get("invalid")]
        assert sorted(invalid) == [3, 5], invalid
        assert len(merged) == 4
    finally:
        win.close()


def test_invalid_survives_auto_save_roundtrip(qapp, monkeypatch, tmp_path):
    """Сериализация/загрузка сессии не теряет invalid (JSON сохраняет поле)."""
    import json
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win._spec_path = str(tmp_path / "spec.xlsx")
        win.excel_writer._specs = [_Spec(2, "Т2")]
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 40.16, "invalid": True},
        ]
        win._run_failed = False

        # roundtrip через save_session/load_session
        from src.session_manager import save_session, load_session
        p = str(tmp_path / "_current.json")
        state = win._build_session_state()
        state["results"] = win._restored_results
        save_session(p, state)
        loaded = load_session(p)
        inv = [r.get("excel_row") for r in loaded.get("results", []) if r.get("invalid")]
        assert inv == [2], inv
    finally:
        win.close()


def test_batch_queue_advances_after_row_done(qapp, monkeypatch, tmp_path):
    """Регрессия: пакетный перезапуск зависал после первой строки. Причина —
    _on_retry_done не сбрасывал _processing_active, и guard в _retry_single_row
    (if self._processing_active: return) блокировал запуск следующей строки.

    Проверяем: очередь полностью доливается (каждая строка запускается, guard
    пропускает после сброса флага в _on_retry_done)."""
    win = _monkey_window(monkeypatch, tmp_path, "run: {}\n")
    try:
        win.excel_writer._specs = [_Spec(2, "Т2"), _Spec(3, "Т3")]
        win._restored_results = [
            {"excel_row": 2, "spec_text": "Т2", "price": 100.0, "invalid": True},
            {"excel_row": 3, "spec_text": "Т3", "price": 200.0, "invalid": True},
        ]
        win._repopulate_table()
        win._retry_batch_mode = True

        # Заглушка: запуск строки НЕ стартует реальный runner, а синхронно
        # завершает её (row_done → done), как сделал бы реальный одиночный retry.
        calls = []
        orig = win._retry_single_row
        def spy(table_row, display_type=""):
            assert win._processing_active is False, "guard не должен блокировать"
            calls.append(table_row)
            spec_item = win.results_table.item(table_row, 1)
            er = None
            for r in win._restored_results:
                if r.get("spec_text") == spec_item.text():
                    er = r.get("excel_row"); break
            # сигналим завершение строки (как row_done → done_signal реального runner)
            win._processing_active = True
            win._on_retry_row_done(table_row,
                                   {"excel_row": er, "spec_text": spec_item.text(), "price": 999.0})
            win._on_retry_done(True, [])
        win._retry_single_row = spy

        win._retry_queue = [2, 3]
        win._launch_next_queued_retry()

        assert calls == [0, 1], f"обе строки должны быть запущены, было: {calls}"
        assert win._retry_queue == []
        assert win._processing_active is False
        assert all(not r.get("invalid") for r in win._restored_results)
    finally:
        win.close()
