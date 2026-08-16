import time
import pytest

from gui.graph_explorer import (
    GraphNode, GraphScene, _edge_touches, _lod_decision, _physics_step,
    LOD_THRESHOLD, MAX_GRAPH_NODES,
)
from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    try:
        app = QApplication.instance() or QApplication([])
    except Exception:
        pytest.skip("QApplication не может быть создан (нет display)")
    yield app


def make_nodes(count, start=0.0):
    return [
        GraphNode(f"n{i}", f"n{i}", "product", float(i % 100) + start, float(i // 100) + start,
                  3.0, (0, 0, 0))
        for i in range(count)
    ]


class TestEdgeTouches:
    def test_touches_own_index(self):
        assert _edge_touches(0, 0, 3, 4)
        assert _edge_touches(0, 3, 0, 4)

    def test_bounds_guard_fixes_precedence_bug(self):
        # (u == idx), но v вне границ — раньше это был IndexError
        assert not _edge_touches(0, 0, 99, 4)
        assert not _edge_touches(2, 5, 2, 4)

    def test_unrelated_edge_false(self):
        assert not _edge_touches(1, 0, 2, 4)


class TestLodDecision:
    def test_small_graph_no_lod(self):
        assert _lod_decision(100) == {"lod": False, "labels": True, "physics": True}

    def test_large_graph_lod(self):
        d = _lod_decision(1000)
        assert d == {"lod": True, "labels": False, "physics": False}

    def test_threshold_boundary(self):
        assert not _lod_decision(LOD_THRESHOLD)["lod"]
        assert _lod_decision(LOD_THRESHOLD + 1)["lod"]

    def test_max_graph_nodes_above_lod_threshold(self):
        assert MAX_GRAPH_NODES > LOD_THRESHOLD


class TestPhysicsStep:
    def test_empty(self):
        _physics_step([], [], 1.0)

    def test_single_node(self):
        node = GraphNode("a", "a", "product", 0.0, 0.0, 3.0, (0, 0, 0))
        _physics_step([node], [], 1.0)

    def test_small_network_moves(self):
        nodes = make_nodes(5)
        edges = [(i, i + 1, (0, 0, 0), False) for i in range(4)]
        x_before = [n.x for n in nodes]
        _physics_step(nodes, edges, 1.0)
        assert [n.x for n in nodes] != x_before

    def test_1000_nodes_performance(self):
        nodes = make_nodes(1000)
        edges = [(i, i + 1, (0, 0, 0), False) for i in range(999)]
        t0 = time.monotonic()
        _physics_step(nodes, edges, 0.5)
        elapsed = time.monotonic() - t0
        assert elapsed < 2.0


class TestSceneLargeGraph:
    def test_build_1000_nodes_no_labels(self, qapp):
        scene = GraphScene()
        nodes = make_nodes(1000)
        edges = [(i, i + 1, (0, 0, 0), False) for i in range(999)]
        t0 = time.monotonic()
        scene.build(nodes, edges, labels=False)
        elapsed = time.monotonic() - t0
        assert len(scene._node_items) == 1000
        assert len(scene._label_items) == 0
        assert elapsed < 5.0

    def test_labels_toggle(self, qapp):
        scene = GraphScene()
        nodes = make_nodes(10)
        edges = [(i, i + 1, (0, 0, 0), False) for i in range(9)]
        scene.build(nodes, edges, labels=True)
        assert len(scene._label_items) == 10
        scene.set_labels_visible(False)
        scene.set_labels_visible(True)
