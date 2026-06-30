"""
observatory/agent_grid.py
=========================
AgentGridWidget  — visual grid of all agents as coloured cells
AgentInspectorWidget — full detail panel for a selected agent

Dark theme: bg #0A0A1A, accent #7C3AED, text #E2E8F0

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 6 (observatory layer)
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import math
from typing import Any, Dict, List, Optional

from PySide6.QtCore import (
    Qt, Signal, QTimer, QPoint, QPropertyAnimation,
    QEasingCurve, Property, QSize,
)
from PySide6.QtGui import (
    QColor, QPainter, QPen, QBrush, QFont, QPainterPath,
    QLinearGradient, QRadialGradient, QFontMetrics,
)
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QScrollArea,
    QGridLayout, QFrame, QSizePolicy, QProgressBar, QTextBrowser,
    QGroupBox, QToolTip, QApplication,
)

# ── Colour constants ──────────────────────────────────────────────────────────
BG_COLOR         = QColor("#0A0A1A")
ACCENT_COLOR     = QColor("#7C3AED")
TEXT_COLOR       = QColor("#E2E8F0")
PANEL_COLOR      = QColor("#111128")
BORDER_COLOR     = QColor("#2D2D4A")

CELL_ACTIVE_HIGH = QColor("#00FF88")   # high credits
CELL_ACTIVE_MED  = QColor("#FFD700")   # medium credits
CELL_ACTIVE_LOW  = QColor("#FF8C00")   # low credits
CELL_STARVING    = QColor("#FF0000")   # starving (pulsing)
CELL_MIGRATING   = QColor("#00BFFF")   # migrating (moving shimmer)
CELL_ELDER       = QColor("#9370DB")   # elder
CELL_DEAD        = QColor("#444444")   # dead (faded)

CELL_W = 90
CELL_H = 72


def _status_val(agent: Any) -> str:
    s = getattr(agent, "status", None)
    if s is None:
        return "UNKNOWN"
    return s.value if hasattr(s, "value") else str(s)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ─────────────────────────────────────────────────────────────────────────────
# AgentCell widget — a single cell in the grid
# ─────────────────────────────────────────────────────────────────────────────

class _AgentCellWidget(QWidget):
    """
    A single cell in the agent grid.
    Emits clicked(agent_id) when clicked.
    Shows tooltip with full stats on hover.
    """

    clicked = Signal(str)   # agent_id

    def __init__(self, agent_id: str, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.agent_id     = agent_id
        self.agent_data: Dict[str, Any] = {}
        self._pulse_alpha = 255    # for starving pulse animation
        self._shimmer_x   = 0      # for migrating shimmer
        self._selected    = False

        self.setFixedSize(CELL_W, CELL_H)
        self.setMouseTracking(True)
        self.setCursor(Qt.PointingHandCursor)

        # Pulse timer for STARVING
        self._pulse_timer = QTimer(self)
        self._pulse_timer.timeout.connect(self._pulse_step)
        self._pulse_dir = -5

        # Shimmer timer for MIGRATING
        self._shimmer_timer = QTimer(self)
        self._shimmer_timer.timeout.connect(self._shimmer_step)

    def update_data(self, agent_data: Dict[str, Any]) -> None:
        self.agent_data = agent_data
        status = str(agent_data.get("status", "UNKNOWN")).upper()

        if status == "STARVING":
            if not self._pulse_timer.isActive():
                self._pulse_timer.start(40)
            self._shimmer_timer.stop()
        elif status == "MIGRATING":
            if not self._shimmer_timer.isActive():
                self._shimmer_timer.start(30)
            self._pulse_timer.stop()
        else:
            self._pulse_timer.stop()
            self._shimmer_timer.stop()
            self._pulse_alpha = 255

        self.update()

    def set_selected(self, selected: bool) -> None:
        self._selected = selected
        self.update()

    def _pulse_step(self) -> None:
        self._pulse_alpha += self._pulse_dir * 5
        if self._pulse_alpha <= 80:
            self._pulse_dir = 5
        elif self._pulse_alpha >= 255:
            self._pulse_dir = -5
        self._pulse_alpha = max(80, min(255, self._pulse_alpha))
        self.update()

    def _shimmer_step(self) -> None:
        self._shimmer_x = (self._shimmer_x + 3) % (CELL_W + 20)
        self.update()

    def _cell_color(self) -> QColor:
        status  = str(self.agent_data.get("status", "UNKNOWN")).upper()
        credits = float(self.agent_data.get("credits", 0.0) or 0.0)

        if status == "DEAD":
            return CELL_DEAD
        if status == "STARVING":
            col = QColor(CELL_STARVING)
            col.setAlpha(self._pulse_alpha)
            return col
        if status == "MIGRATING":
            return CELL_MIGRATING
        if status == "ELDER":
            return CELL_ELDER

        # ACTIVE — colour by credits
        if credits >= 50:
            return CELL_ACTIVE_HIGH
        if credits >= 15:
            return CELL_ACTIVE_MED
        return CELL_ACTIVE_LOW

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w, h = self.width(), self.height()
        status  = str(self.agent_data.get("status", "UNKNOWN")).upper()
        credits = float(self.agent_data.get("credits", 0.0) or 0.0)
        name    = str(self.agent_data.get("name", self.agent_id[:8]))
        gen     = int(self.agent_data.get("generation", 1) or 1)

        # ── Background ────────────────────────────────────────────────────────
        painter.fillRect(0, 0, w, h, QColor("#0D0D20"))

        cell_col = self._cell_color()

        # ── MIGRATING shimmer overlay ─────────────────────────────────────────
        if status == "MIGRATING":
            grad = QLinearGradient(self._shimmer_x - 20, 0, self._shimmer_x + 20, h)
            grad.setColorAt(0, QColor(0, 0, 0, 0))
            grad.setColorAt(0.5, QColor(255, 255, 255, 60))
            grad.setColorAt(1, QColor(0, 0, 0, 0))
            painter.fillRect(0, 0, w, h, QBrush(grad))

        # ── Coloured accent bar (top 3px) ─────────────────────────────────────
        painter.fillRect(0, 0, w, 3, cell_col)

        # ── Border ────────────────────────────────────────────────────────────
        pen_color = QColor("#7C3AED") if self._selected else cell_col
        pen_color.setAlpha(180)
        painter.setPen(QPen(pen_color, 1.5))
        painter.drawRect(1, 1, w - 2, h - 2)

        # ── Name label ────────────────────────────────────────────────────────
        font = QFont("Consolas", 7, QFont.Bold)
        painter.setFont(font)
        fm = QFontMetrics(font)

        # Truncate name to fit
        display_name = name
        while fm.horizontalAdvance(display_name) > w - 8 and len(display_name) > 4:
            display_name = display_name[:-2] + "…"

        painter.setPen(QPen(TEXT_COLOR))
        painter.drawText(4, 18, display_name)

        # ── Generation ────────────────────────────────────────────────────────
        small_font = QFont("Consolas", 6)
        painter.setFont(small_font)
        gen_color = QColor(cell_col)
        gen_color.setAlpha(200)
        painter.setPen(QPen(gen_color))
        painter.drawText(4, 32, f"Gen {gen}")

        # ── Credits bar ───────────────────────────────────────────────────────
        bar_x, bar_y, bar_w, bar_h = 4, 40, w - 8, 6
        painter.setPen(Qt.NoPen)
        painter.fillRect(bar_x, bar_y, bar_w, bar_h, QColor("#1A1A2E"))
        fill_w = int(bar_w * min(1.0, credits / 100.0))
        fill_col = QColor(cell_col)
        fill_col.setAlpha(200)
        painter.fillRect(bar_x, bar_y, fill_w, bar_h, fill_col)

        # ── Credits value ─────────────────────────────────────────────────────
        painter.setPen(QPen(TEXT_COLOR))
        painter.setFont(QFont("Consolas", 6))
        painter.drawText(4, 58, f"₡{credits:.1f}")

        # ── Status dot ───────────────────────────────────────────────────────
        dot_col = QColor(cell_col)
        painter.setBrush(QBrush(dot_col))
        painter.setPen(Qt.NoPen)
        painter.drawEllipse(w - 12, 48, 6, 6)

        # ── DEAD overlay ──────────────────────────────────────────────────────
        if status == "DEAD":
            overlay = QColor(0, 0, 0, 140)
            painter.fillRect(0, 0, w, h, overlay)
            painter.setPen(QPen(QColor("#666666")))
            painter.setFont(QFont("Consolas", 7))
            painter.drawText(self.rect(), Qt.AlignCenter, "DEAD")

        painter.end()

    def mousePressEvent(self, event) -> None:
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.agent_id)

    def enterEvent(self, event) -> None:
        data = self.agent_data
        tooltip = (
            f"<b style='color:#7C3AED'>{data.get('name', self.agent_id[:8])}</b><br>"
            f"<code>ID: {self.agent_id[:12]}…</code><br>"
            f"Status: <b>{data.get('status', '?')}</b><br>"
            f"Generation: {data.get('generation', '?')}<br>"
            f"Credits: ₡{float(data.get('credits', 0)):.2f}<br>"
            f"Age: {data.get('age', '?')} ticks<br>"
            f"Territory: {str(data.get('territory_id', '?'))[:12]}<br>"
            f"Reputation: {float(data.get('reputation', 0.5)):.2f}<br>"
            f"Divine Favour: {float(data.get('divine_favor', 0.5)):.2f}"
        )
        QToolTip.showText(event.globalPos(), tooltip, self)


# ─────────────────────────────────────────────────────────────────────────────
# AgentGridWidget
# ─────────────────────────────────────────────────────────────────────────────

class AgentGridWidget(QWidget):
    """
    Grid of coloured cells, one per agent.
    
    Signals
    -------
    agent_selected(str) — emitted when a cell is clicked, carries agent_id

    Public API
    ----------
    refresh(agents: dict)   — update all cells from agents dict
    clear_selection()
    """

    agent_selected = Signal(str)   # agent_id

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._cells:    Dict[str, _AgentCellWidget] = {}
        self._selected: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"background-color: {BG_COLOR.name()};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel("◈ AGENT POPULATION GRID")
        header.setStyleSheet(
            f"color: {ACCENT_COLOR.name()}; font: bold 11px 'Consolas';"
            f"background: #0D0D20; padding: 6px 10px; border-bottom: 1px solid #2D2D4A;"
        )
        outer.addWidget(header)

        # ── Legend ────────────────────────────────────────────────────────────
        legend = self._build_legend()
        outer.addWidget(legend)

        # ── Scroll area ───────────────────────────────────────────────────────
        self._scroll = QScrollArea()
        self._scroll.setWidgetResizable(True)
        self._scroll.setStyleSheet(
            f"QScrollArea {{ background: {BG_COLOR.name()}; border: none; }}"
            "QScrollBar:vertical { background: #111128; width: 8px; }"
            "QScrollBar::handle:vertical { background: #7C3AED; border-radius: 4px; }"
        )

        self._grid_container = QWidget()
        self._grid_container.setStyleSheet(f"background: {BG_COLOR.name()};")
        self._grid_layout = QGridLayout(self._grid_container)
        self._grid_layout.setSpacing(4)
        self._grid_layout.setContentsMargins(8, 8, 8, 8)

        self._scroll.setWidget(self._grid_container)
        outer.addWidget(self._scroll, stretch=1)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status_label = QLabel("Awaiting world data…")
        self._status_label.setStyleSheet(
            "color: #888; font: 9px 'Consolas';"
            "background: #0D0D20; padding: 3px 8px;"
            "border-top: 1px solid #2D2D4A;"
        )
        outer.addWidget(self._status_label)

    def _build_legend(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet("background: #0D0D20; border-bottom: 1px solid #1A1A2E;")
        layout = QHBoxLayout(w)
        layout.setContentsMargins(8, 3, 8, 3)
        layout.setSpacing(12)

        items = [
            ("#00FF88", "Active/High"),
            ("#FFD700", "Active/Med"),
            ("#FF8C00", "Active/Low"),
            ("#FF0000", "Starving"),
            ("#00BFFF", "Migrating"),
            ("#9370DB", "Elder"),
            ("#444444", "Dead"),
        ]
        for color, label in items:
            dot = QLabel("●")
            dot.setStyleSheet(f"color: {color}; font: 10px;")
            lbl = QLabel(label)
            lbl.setStyleSheet("color: #888; font: 8px 'Consolas';")
            layout.addWidget(dot)
            layout.addWidget(lbl)

        layout.addStretch()
        return w

    def refresh(self, agents: Dict[str, Any]) -> None:
        """Update all cells from the agents dict. Auto-resizes grid."""
        if not agents:
            self._status_label.setText("No agents in world.")
            return

        existing_ids = set(self._cells.keys())
        incoming_ids = set(agents.keys())

        # ── Remove dead cells that were cleaned from world ────────────────────
        for removed_id in existing_ids - incoming_ids:
            cell = self._cells.pop(removed_id, None)
            if cell:
                self._grid_layout.removeWidget(cell)
                cell.deleteLater()

        # ── Calculate optimal columns ─────────────────────────────────────────
        available_w = max(self._scroll.viewport().width() - 20, CELL_W + 4)
        cols = max(1, available_w // (CELL_W + 4))

        # ── Update / create cells ─────────────────────────────────────────────
        for idx, (agent_id, agent) in enumerate(agents.items()):
            # Gather data
            status  = _attr(agent, "status", None)
            status_val = status.value if hasattr(status, "value") else str(status)

            data = {
                "name":        str(_attr(agent, "name", agent_id[:8])),
                "generation":  int(_attr(agent, "generation", 1) or 1),
                "credits":     float(_attr(agent, "credits", 0.0) or 0.0),
                "status":      status_val,
                "age":         int(_attr(agent, "age", 0) or 0),
                "territory_id": str(_attr(agent, "territory_id", "") or ""),
                "reputation":  float(_attr(agent, "reputation", 0.5) or 0.5),
                "divine_favor": float(_attr(agent, "divine_favor", 0.5) or 0.5),
            }

            if agent_id not in self._cells:
                cell = _AgentCellWidget(agent_id, self._grid_container)
                cell.clicked.connect(self._on_cell_clicked)
                self._cells[agent_id] = cell

                row, col = divmod(idx, cols)
                self._grid_layout.addWidget(cell, row, col)
            else:
                cell = self._cells[agent_id]

            cell.update_data(data)
            if agent_id == self._selected:
                cell.set_selected(True)

        # Status summary
        alive = sum(
            1 for a in agents.values()
            if str(_attr(a, "status", "")).upper() not in ("DEAD",)
        )
        self._status_label.setText(
            f"Total: {len(agents)} | Alive: {alive} | Dead: {len(agents) - alive}"
        )

    def _on_cell_clicked(self, agent_id: str) -> None:
        # Deselect old
        if self._selected and self._selected in self._cells:
            self._cells[self._selected].set_selected(False)

        self._selected = agent_id
        if agent_id in self._cells:
            self._cells[agent_id].set_selected(True)

        self.agent_selected.emit(agent_id)

    def clear_selection(self) -> None:
        if self._selected and self._selected in self._cells:
            self._cells[self._selected].set_selected(False)
        self._selected = None


# ─────────────────────────────────────────────────────────────────────────────
# AgentInspectorWidget
# ─────────────────────────────────────────────────────────────────────────────

_DARK_PANEL = (
    f"background-color: #111128; color: #E2E8F0;"
    "border: 1px solid #2D2D4A; border-radius: 4px;"
)

_LABEL_STYLE = "color: #7C3AED; font: bold 9px 'Consolas';"
_VALUE_STYLE = "color: #E2E8F0; font: 9px 'Consolas';"


class AgentInspectorWidget(QWidget):
    """
    Detailed inspector panel for a selected agent.

    Shows:
    - Identity card
    - Vitals (credits, health, age progress bar)
    - Capability chromosome (100 coloured bits)
    - Active tools list
    - Recent episodic memories (last 10)
    - Relationship network summary
    - Task history (last 5)
    - Divine messages received
    - Genome parameters
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._current_agent_id: Optional[str] = None
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"background: {BG_COLOR.name()}; color: {TEXT_COLOR.name()};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel("◈ AGENT INSPECTOR")
        header.setStyleSheet(
            f"color: {ACCENT_COLOR.name()}; font: bold 11px 'Consolas';"
            "background: #0D0D20; padding: 6px 10px;"
            "border-bottom: 1px solid #2D2D4A;"
        )
        outer.addWidget(header)

        # ── Scroll area ───────────────────────────────────────────────────────
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(
            f"QScrollArea {{ background: {BG_COLOR.name()}; border: none; }}"
            "QScrollBar:vertical { background: #111128; width: 6px; }"
            "QScrollBar::handle:vertical { background: #7C3AED; border-radius: 3px; }"
        )

        content = QWidget()
        content.setStyleSheet(f"background: {BG_COLOR.name()};")
        self._content_layout = QVBoxLayout(content)
        self._content_layout.setContentsMargins(8, 8, 8, 8)
        self._content_layout.setSpacing(8)

        scroll.setWidget(content)
        outer.addWidget(scroll, stretch=1)

        self._show_empty_state()

    def _show_empty_state(self) -> None:
        lbl = QLabel("Click an agent in the grid to inspect.")
        lbl.setStyleSheet("color: #555; font: 10px 'Consolas'; padding: 20px;")
        lbl.setAlignment(Qt.AlignCenter)
        self._content_layout.addWidget(lbl)

    def _clear_content(self) -> None:
        while self._content_layout.count():
            item = self._content_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

    def inspect_agent(self, agent_id: str, agents: Dict[str, Any]) -> None:
        """Update inspector for the selected agent."""
        self._current_agent_id = agent_id
        agent = agents.get(agent_id)
        if agent is None:
            self._clear_content()
            self._show_empty_state()
            return

        self._clear_content()
        self._build_identity_card(agent_id, agent)
        self._build_vitals(agent)
        self._build_chromosome(agent)
        self._build_tools(agent)
        self._build_memories(agent)
        self._build_relationships(agent)
        self._build_task_history(agent)
        self._build_divine_messages(agent)
        self._build_genome_params(agent)
        self._content_layout.addStretch()

    # ── Section builders ──────────────────────────────────────────────────────

    def _section(self, title: str) -> QFrame:
        frame = QFrame()
        frame.setStyleSheet(_DARK_PANEL)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(4)

        lbl = QLabel(title)
        lbl.setStyleSheet(
            f"color: {ACCENT_COLOR.name()}; font: bold 9px 'Consolas';"
            "border: none; border-bottom: 1px solid #2D2D4A; padding-bottom: 3px;"
        )
        layout.addWidget(lbl)
        return frame

    def _kv(self, key: str, val: str) -> QWidget:
        row = QWidget()
        row.setStyleSheet("background: transparent; border: none;")
        h = QHBoxLayout(row)
        h.setContentsMargins(0, 0, 0, 0)
        h.setSpacing(6)
        k = QLabel(f"{key}:")
        k.setStyleSheet(_LABEL_STYLE + " border: none; min-width: 80px;")
        v = QLabel(str(val))
        v.setStyleSheet(_VALUE_STYLE + " border: none;")
        v.setWordWrap(True)
        h.addWidget(k)
        h.addWidget(v, stretch=1)
        return row

    def _bar(self, value: float, max_val: float, color: str) -> QProgressBar:
        bar = QProgressBar()
        bar.setMinimum(0)
        bar.setMaximum(int(max_val * 10))
        bar.setValue(int(max(0, min(value, max_val)) * 10))
        bar.setFixedHeight(8)
        bar.setTextVisible(False)
        bar.setStyleSheet(
            f"QProgressBar {{ background: #1A1A2E; border: none; border-radius: 3px; }}"
            f"QProgressBar::chunk {{ background: {color}; border-radius: 3px; }}"
        )
        return bar

    def _build_identity_card(self, agent_id: str, agent: Any) -> None:
        frame = self._section("▸ IDENTITY")
        layout = frame.layout()
        layout.addWidget(self._kv("ID",        agent_id[:20] + "…"))
        layout.addWidget(self._kv("Name",      str(_attr(agent, "name", "?"))))
        layout.addWidget(self._kv("Status",    str(_attr(agent, "status", "?"))))
        layout.addWidget(self._kv("Gen",       str(_attr(agent, "generation", "?"))))
        layout.addWidget(self._kv("Parent",    str(_attr(agent, "parent_id", "genesis") or "genesis")[:20]))
        layout.addWidget(self._kv("Lineage",   str(_attr(agent, "lineage_id", "?") or "?")[:20]))
        layout.addWidget(self._kv("Territory", str(_attr(agent, "territory_id", "?") or "?")[:24]))
        born_at = _attr(agent, "born_at", 0) or 0
        layout.addWidget(self._kv("Born@",     f"tick {born_at}"))
        self._content_layout.addWidget(frame)

    def _build_vitals(self, agent: Any) -> None:
        frame = self._section("▸ VITALS")
        layout = frame.layout()

        credits = float(_attr(agent, "credits", 0.0) or 0.0)
        health  = float(_attr(agent, "health", 1.0) or 1.0)
        age     = int(_attr(agent, "age", 0) or 0)
        max_age = int(_attr(agent, "max_age", 1000) or 1000)
        rep     = float(_attr(agent, "reputation", 0.5) or 0.5)
        divf    = float(_attr(agent, "divine_favor", 0.5) or 0.5)

        layout.addWidget(QLabel(f"Credits: ₡{credits:.2f}"))
        layout.addWidget(self._bar(credits, 100, "#00FF88"))

        layout.addWidget(QLabel(f"Health: {health:.2%}"))
        layout.addWidget(self._bar(health, 1.0, "#7C3AED"))

        layout.addWidget(QLabel(f"Age: {age}/{max_age} ticks"))
        layout.addWidget(self._bar(age, max_age, "#FFD700"))

        layout.addWidget(QLabel(f"Reputation: {rep:.3f}"))
        layout.addWidget(self._bar(rep, 1.0, "#00BFFF"))

        layout.addWidget(QLabel(f"Divine Favour: {divf:.3f}"))
        layout.addWidget(self._bar(divf, 1.0, "#9370DB"))

        for w in frame.findChildren(QLabel):
            if w is not frame.layout().itemAt(0).widget():
                w.setStyleSheet(_VALUE_STYLE + " border: none;")

        self._content_layout.addWidget(frame)

    def _build_chromosome(self, agent: Any) -> None:
        frame = self._section("▸ CAPABILITY CHROMOSOME (100-bit)")
        layout = frame.layout()

        genome = _attr(agent, "genome", None)
        if genome is not None:
            chrom = _attr(genome, "capability_chromosome", None)
            bits_str = ""
            if chrom is not None:
                bits_attr = _attr(chrom, "bits", None)
                if bits_attr is not None:
                    if isinstance(bits_attr, str):
                        bits_str = bits_attr
                    elif hasattr(bits_attr, "__iter__"):
                        bits_str = "".join(str(int(b)) for b in bits_attr)
        else:
            bits_str = "0" * 100

        chromosome_widget = _ChromosomeWidget(bits_str)
        layout.addWidget(chromosome_widget)

        active = bits_str.count("1") if bits_str else 0
        lbl = QLabel(f"Active tools: {active}/100")
        lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
        layout.addWidget(lbl)
        self._content_layout.addWidget(frame)

    def _build_tools(self, agent: Any) -> None:
        frame = self._section("▸ ACTIVE TOOLS")
        layout = frame.layout()

        tools = _attr(agent, "active_tools", []) or []
        if not tools:
            lbl = QLabel("No active tools registered.")
            lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
            layout.addWidget(lbl)
        else:
            for i, tool in enumerate(tools[:15]):
                lbl = QLabel(f"  {i+1}. {tool}")
                lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
                layout.addWidget(lbl)
            if len(tools) > 15:
                more = QLabel(f"  … and {len(tools) - 15} more")
                more.setStyleSheet("color: #666; font: 8px 'Consolas'; border: none;")
                layout.addWidget(more)

        self._content_layout.addWidget(frame)

    def _build_memories(self, agent: Any) -> None:
        frame = self._section("▸ RECENT EPISODIC MEMORIES (last 10)")
        layout = frame.layout()

        memories: List[Any] = []
        mem_sys = _attr(agent, "memory", None)
        if mem_sys is not None:
            episodic = _attr(mem_sys, "episodic", None)
            if episodic is not None:
                nodes = _attr(episodic, "nodes", None) or []
                if isinstance(nodes, dict):
                    memories = list(nodes.values())[-10:]
                elif isinstance(nodes, list):
                    memories = nodes[-10:]

        if not memories:
            lbl = QLabel("No episodic memories stored.")
            lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
            layout.addWidget(lbl)
        else:
            for mem in reversed(memories[-10:]):
                content = str(_attr(mem, "content", str(mem)))[:80]
                valence = float(_attr(mem, "emotional_valence", 0.0) or 0.0)
                val_col = "#00FF88" if valence > 0.2 else "#FF6B6B" if valence < -0.2 else "#888"
                lbl = QLabel(f"<span style='color:{val_col}'>{'▲' if valence>0 else '▼' if valence<0 else '●'}</span> {content}")
                lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
                lbl.setWordWrap(True)
                layout.addWidget(lbl)

        self._content_layout.addWidget(frame)

    def _build_relationships(self, agent: Any) -> None:
        frame = self._section("▸ RELATIONSHIPS")
        layout = frame.layout()

        rels = _attr(agent, "relationships", {}) or {}
        if not rels:
            lbl = QLabel("No relationships formed yet.")
            lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
            layout.addWidget(lbl)
        else:
            sorted_rels = sorted(rels.items(), key=lambda x: x[1], reverse=True)
            for rid, trust in sorted_rels[:8]:
                bar_len = int(trust * 20)
                bar = "█" * bar_len + "░" * (20 - bar_len)
                col = "#00FF88" if trust > 0.6 else "#FFD700" if trust > 0.3 else "#FF6B6B"
                lbl = QLabel(f"<span style='color:{col}'>{bar}</span>  {str(rid)[:12]} ({trust:.2f})")
                lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
                layout.addWidget(lbl)
            if len(rels) > 8:
                m = QLabel(f"… {len(rels) - 8} more relationships")
                m.setStyleSheet("color: #555; font: 8px 'Consolas'; border: none;")
                layout.addWidget(m)

        self._content_layout.addWidget(frame)

    def _build_task_history(self, agent: Any) -> None:
        frame = self._section("▸ TASK HISTORY (last 5)")
        layout = frame.layout()

        history = _attr(agent, "task_history", []) or []
        if not history:
            lbl = QLabel("No tasks completed yet.")
            lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
            layout.addWidget(lbl)
        else:
            for task in list(reversed(history))[:5]:
                if isinstance(task, dict):
                    desc  = str(task.get("description", task.get("task", str(task))))[:60]
                    ok    = task.get("success", True)
                    tick  = task.get("tick", "?")
                else:
                    desc = str(task)[:60]
                    ok   = True
                    tick = "?"

                icon  = "✓" if ok else "✗"
                color = "#00FF88" if ok else "#FF6B6B"
                lbl   = QLabel(f"<span style='color:{color}'>{icon}</span> [{tick}] {desc}")
                lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
                lbl.setWordWrap(True)
                layout.addWidget(lbl)

        self._content_layout.addWidget(frame)

    def _build_divine_messages(self, agent: Any) -> None:
        frame = self._section("▸ DIVINE MESSAGES RECEIVED")
        layout = frame.layout()

        msgs = _attr(agent, "divine_messages_received", []) or []
        if not msgs:
            lbl = QLabel("No divine messages received.")
            lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
            layout.addWidget(lbl)
        else:
            for msg in list(reversed(msgs))[:5]:
                persona   = str(msg.get("persona", "UNKNOWN"))
                msg_type  = str(msg.get("message_type", "?"))
                content   = str(msg.get("content", ""))[:80]
                ts        = msg.get("timestamp", "?")
                col = "#9370DB"
                lbl = QLabel(
                    f"<span style='color:{col}'>✦ {persona} [{msg_type}]</span><br>"
                    f"<span style='color:#888'>{content}…</span>"
                )
                lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
                lbl.setWordWrap(True)
                layout.addWidget(lbl)

        self._content_layout.addWidget(frame)

    def _build_genome_params(self, agent: Any) -> None:
        frame = self._section("▸ GENOME PARAMETERS")
        layout = frame.layout()

        genome = _attr(agent, "genome", None)
        if genome is None:
            lbl = QLabel("No genome data available.")
            lbl.setStyleSheet(_VALUE_STYLE + " border: none;")
            layout.addWidget(lbl)
        else:
            params = _attr(genome, "parameter_genes", None)
            if params is not None:
                temp    = _attr(params, "temperature", _attr(params, "get", lambda k, d: d)("temperature", "?"))
                retry   = _attr(params, "retry_limit",  _attr(params, "get", lambda k, d: d)("retry_limit", "?"))
                risk    = _attr(params, "risk_tolerance", _attr(params, "get", lambda k, d: d)("risk_tolerance", "?"))
                coop    = _attr(params, "cooperation_bias", _attr(params, "get", lambda k, d: d)("cooperation_bias", "?"))
                if isinstance(params, dict):
                    temp  = params.get("temperature", "?")
                    retry = params.get("retry_limit", "?")
                    risk  = params.get("risk_tolerance", "?")
                    coop  = params.get("cooperation_bias", "?")

                layout.addWidget(self._kv("Temperature",    str(temp)))
                layout.addWidget(self._kv("Retry Limit",    str(retry)))
                layout.addWidget(self._kv("Risk Tolerance", str(risk)))
                layout.addWidget(self._kv("Cooperation",    str(coop)))

            mutation_rate = _attr(genome, "mutation_rate", "?")
            layout.addWidget(self._kv("Mutation Rate", str(mutation_rate)))

            prompt_dna = _attr(genome, "prompt_dna", None)
            if prompt_dna is not None:
                exons  = _attr(prompt_dna, "exons",  []) or []
                introns = _attr(prompt_dna, "introns", []) or []
                if isinstance(prompt_dna, dict):
                    exons   = prompt_dna.get("exons", [])
                    introns = prompt_dna.get("introns", [])
                layout.addWidget(self._kv("DNA Exons",   str(len(exons)) + " segments"))
                layout.addWidget(self._kv("DNA Introns", str(len(introns)) + " segments"))

        self._content_layout.addWidget(frame)


# ─────────────────────────────────────────────────────────────────────────────
# _ChromosomeWidget — 100-bit visualiser
# ─────────────────────────────────────────────────────────────────────────────

class _ChromosomeWidget(QWidget):
    """Draws 100 coloured bits representing the capability chromosome."""

    def __init__(self, bits_str: str = "", parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.bits_str = bits_str.ljust(100, "0")[:100]
        self.setFixedHeight(30)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Fixed)

    def paintEvent(self, event) -> None:
        painter = QPainter(self)
        w = self.width()
        bit_w = max(1, (w - 4) // 100)
        for i, bit in enumerate(self.bits_str):
            x = 2 + i * bit_w
            color = QColor("#00FF88") if bit == "1" else QColor("#1A1A2E")
            painter.fillRect(x, 4, max(1, bit_w - 1), 18, color)
        painter.end()
