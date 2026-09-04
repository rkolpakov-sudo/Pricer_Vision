import math
import time
import threading
import html as _html
import networkx as nx
import numpy as np
from src import icons as ui_icons
from PySide6.QtCore import Qt, QObject, Signal, QTimer, QEvent
from PySide6.QtGui import (
    QColor, QPen, QBrush, QFont, QPainter, QRadialGradient, QPalette,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QGridLayout, QLabel, QPushButton,
    QGraphicsView, QGraphicsScene, QGraphicsEllipseItem,
    QGraphicsLineItem, QGraphicsTextItem, QGraphicsItem,
    QCheckBox, QToolTip, QFrame, QSizePolicy,
)
from PySide6.QtOpenGLWidgets import QOpenGLWidget


STYLES = {
    "root":        (255, 215, 0),     # Gold
    "product":     (168, 85, 247),    # Purple-500
    "site":        (249, 115, 22),    # Orange-500
    "approach":    (59, 130, 246),    # Blue-500
    "price":       (34, 211, 238),    # Cyan-500
}
SIZES = {"root": 4, "product": 3.5, "site": 3, "approach": 2.5, "price": 3}
ECOLORS = {
    "HAS_SITE":  (168, 85, 247),     # Purple (matches product)
    "HAS_PRICE": (34, 211, 238),     # Cyan (matches price)
    "APPROACH":  (100, 110, 130),    # Gray-blue
    "SIMILAR":   (249, 115, 22),     # Orange (matches site)
}

LOD_THRESHOLD = 500  # выше этого числа нод — отключаем подписи и физику
MAX_GRAPH_NODES = 1000  # максимальное число нод в графе (свыше включается LOD)
PHYSICS_SYNC_INTERVAL = 0.033  # ~30 fps для синхронизации позиций на UI


def _edge_touches(idx: int, u: int, v: int, n: int) -> bool:
    """True, если ребро (u,v) инцидентно узлу idx и оба конца в границах."""
    return (u == idx or v == idx) and u < n and v < n


def _lod_decision(node_count: int, threshold: int = LOD_THRESHOLD) -> dict:
    """Решение по уровню детализации (LOD).

    При превышении порога подписи и непрерывная физика отключаются —
    рендерится только статичный граф, чтобы UI не тормозил.
    """
    lod = node_count > threshold
    return {"lod": lod, "labels": not lod, "physics": not lod}


class GraphNode:
    __slots__ = ("id", "label", "type", "x", "y", "vx", "vy",
                 "size", "color", "fixed", "item", "data")

    def __init__(self, nid, label, ntype, x, y, size, color):
        self.id = nid
        self.label = label
        self.type = ntype
        self.x = x
        self.y = y
        self.vx = 0.0
        self.vy = 0.0
        self.size = size
        self.color = color
        self.fixed = False
        self.item = None
        self.data = None


def _physics_step(nodes, edges, alpha: float):
    """Один шаг физической симуляции. Мутирует позиции узлов на месте.

    Вынесен из PhysicsWorker._run в чистую функцию — тестируется без Qt.
    """
    n = len(nodes)
    if n == 0:
        return

    positions = np.zeros((n, 2), dtype=np.float64)
    forces = np.zeros((n, 2), dtype=np.float64)
    fixed = np.zeros(n, dtype=bool)
    sizes = np.zeros(n, dtype=np.float64)
    for i, nd in enumerate(nodes):
        positions[i, 0] = nd.x
        positions[i, 1] = nd.y
        fixed[i] = nd.fixed
        sizes[i] = nd.size

    fd = alpha * 200.0
    for i in range(n):
        if fixed[i]:
            continue
        dx = positions[i, 0] - positions[:, 0]
        dy = positions[i, 1] - positions[:, 1]
        d = np.sqrt(dx * dx + dy * dy)
        d = np.maximum(d, 1.0)
        f = fd / (d * d)
        forces[i, 0] = np.sum(dx / d * f)
        forces[i, 1] = np.sum(dy / d * f)

    for u, v, *_ in edges:
        if u >= n or v >= n:
            continue
        dx = positions[v, 0] - positions[u, 0]
        dy = positions[v, 1] - positions[u, 1]
        d = max(math.sqrt(dx * dx + dy * dy), 1)
        f = alpha * (d - 180) * 0.03
        fx = dx / d * f
        fy = dy / d * f
        if not fixed[u]:
            forces[u, 0] += fx
            forces[u, 1] += fy
        if not fixed[v]:
            forces[v, 0] -= fx
            forces[v, 1] -= fy

    center_strength = 0.002
    for i in range(n):
        if not fixed[i]:
            forces[i, 0] -= positions[i, 0] * alpha * center_strength
            forces[i, 1] -= positions[i, 1] * alpha * center_strength

    for i in range(n):
        if not fixed[i]:
            cur_vx = nodes[i].vx + forces[i, 0]
            cur_vy = nodes[i].vy + forces[i, 1]
            cur_vx *= 0.6
            cur_vy *= 0.6
            nodes[i].vx = cur_vx
            nodes[i].vy = cur_vy
            nodes[i].x += cur_vx
            nodes[i].y += cur_vy

    for i in range(n):
        if fixed[i]:
            continue
        for j in range(i + 1, n):
            if fixed[j]:
                continue
            dx = nodes[j].x - nodes[i].x
            dy = nodes[j].y - nodes[i].y
            d = math.sqrt(dx * dx + dy * dy)
            min_d = max(30, (sizes[i] + sizes[j]) * 1.5)
            if d < min_d and d > 0.1:
                overlap = (min_d - d) / 2
                fx = dx / d * overlap
                fy = dy / d * overlap
                nodes[i].x -= fx
                nodes[j].x += fx
                nodes[i].y -= fy
                nodes[j].y += fy


