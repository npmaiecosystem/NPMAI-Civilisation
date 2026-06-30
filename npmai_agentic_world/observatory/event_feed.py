"""
observatory/event_feed.py
=========================
EventFeedWidget — scrolling, colour-coded, filterable world event feed
for the NPMAI Agentic World Observatory.

Events are categorised and colour-coded. Newest events appear at the top.
Clicking an event expands its full JSON data.

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 6 (observatory layer)
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Set

from PySide6.QtCore import Qt, Signal, QTimer
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QTextBrowser,
    QCheckBox, QLineEdit, QFrame, QScrollArea, QPushButton,
    QSizePolicy,
)

# ── Category → (display_name, hex_colour) ────────────────────────────────────
_CATEGORY_STYLES: Dict[str, tuple] = {
    "LIFECYCLE":   ("LIFECYCLE",    "#FFFFFF"),
    "ECONOMY":     ("ECONOMY",      "#FFD700"),
    "REPRODUCTION": ("REPRO",       "#00E5FF"),
    "MIGRATION":   ("MIGRATION",    "#00BFFF"),
    "GOVERNANCE":  ("GOVERNANCE",   "#B967FF"),
    "DIVINE":      ("DIVINE",       "#EE44FF"),
    "BAD_ACTIVITY": ("⚠ BAD",       "#FF3333"),
    "SOCIAL":      ("SOCIAL",       "#44FF88"),
    "COGNITION":   ("COGNITION",    "#AAAAFF"),
    "MEMORY":      ("MEMORY",       "#88AAFF"),
    "SYSTEM":      ("SYSTEM",       "#888888"),
    "UNKNOWN":     ("?",            "#666666"),
}

_EVENT_TYPE_TO_CATEGORY: Dict[str, str] = {
    # Lifecycle
    "AGENT_BORN":           "LIFECYCLE",
    "AGENT_DIED":           "LIFECYCLE",
    "AGENT_STARVING":       "LIFECYCLE",
    "WORLD_INITIALIZED":    "LIFECYCLE",
    "WORLD_STARTED":        "LIFECYCLE",
    "WORLD_STOPPED":        "LIFECYCLE",
    "AGENT_LIFECYCLE":      "LIFECYCLE",
    # Economy
    "CREDITS_EARNED":       "ECONOMY",
    "CREDITS_SPENT":        "ECONOMY",
    "CREDIT_TRANSFER":      "ECONOMY",
    "ECONOMY_TICK":         "ECONOMY",
    # Reproduction
    "AGENT_REPRODUCED":     "REPRODUCTION",
    "CHILD_BORN":           "REPRODUCTION",
    # Migration
    "MIGRATION_STARTED":    "MIGRATION",
    "MIGRATION_COMPLETED":  "MIGRATION",
    "TERRITORY_ENTERED":    "MIGRATION",
    # Governance
    "PROPOSAL_CREATED":     "GOVERNANCE",
    "VOTE_CAST":            "GOVERNANCE",
    "LAW_PASSED":           "GOVERNANCE",
    "PROPOSAL_FAILED":      "GOVERNANCE",
    "AGENT_ELECTED":        "GOVERNANCE",
    "AGENT_EXECUTED":       "GOVERNANCE",
    "LAW_VIOLATION":        "GOVERNANCE",
    # Divine
    "DIVINE_MESSAGE_SENT":  "DIVINE",
    "DIVINE_INTERPRETED":   "DIVINE",
    # Bad activity
    "BAD_ACTIVITY":         "BAD_ACTIVITY",
    # Social
    "AGENT_TAUGHT":         "SOCIAL",
    "PHEROMONE_LEFT":       "SOCIAL",
    "MESSAGE_SENT":         "SOCIAL",
    # Cognition
    "TASK_STARTED":         "COGNITION",
    "TASK_COMPLETED":       "COGNITION",
    "TASK_FAILED":          "COGNITION",
    # Memory
    "MEMORY_STORED":        "MEMORY",
    "MEMORY_RETRIEVED":     "MEMORY",
    # System
    "CLOCK_TICK":           "SYSTEM",
    "SYSTEM_ERROR":         "SYSTEM",
    "SNAPSHOT_TAKEN":       "SYSTEM",
}

_BG       = "#0A0A1A"
_PANEL    = "#0D0D20"
_ACCENT   = "#7C3AED"
_TEXT     = "#E2E8F0"
_BORDER   = "#2D2D4A"
_MAX_EVENTS = 2000   # cap to avoid memory bloat


class EventFeedWidget(QWidget):
    """
    Scrolling, colour-coded world event feed.

    Public API
    ----------
    push_event(event: dict)   — add a new event (called from WorldController callback)
    push_events(events: list) — bulk add
    clear()                   — clear all events
    """

    event_clicked = Signal(dict)   # emits full event dict on click

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._events:          List[Dict[str, Any]] = []
        self._active_filters:  Set[str] = set(_CATEGORY_STYLES.keys())
        self._search_term:     str = ""
        self._auto_scroll:     bool = True
        self._setup_ui()

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"background: {_BG};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel("◈ WORLD EVENT FEED")
        header.setStyleSheet(
            f"color: {_ACCENT}; font: bold 11px 'Consolas';"
            f"background: {_PANEL}; padding: 6px 10px;"
            f"border-bottom: 1px solid {_BORDER};"
        )
        outer.addWidget(header)

        # ── Filter bar ────────────────────────────────────────────────────────
        outer.addWidget(self._build_filter_bar())

        # ── Search ────────────────────────────────────────────────────────────
        outer.addWidget(self._build_search_bar())

        # ── Event display ─────────────────────────────────────────────────────
        self._feed = QTextBrowser()
        self._feed.setOpenLinks(False)
        self._feed.setStyleSheet(
            f"background: {_BG}; color: {_TEXT};"
            "font: 9px 'Consolas';"
            f"border: none;"
        )
        self._feed.anchorClicked.connect(self._on_link_clicked)
        outer.addWidget(self._feed, stretch=1)

        # ── Status bar ────────────────────────────────────────────────────────
        self._status = QLabel("No events yet.")
        self._status.setStyleSheet(
            f"color: #666; font: 8px 'Consolas';"
            f"background: {_PANEL}; padding: 2px 8px;"
            f"border-top: 1px solid {_BORDER};"
        )
        outer.addWidget(self._status)

    def _build_filter_bar(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {_PANEL}; border-bottom: 1px solid {_BORDER};")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 3, 6, 3)
        h.setSpacing(6)

        lbl = QLabel("Filter:")
        lbl.setStyleSheet(f"color: #888; font: 8px 'Consolas'; border: none;")
        h.addWidget(lbl)

        self._filter_checks: Dict[str, QCheckBox] = {}
        for cat, (label, color) in _CATEGORY_STYLES.items():
            if cat == "UNKNOWN":
                continue
            cb = QCheckBox(label)
            cb.setChecked(True)
            cb.setStyleSheet(
                f"color: {color}; font: 7px 'Consolas';"
                f"QCheckBox::indicator {{ width: 10px; height: 10px; }}"
            )
            cb.toggled.connect(lambda checked, c=cat: self._toggle_filter(c, checked))
            self._filter_checks[cat] = cb
            h.addWidget(cb)

        h.addStretch()

        # Auto-scroll toggle
        self._autoscroll_cb = QCheckBox("Auto-scroll")
        self._autoscroll_cb.setChecked(True)
        self._autoscroll_cb.setStyleSheet(f"color: {_ACCENT}; font: 7px 'Consolas';")
        self._autoscroll_cb.toggled.connect(lambda v: setattr(self, "_auto_scroll", v))
        h.addWidget(self._autoscroll_cb)

        # Clear button
        clear_btn = QPushButton("Clear")
        clear_btn.setStyleSheet(
            f"color: #888; font: 7px 'Consolas';"
            "background: #1A1A2E; border: 1px solid #2D2D4A;"
            "padding: 1px 6px; border-radius: 2px;"
        )
        clear_btn.clicked.connect(self.clear)
        h.addWidget(clear_btn)

        return w

    def _build_search_bar(self) -> QWidget:
        w = QWidget()
        w.setStyleSheet(f"background: {_PANEL}; border-bottom: 1px solid {_BORDER};")
        h = QHBoxLayout(w)
        h.setContentsMargins(6, 2, 6, 2)
        h.setSpacing(4)

        lbl = QLabel("🔍")
        lbl.setStyleSheet("color: #888; border: none;")
        h.addWidget(lbl)

        self._search_box = QLineEdit()
        self._search_box.setPlaceholderText("Search events…")
        self._search_box.setStyleSheet(
            "background: #111128; color: #E2E8F0;"
            "font: 9px 'Consolas';"
            "border: 1px solid #2D2D4A; border-radius: 2px;"
            "padding: 2px 4px;"
        )
        self._search_box.textChanged.connect(self._on_search_changed)
        h.addWidget(self._search_box, stretch=1)
        return w

    # ── Public API ────────────────────────────────────────────────────────────

    def push_event(self, event: Dict[str, Any]) -> None:
        """Add a single event to the top of the feed."""
        self._events.insert(0, event)
        if len(self._events) > _MAX_EVENTS:
            self._events.pop()
        self._render_visible()
        self._status.setText(
            f"Events: {len(self._events)} | Showing: {self._count_visible()}"
        )

    def push_events(self, events: List[Dict[str, Any]]) -> None:
        """Add multiple events at once."""
        for e in events:
            self._events.insert(0, e)
        if len(self._events) > _MAX_EVENTS:
            del self._events[_MAX_EVENTS:]
        self._render_visible()
        self._status.setText(
            f"Events: {len(self._events)} | Showing: {self._count_visible()}"
        )

    def clear(self) -> None:
        self._events.clear()
        self._feed.clear()
        self._status.setText("Cleared.")

    # ── Internal rendering ────────────────────────────────────────────────────

    def _render_visible(self) -> None:
        visible = self._get_visible_events()

        html_parts = []
        for idx, event in enumerate(visible[:500]):  # cap render at 500
            html_parts.append(self._event_to_html(event, idx))

        self._feed.setHtml(
            f"<div style='background:{_BG}; font-family:Consolas; font-size:9px;'>"
            + "".join(html_parts)
            + "</div>"
        )

        if self._auto_scroll:
            self._feed.moveCursor(QTextCursor.Start)

    def _event_to_html(self, event: Dict[str, Any], idx: int) -> str:
        event_type  = str(event.get("event_type", event.get("type", "UNKNOWN")))
        agent_id    = str(event.get("agent_id", "") or "")
        territory   = str(event.get("territory_id", "") or "")
        timestamp   = event.get("timestamp", 0)
        tick        = event.get("tick", event.get("data", {}).get("tick", "?") if isinstance(event.get("data"), dict) else "?")
        data        = event.get("data", {})

        category = _EVENT_TYPE_TO_CATEGORY.get(event_type, "UNKNOWN")
        _, color  = _CATEGORY_STYLES.get(category, ("?", "#666"))

        # Format timestamp
        if isinstance(timestamp, (int, float)) and timestamp > 0:
            try:
                ts_str = datetime.fromtimestamp(timestamp / 1000, tz=timezone.utc).strftime("%H:%M:%S")
            except Exception:
                ts_str = str(timestamp)
        else:
            ts_str = "--:--:--"

        agent_short    = agent_id[:12] + "…" if len(agent_id) > 12 else agent_id
        territory_short = territory[:8] + "…" if len(territory) > 8 else territory

        # Data summary (first 120 chars of JSON)
        data_summary = ""
        if data and isinstance(data, dict):
            try:
                raw = json.dumps(data, default=str)
                data_summary = raw[:120] + ("…" if len(raw) > 120 else "")
            except Exception:
                data_summary = str(data)[:120]

        is_bad = category == "BAD_ACTIVITY"
        bg_color = "#200A0A" if is_bad else "transparent"
        font_weight = "bold" if is_bad else "normal"

        # Build row with clickable link on event type
        link_id = f"event_{idx}"
        return (
            f"<div style='background:{bg_color}; padding:3px 6px; "
            f"border-bottom:1px solid #111128; font-weight:{font_weight};'>"
            f"<span style='color:#555'>{ts_str}</span>&nbsp;"
            f"<span style='color:{color}; font-weight:bold'>[{category[:8]:8}]</span>&nbsp;"
            f"<a href='{link_id}' style='color:{color}; text-decoration:none;'>"
            f"{event_type}</a>&nbsp;"
            f"<span style='color:#666'>"
            f"{'@'+agent_short if agent_short else ''}"
            f"{' T:'+territory_short if territory_short else ''}"
            f"</span>"
            f"{'<br><span style=color:#444>' + data_summary + '</span>' if data_summary else ''}"
            f"</div>"
        )

    def _get_visible_events(self) -> List[Dict[str, Any]]:
        result = []
        for event in self._events:
            event_type = str(event.get("event_type", event.get("type", "UNKNOWN")))
            category   = _EVENT_TYPE_TO_CATEGORY.get(event_type, "UNKNOWN")
            if category not in self._active_filters:
                continue
            if self._search_term:
                search_in = (
                    event_type.lower()
                    + str(event.get("agent_id", "")).lower()
                    + json.dumps(event.get("data", {}), default=str).lower()
                )
                if self._search_term.lower() not in search_in:
                    continue
            result.append(event)
        return result

    def _count_visible(self) -> int:
        return len(self._get_visible_events())

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _toggle_filter(self, category: str, checked: bool) -> None:
        if checked:
            self._active_filters.add(category)
        else:
            self._active_filters.discard(category)
        self._render_visible()

    def _on_search_changed(self, text: str) -> None:
        self._search_term = text
        self._render_visible()

    def _on_link_clicked(self, url) -> None:
        link_str = url.toString()
        if link_str.startswith("event_"):
            try:
                idx = int(link_str.split("_")[1])
                visible = self._get_visible_events()
                if 0 <= idx < len(visible):
                    self.event_clicked.emit(visible[idx])
            except (ValueError, IndexError):
                pass