class PhysicsWorker(QObject):
    updated = Signal(object)
    stabilized = Signal()

    def __init__(self):
        super().__init__()
        self._running = False
        self._thread = None
        self._gen = 0
        self._lock = threading.Lock()
        self._iteration = 0

    def start(self, nodes, edges):
        with self._lock:
            self._gen += 1
            gen = self._gen
            self._nodes = nodes
            self._edges = edges
            self._alpha = 1.0
            self._iteration = 0
            self._running = True
            self._thread = threading.Thread(target=self._run, args=(gen,), daemon=True)
            self._thread.start()

    def stop(self):
        self._running = False

    def reheat(self, alpha=0.3):
        with self._lock:
            if self._running:
                self._alpha = alpha
                return
            self._gen += 1
            gen = self._gen
            self._alpha = alpha
            self._iteration = 0
            self._running = True
            self._thread = threading.Thread(target=self._run, args=(gen,), daemon=True)
            self._thread.start()

    def _run(self, gen):
        while self._running:
            with self._lock:
                alpha = self._alpha
                if gen != self._gen:
                    break
            if alpha < 0.001:
                time.sleep(0.05)
                continue
            alpha *= 0.98
            with self._lock:
                self._alpha = alpha
                self._iteration += 1
                iteration = self._iteration
                if gen != self._gen:
                    break
                if iteration > 150:
                    self._running = False
                    self._alpha = 0.0
            if iteration > 150:
                self.stabilized.emit()
                break
            nodes = self._nodes
            edges = self._edges
            _physics_step(nodes, edges, alpha)

            if gen == self._gen:
                self.updated.emit(nodes)
            time.sleep(0.016)


class NodeItem(QGraphicsEllipseItem):
    def __init__(self, idx, node, scene):
        sz = node.size
        super().__init__(-sz, -sz, sz * 2, sz * 2)
        self._idx = idx
        self._node = node
        self._scene_ref = scene
        self._highlighted = False
        r, g, b = node.color
        grad = QRadialGradient(-sz * 0.3, -sz * 0.3, sz * 1.4)
        grad.setColorAt(0.0, QColor(min(255, r+80), min(255, g+80), min(255, b+80)))
        grad.setColorAt(0.5, QColor(r, g, b))
        grad.setColorAt(1.0, QColor(max(0, r-60), max(0, g-60), max(0, b-60)))
        self.setBrush(QBrush(grad))
        self.setPen(QPen(QColor(40, 40, 50), 0.5))
        self.setAcceptHoverEvents(True)
        self.setCursor(Qt.PointingHandCursor)
        self.setFlag(QGraphicsItem.ItemIsMovable, True)
        self.setFlag(QGraphicsItem.ItemSendsGeometryChanges, True)
        self.setZValue(10)
        self.setToolTip(f"{node.id}\n{node.label}")

    def itemChange(self, change, value):
        if change == QGraphicsItem.ItemPositionHasChanged and self._node:
            self._node.x = value.x()
            self._node.y = value.y()
            if self._scene_ref:
                self._scene_ref._update_edges(self._idx)
        return super().itemChange(change, value)

    def hoverEnterEvent(self, e):
        self.setPen(QPen(QColor(200, 200, 220), 1.5))
        super().hoverEnterEvent(e)

    def hoverLeaveEvent(self, e):
        if not self._highlighted:
            self.setPen(QPen(QColor(40, 40, 50), 0.6))
        super().hoverLeaveEvent(e)

    def mousePressEvent(self, e):
        if e.button() == Qt.LeftButton and self._scene_ref:
            self._scene_ref._toggle_highlight(self._idx)
            if self._node:
                self._node.fixed = True
            if self._scene_ref._physics_ref:
                self._scene_ref._physics_ref.reheat()
        super().mousePressEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.LeftButton and self._node:
            self._node.fixed = False
        super().mouseReleaseEvent(e)


class GraphScene(QGraphicsScene):
    node_selected = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setBackgroundBrush(QColor("#c0c4cc"))
        self._label_items = {}
        self._edge_data = []
        self._highlighted = set()
        self._node_items = []
        self._nodes = []
        self._physics_ref = None
        self._glow_items = {}
        self._edge_glow_items = {}

    def build(self, nodes, edges, labels=True):
        self.clear()
        self._label_items.clear()
        self._edge_data.clear()
        self._highlighted.clear()
        self._node_items.clear()
        self._glow_items.clear()
        self._edge_glow_items.clear()
        self._nodes = nodes

        label_font = QFont("Segoe UI")
        label_font.setPixelSize(5)
        for u, v, color, dashed in edges:
            r, g, b = color
            pen = QPen(QColor(r, g, b, 80), 0.5,
                Qt.DashLine if dashed else Qt.SolidLine)
            li = self.addLine(0, 0, 0, 0, pen)
            li.setZValue(1)
            self._edge_data.append((li, u, v, (r, g, b), dashed))

        for i, node in enumerate(nodes):
            item = NodeItem(i, node, self)
            item.setPos(node.x, node.y)
            self.addItem(item)
            node.item = item
            self._node_items.append(item)

            if not labels:
                continue

            txt = QGraphicsTextItem()
            display = node.label
            txt.setPlainText(display)
            txt.setDefaultTextColor(QColor(224, 224, 224))
            txt.setFont(label_font)
            txt.setZValue(15)
            txt.setVisible(True)
            self.addItem(txt)
            self._label_items[i] = txt

    def _update_edges(self, idx):
        n = len(self._nodes)
        for li, u, v, *_ in self._edge_data:
            if _edge_touches(idx, u, v, n):
                li.setLine(self._nodes[u].x, self._nodes[u].y,
                           self._nodes[v].x, self._nodes[v].y)

    def _toggle_highlight(self, idx):
        if idx in self._highlighted:
            self._clear_highlight()
            return
        self._clear_highlight()
        self._highlighted.add(idx)
        self._node_items[idx]._highlighted = True
        self._node_items[idx].setPen(QPen(QColor(220, 220, 240), 2.0))
        if idx in self._label_items:
            self._label_items[idx].setVisible(True)
        self._create_glow(idx, 3.5, (220, 220, 240), 180)
        for li, u, v, *_ in self._edge_data:
            if u == idx:
                self._highlighted.add(v)
                if v in self._label_items:
                    self._label_items[v].setVisible(True)
                if v < len(self._node_items):
                    self._node_items[v].setPen(QPen(QColor(200, 200, 220), 1.5))
                self._create_glow(v, 2.5, (200, 200, 220), 100)
            if v == idx:
                self._highlighted.add(u)
                if u in self._label_items:
                    self._label_items[u].setVisible(True)
                if u < len(self._node_items):
                    self._node_items[u].setPen(QPen(QColor(200, 200, 220), 1.5))
                self._create_glow(u, 2.5, (200, 200, 220), 100)
        self._highlight_edges()
        if idx < len(self._nodes):
            self.node_selected.emit(self._nodes[idx].id)

    def _create_glow(self, idx, size_mult, color, alpha):
        if idx >= len(self._node_items):
            return
        parent = self._node_items[idx]
        sz = parent._node.size * size_mult
        r, g, b = color
        glow = QGraphicsEllipseItem(-sz, -sz, sz * 2, sz * 2, parent)
        grad = QRadialGradient(0, 0, sz)
        grad.setColorAt(0.0, QColor(r, g, b, alpha))
        grad.setColorAt(0.3, QColor(r, g, b, alpha // 2))
        grad.setColorAt(0.7, QColor(r, g, b, alpha // 4))
        grad.setColorAt(1.0, QColor(r, g, b, 0))
        glow.setBrush(QBrush(grad))
        glow.setPen(QPen(Qt.NoPen))
        glow.setZValue(5)
        self._glow_items.setdefault(idx, []).append(glow)

    def _highlight_edges(self):
        for idx, (li, u, v, (r, g, b), dashed) in enumerate(self._edge_data):
            if u in self._highlighted and v in self._highlighted:
                x1, y1 = self._nodes[u].x, self._nodes[u].y
                x2, y2 = self._nodes[v].x, self._nodes[v].y
                glows = []
                for w, a in [(6, 25), (3, 70)]:
                    gli = self.addLine(x1, y1, x2, y2,
                        QPen(QColor(180, 140, 255, a), w, Qt.SolidLine))
                    gli.setZValue(0.5)
                    glows.append(gli)
                self._edge_glow_items[idx] = glows
                li.setPen(QPen(QColor(200, 180, 255), 1.2, Qt.SolidLine))

    def _clear_highlight(self):
        for item in self._node_items:
            item._highlighted = False
            item.setPen(QPen(QColor(40, 40, 50), 0.6))
        for li, u, v, (r, g, b), dashed in self._edge_data:
            li.setPen(QPen(QColor(r, g, b, 80), 0.5,
                Qt.DashLine if dashed else Qt.SolidLine))
        for idx, glows in self._glow_items.items():
            for g in glows:
                self.removeItem(g)
        self._glow_items.clear()
        for idx, glows in self._edge_glow_items.items():
            for g in glows:
                self.removeItem(g)
        self._edge_glow_items.clear()
        self._highlighted.clear()
        self.node_selected.emit(None)

    def reposition_labels(self):
        for i, node in enumerate(self._nodes):
            if i in self._label_items:
                txt = self._label_items[i]
                bw = txt.boundingRect().width()
                bh = txt.boundingRect().height()
                txt.setPos(node.x - bw / 2, node.y + node.size + 2)

    def sync_all(self, update_labels: bool = True):
        for i, node in enumerate(self._nodes):
            if node.item and node.item.scene() is self:
                node.item.setPos(node.x, node.y)
        for li, u, v, *_ in self._edge_data:
            if u < len(self._nodes) and v < len(self._nodes):
                li.setLine(self._nodes[u].x, self._nodes[u].y,
                           self._nodes[v].x, self._nodes[v].y)
        for idx, glows in self._edge_glow_items.items():
            if idx < len(self._edge_data):
                _, u, v, _, _ = self._edge_data[idx]
                if u < len(self._nodes) and v < len(self._nodes):
                    x1, y1 = self._nodes[u].x, self._nodes[u].y
                    x2, y2 = self._nodes[v].x, self._nodes[v].y
                    for gli in glows:
                        gli.setLine(x1, y1, x2, y2)
        if update_labels:
            self.reposition_labels()

    def update_theme(self, is_dark):
        color = QColor(224, 224, 224) if is_dark else QColor(30, 30, 35)
        for txt in self._label_items.values():
            txt.setDefaultTextColor(color)

    def set_labels_visible(self, visible: bool):
        """Включает/выключает подписи нод (LOD: при 1000+ нодах — off)."""
        for txt in self._label_items.values():
            txt.setVisible(visible)


class GraphCanvas(QGraphicsView):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._scene = GraphScene(self)
        self.setScene(self._scene)
        self.setRenderHint(QPainter.Antialiasing)
        self.setRenderHint(QPainter.SmoothPixmapTransform)
        self.setViewport(QOpenGLWidget())
        self.viewport().setMouseTracking(True)
        self.setDragMode(QGraphicsView.NoDrag)
        self.setTransformationAnchor(QGraphicsView.AnchorUnderMouse)
        self.setResizeAnchor(QGraphicsView.AnchorUnderMouse)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.setBackgroundBrush(QColor(26, 27, 46))
        self.setViewportUpdateMode(QGraphicsView.SmartViewportUpdate)
        self._panning = False
        self._pan_start = None
        self._update_theme()

    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange:
            self._update_theme()
        super().changeEvent(event)

    def _update_theme(self):
        bg = self.palette().color(QPalette.Window)
        is_dark = bg.lightness() < 128
        self.setBackgroundBrush(QColor(26, 27, 46) if is_dark else QColor(240, 240, 245))
        self._scene.update_theme(is_dark)

    def wheelEvent(self, e):
        factor = 1.15 if e.angleDelta().y() > 0 else 0.87
        self.scale(factor, factor)

    def mousePressEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = True
            self._pan_start = e.position()
            self.setCursor(Qt.ClosedHandCursor)
            e.accept()
            return
        if e.button() == Qt.LeftButton:
            item = self.itemAt(e.position().toPoint())
            if item is None or isinstance(item, QGraphicsLineItem):
                self._scene._clear_highlight()
        super().mousePressEvent(e)

    def mouseMoveEvent(self, e):
        if self._panning and self._pan_start is not None:
            delta = e.position() - self._pan_start
            self._pan_start = e.position()
            self.horizontalScrollBar().setValue(
                int(self.horizontalScrollBar().value() - delta.x()))
            self.verticalScrollBar().setValue(
                int(self.verticalScrollBar().value() - delta.y()))
            e.accept()
            return
        pos = self.mapToScene(e.position().toPoint())
        item = self._scene.itemAt(pos, self.transform())
        if item and isinstance(item, NodeItem) and item._node:
            global_pos = self.viewport().mapToGlobal(e.position().toPoint())
            QToolTip.showText(global_pos, f"{item._node.id}\n{item._node.label}", self)
        else:
            QToolTip.hideText()
        super().mouseMoveEvent(e)

    def mouseReleaseEvent(self, e):
        if e.button() == Qt.MiddleButton:
            self._panning = False
            self.setCursor(Qt.ArrowCursor)
            e.accept()
            return
        super().mouseReleaseEvent(e)

    def set_data(self, nodes, edges, labels=True):
        self._scene.build(nodes, edges, labels=labels)
        self._scene.reposition_labels()
        self._update_theme()


class NodeInfoOverlay(QFrame):
    TYPE_COLORS = {"root": "#FFD700", "product": "#a855f7", "site": "#f97316",
                   "approach": "#3b82f6", "price": "#22d3ee"}

    def __init__(self, parent=None, is_dark=True):
        super().__init__(parent)
        self.setAttribute(Qt.WA_TransparentForMouseEvents, True)
        self.setFixedWidth(300)
        self._is_dark = is_dark
        self._cur_tc = "#888"

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 10, 14, 10)
        layout.setSpacing(3)

        self._title = QLabel()
        self._title.setWordWrap(True)
        self._title.setObjectName("ot")

        self._type_label = QLabel()
        self._type_label.setObjectName("oty")

        self._sep = QFrame()
        self._sep.setObjectName("os")
        self._sep.setFrameShape(QFrame.HLine)
        self._sep.setMaximumHeight(1)

        self._body = QLabel()
        self._body.setWordWrap(True)
        self._body.setObjectName("ob")

        layout.addWidget(self._title)
        layout.addWidget(self._type_label)
        layout.addWidget(self._sep)
        layout.addWidget(self._body)

        self.setMaximumHeight(400)
        self._apply_style()
        self.hide()

    def _apply_style(self):
        d = self._is_dark
        bg = "rgba(20,22,36,235)" if d else "rgba(230,232,238,240)"
        bd = "#45475a" if d else "#a5a9b5"
        dc = "#ddd" if d else "#444"
        self.setStyleSheet(
            "NodeInfoOverlay{background-color:%s;border:1px solid %s;border-radius:8px}"
            "QLabel#ot{font-size:14px;font-weight:700;color:%s;background:transparent;padding:0;border:none}"
            "QLabel#oty{font-size:11px;color:#999;background:transparent;padding:0;border:none}"
            "QLabel#ob{font-size:13px;color:%s;background:transparent;padding:0;border:none}"
            "QFrame#os{background-color:%s;border:none;max-height:1px;color:%s}"
            % (bg, bd, self._cur_tc, dc, bd, bd))

    def show_info(self, node_id, node_type, node_label, html_body):
        self._cur_tc = self.TYPE_COLORS.get(node_type, "#888")
        self._apply_style()
        self._title.setText(node_label)
        self._type_label.setText("%s  ·  %s" % (node_type, node_id))
        has_body = bool(html_body.strip()) if html_body else False
        self._sep.setVisible(has_body)
        self._body.setVisible(has_body)
        self._body.setText(html_body)
        self.setMaximumHeight(400)
        self.adjustSize()
        self.show()

    def update_theme(self, is_dark):
        self._is_dark = is_dark
        if self.isVisible():
            self._apply_style()


class GraphExplorerWidget(QWidget):
    def __init__(self):
        super().__init__()
        self._engine = None
        self._nodes = []
        self._edges = []
        self._canvas = GraphCanvas()
        self._physics = PhysicsWorker()
        self._physics.updated.connect(self._on_physics_update)
        self._canvas._scene._physics_ref = self._physics
        self._render_timer = QTimer(self)
        self._render_timer.setSingleShot(True)
        self._render_timer.setInterval(150)
        self._render_timer.timeout.connect(self._render)

        self._physics.stabilized.connect(self._on_stabilized)
        self._canvas._scene.node_selected.connect(self._show_node_info)

        self._node_filters = {
            "product": True,
            "site": True,
            "price": False,
        }
        self._edge_filters = {
            "APPROACH": True,
        }

        self._cached_pos = {}
        self._lod_active = False
        self._last_sync_time = 0.0

        layout = QVBoxLayout(self)
        layout.setSpacing(6)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Фильтры:"))
        self._filter_cbs = {}
        for key, label, color in [
            ("product", "Товары", STYLES["product"]),
            ("site", "Сайты", STYLES["site"]),
            ("price", "Цены", STYLES["price"]),
            ("APPROACH", "APPROACH", ECOLORS["APPROACH"]),
        ]:
            cb = QCheckBox(label)
            cb.setChecked(self._node_filters.get(key, self._edge_filters.get(key, True)))
            cb.setStyleSheet(
                f"QCheckBox::indicator:checked {{ background-color: rgb({color[0]},{color[1]},{color[2]}); }}"
                f"QCheckBox::indicator {{ width: 10px; height: 10px; border-radius: 3px; }}"
            )
            cb.toggled.connect(lambda checked, k=key: self._on_filter_toggle(k, checked))
            filter_row.addWidget(cb)
            self._filter_cbs[key] = cb
        filter_row.addStretch(1)
        layout.addLayout(filter_row)

        h = QHBoxLayout()
        h.addWidget(QLabel("Граф знаний"))
        self._info = QLabel("  Наведи — подпись | Клин — выделить связи | Тащи — переместить")
        self._info.setObjectName("subtitle")
        h.addWidget(self._info, 1)
        bf = QPushButton("По размеру")
        bf.setObjectName("ghost")
        bf.clicked.connect(self._fit)
        h.addWidget(bf)
        be = QPushButton("Экспорт")
        be.setObjectName("ghost")
        be.clicked.connect(self._export_json)
        h.addWidget(be)
        br = QPushButton("Обновить")
        br.setObjectName("ghost")
        br.clicked.connect(lambda: self.load_graph())
        h.addWidget(br)
        layout.addLayout(h)

        canvas_container = QWidget()
        canvas_container.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Expanding)
        cl = QGridLayout(canvas_container)
        cl.setContentsMargins(0, 0, 0, 0)
        cl.setSpacing(0)
        cl.addWidget(self._canvas, 0, 0)
        bg = self.palette().color(QPalette.Window)
        is_dark = bg.lightness() < 128
        self._info_panel = NodeInfoOverlay(canvas_container, is_dark=is_dark)
        cl.addWidget(self._info_panel, 0, 0, Qt.AlignTop | Qt.AlignLeft)
        layout.addWidget(canvas_container, 1)

    def changeEvent(self, event):
        if event.type() == QEvent.PaletteChange:
            bg = self.palette().color(QPalette.Window)
            self._info_panel.update_theme(bg.lightness() < 128)
        super().changeEvent(event)

    def _on_physics_update(self):
        now = time.monotonic()
        if now - self._last_sync_time < PHYSICS_SYNC_INTERVAL:
            return
        self._last_sync_time = now
        # Во время физики подписи не пересчитываем — они позиционируются при стабилизации
        self._canvas._scene.sync_all(update_labels=False)
        if self._nodes:
            self._cached_pos = {node.id: (node.x, node.y) for node in self._nodes}

    def _fit(self):
        rect = self._canvas._scene.itemsBoundingRect()
        if rect.isValid() and not rect.isEmpty():
            self._canvas.fitInView(rect.adjusted(-60, -60, 60, 60),
                                   Qt.KeepAspectRatio)

    def _export_json(self):
        from PySide6.QtWidgets import QFileDialog
        path, _ = QFileDialog.getSaveFileName(self, "Сохранить граф", "graph.json", "JSON (*.json)")
        if path:
            data = {"nodes": [], "edges": []}
            for node in self._nodes:
                n = {"id": node.id, "label": node.label, "type": node.type,
                     "x": round(node.x, 1), "y": round(node.y, 1), "size": node.size,
                     "color": f"rgb({node.color[0]},{node.color[1]},{node.color[2]})"}
                data["nodes"].append(n)
            for u, v, color, dashed in self._edges:
                data["edges"].append({"from_idx": u, "to_idx": v,
                    "from": self._nodes[u].id if u < len(self._nodes) else "?",
                    "to": self._nodes[v].id if v < len(self._nodes) else "?",
                    "color": f"rgb({color[0]},{color[1]},{color[2]})",
                    "dashed": dashed})
            import json
            with open(path, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            self._info.setText(f"  Экспорт: {path} ({len(data['nodes'])} узлов)")

    def _on_filter_toggle(self, key, checked):
        if key in self._node_filters:
            self._node_filters[key] = checked
        if key in self._edge_filters:
            self._edge_filters[key] = checked
        self._render_timer.start()

    def load_graph(self, engine=None):
        self._engine = engine or self._engine
        if not self._engine:
            return
        self._cached_pos.clear()
        self._render()

    def _render(self):
        self._info_panel.hide()
        try:
            engine = self._engine
            G = nx.Graph()

            all_products = engine.get_all_products()
            all_sites = engine.get_all_sites()

            G.add_node("root", type="root", name="Pricer Vision")

            show_product = self._node_filters.get("product", True)
            show_site = self._node_filters.get("site", True)
            show_price = self._node_filters.get("price", True)

            if show_product:
                for pid, pdata in all_products.items():
                    G.add_node(pid, type="product", name=pdata.get("name", pid))

            if show_site:
                for sid, sdata in all_sites.items():
                    G.add_node(sid, type="site", name=sdata.get("name", sid))

            if show_product:
                for pid in all_products:
                    G.add_edge("root", pid, relation="HAS_SITE")

            if show_product and show_site:
                for pid in all_products:
                    sites = engine.get_sites_for_product(pid)
                    for s in sites:
                        sid = s.get("id", s.get("site_id", ""))
                        if sid in all_sites:
                            G.add_edge(pid, sid, relation="HAS_SITE")

            if show_product and show_site and self._edge_filters.get("APPROACH", True):
                approaches = engine.get_all_approaches_for_assistant()
                for a in approaches:
                    pid = a.get("product_type_id", "")
                    sid = a.get("site_id", "")
                    # APPROACH-ребро показываем ТОЛЬКО если сайт реально зарегистрирован
                    # для этого товара (HAS_SITE). Иначе устаревшие подходы из БД рисуют
                    # сиротские сайты, не связанные с товаром.
                    if pid in all_products and sid in all_sites and G.has_edge(pid, sid):
                        G.add_edge(pid, sid, relation="APPROACH")

            if show_price:
                prices = engine.get_all_confirmed_prices()
                for p in prices[:100]:
                    pid = p.get("product_type_id", "")
                    if pid in all_products and pid in G:
                        price_label = f"price_{p.get('id', 0)}"
                        G.add_node(price_label, type="price",
                                   name=f"₽{p.get('price', 0):.0f}")
                        G.add_edge(pid, price_label, relation="HAS_PRICE")

            if len(G) < 2:
                self._info.setText("  Граф пуст — загрузите данные через YAML или обработайте спецификацию")
                return

            MAX = MAX_GRAPH_NODES
            gnodes = []
            seen = set()

            for nd, d in G.nodes(data=True):
                t = d.get("type", "")
                if t == "root":
                    seen.add(nd)
                    gnodes.append((nd, d.get("name", nd), STYLES[t], SIZES[t], t))
                elif t == "product":
                    seen.add(nd)
                    gnodes.append((nd, d.get("name", nd), STYLES[t], SIZES[t], t))
                elif t == "site":
                    if len(gnodes) >= MAX:
                        break
                    seen.add(nd)
                    gnodes.append((nd, d.get("name", nd), STYLES[t], SIZES[t], t))

            for nd, d in G.nodes(data=True):
                if d.get("type") == "price" and nd not in seen and len(gnodes) < MAX:
                    seen.add(nd)
                    gnodes.append((nd, d.get("name", nd), STYLES["price"], SIZES["price"], "price"))

            gedges = []
            for u, v, d in G.edges(data=True):
                if u in seen and v in seen:
                    r = d.get("relation", "")
                    ec = ECOLORS.get(r, (80, 80, 100))
                    gedges.append((u, v, ec, False))

            conn = set()
            for u, v, *_ in gedges:
                conn.add(u)
                conn.add(v)
            gnodes = [(i, l, c, s, ty) for i, l, c, s, ty in gnodes if i in conn]
            if len(gnodes) < 2:
                self._info.setText("  Недостаточно связанных узлов для отображения")
                return

            sub = nx.Graph()
            for nid, _, _, _, _ in gnodes:
                sub.add_node(nid)
            for u, v, _, _ in gedges:
                sub.add_edge(u, v)
            comps = list(nx.connected_components(sub))
            largest = max(comps, key=len) if comps else set()
            gnodes = [(i, l, c, s, ty) for i, l, c, s, ty in gnodes if i in largest]
            gedges = [(u, v, c, dsh) for u, v, c, dsh in gedges if u in largest and v in largest]

            sub2 = nx.Graph()
            for nid, _, _, _, _ in gnodes:
                sub2.add_node(nid)
            for u, v, _, _ in gedges:
                sub2.add_edge(u, v)

            product_ids = {nid for nid, _, _, _, ty in gnodes if ty == "product"}
            site_ids = {nid for nid, _, _, _, ty in gnodes if ty == "site"}
            price_ids = {nid for nid, _, _, _, ty in gnodes if ty == "price"}
            root_ids = {nid for nid, _, _, _, ty in gnodes if ty == "root"}

            parent_of = {}
            for u, v, d in G.edges(data=True):
                if u in seen and v in seen:
                    if d.get("relation") in ("HAS_SITE", "HAS_PRICE"):
                        parent_of[v] = u

            children_of = {}
            for nid in site_ids | price_ids:
                p = parent_of.get(nid)
                if p:
                    children_of.setdefault(p, []).append(nid)

            pos = {}
            root_node = next(iter(root_ids)) if root_ids else None
            if root_node:
                pos[root_node] = (0.0, 0.0)

            R_PRODUCT = 220
            R_SITE = 350
            R_PRICE = 480

            product_list = sorted(product_ids)
            n_products = len(product_list)

            for i, nid in enumerate(product_list):
                angle = 2.0 * math.pi * i / n_products
                pos[nid] = (R_PRODUCT * math.cos(angle),
                            R_PRODUCT * math.sin(angle))

                half_wedge = math.pi / max(n_products, 1)
                left = angle - half_wedge + 0.08
                right = angle + half_wedge - 0.08

                for cid in children_of.get(nid, []):
                    if cid in site_ids:
                        r = R_SITE
                    elif cid in price_ids:
                        r = R_PRICE
                    else:
                        continue
                    siblings = [x for x in children_of.get(nid, [])
                                if (x in site_ids) == (cid in site_ids)]
                    k = siblings.index(cid)
                    n_sib = len(siblings)
                    frac = k / max(n_sib - 1, 1) if n_sib > 1 else 0.5
                    child_angle = left + frac * (right - left)
                    pos[cid] = (r * math.cos(child_angle),
                                r * math.sin(child_angle))

            node_idx = {}
            graph_nodes = []
            for i, (nid, lbl, (r, g, b), sz, ntype) in enumerate(gnodes):
                x, y = pos.get(nid, (0, 0))
                node_idx[nid] = i
                graph_nodes.append(GraphNode(nid, lbl, ntype, x, y, sz, (r, g, b)))

            # Inject cached positions for continuity across filter toggles
            if self._cached_pos:
                for nd in graph_nodes:
                    cp = self._cached_pos.get(nd.id)
                    if cp:
                        nd.x, nd.y = cp
                        nd.vx = nd.vy = 0.0

            graph_edges = []
            for u, v, (cr, cg, cb), dsh in gedges:
                ui = node_idx.get(u)
                vi = node_idx.get(v)
                if ui is not None and vi is not None:
                    graph_edges.append((ui, vi, (cr, cg, cb), dsh))

            self._physics.stop()
            self._nodes = graph_nodes
            self._edges = graph_edges
            lod = _lod_decision(len(graph_nodes))
            self._lod_active = lod["lod"]
            self._canvas.set_data(graph_nodes, graph_edges, labels=lod["labels"])
            if lod["physics"]:
                self._physics.start(graph_nodes, graph_edges)
            else:
                self._canvas._scene.reposition_labels()
                self._canvas.fitInView(self._canvas._scene.itemsBoundingRect().adjusted(-60, -60, 60, 60),
                                       Qt.KeepAspectRatio)
            lod_note = f" | LOD: подписи и физика отключены ({len(graph_nodes)} нод)" if self._lod_active else ""
            self._info.setText(f"Узлов: {len(graph_nodes)} | Связей: {len(graph_edges)}{lod_note}")
        except Exception as e:
            import traceback
            traceback.print_exc()
            self._info.setText(f"Error: {e}")

    def _on_stabilized(self):
        if self._nodes:
            self._cached_pos = {node.id: (node.x, node.y) for node in self._nodes}
        self._canvas._scene.sync_all(update_labels=True)
        self._info.setText(f"  Узлов: {len(self._nodes)} | Связей: {len(self._edges)} | Стабилен ✓")

    def _show_node_info(self, node_id):
        if node_id is None:
            self._info_panel.hide()
            return
        node = next((n for n in self._nodes if n.id == node_id), None)
        if not node:
            self._info_panel.hide()
            return
        details = []
        if self._engine and node.type == "product":
            products = self._engine.get_all_products()
            pdata = products.get(node.id)
            if pdata:
                spec = pdata.get("spec_text", "")
                if spec:
                    details.append("📋 %s" % (spec[:120] + ("…" if len(spec) > 120 else "")))
                prices = self._engine.get_all_confirmed_prices()
                node_prices = [p for p in prices if p.get("product_type_id") == node.id]
                if node_prices:
                    details.append("💰 Цен: %d" % len(node_prices))
                    for p in node_prices[:3]:
                        details.append("   ₽%.0f (%s)" % (p.get("price", 0), p.get("date", "?")))
        elif self._engine and node.type == "site":
            sites = self._engine.get_all_sites()
            sdata = sites.get(node.id)
            if sdata:
                url = sdata.get("url", "")
                if url and url != node.id:
                    details.append("🔗 %s" % url)
        elif self._engine and node.type == "root":
            products = self._engine.get_all_products()
            details.append("📦 Товаров: %d" % len(products))
            sites = self._engine.get_all_sites()
            details.append("🌐 Сайтов: %d" % len(sites))
        elif node.type == "price":
            details.append("💰 Цена")

        if not details:
            details.append("")

        body = "<br>".join(_html.escape(d, quote=False) for d in details)
        self._info_panel.show_info(node.id, node.type, node.label, ui_icons.replace_emojis(body, px=12))
