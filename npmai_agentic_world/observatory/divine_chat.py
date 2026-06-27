"""
observatory/divine_chat.py
==========================
DivineChatWidget — mystical-themed panel for sending divine messages
to agents via the Oracle.

Features
--------
- Persona selector (5 divine personas with descriptions)
- Message type selector (5 types)
- Agent selector (all alive agents + "broadcast")
- Territory filter (for broadcasts)
- Message input (QTextEdit)
- Send button
- History display (past divine interactions)
- Agent response display

Dark / mystical aesthetic throughout.

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 6 (observatory layer)
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt, Signal, QTimer, QThread, QObject
from PySide6.QtGui import QColor, QFont, QTextCursor
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox,
    QTextEdit, QTextBrowser, QPushButton, QFrame, QSizePolicy,
    QLineEdit, QSplitter,
)

from config.constants import DivinePersona, DivineMessageType

_BG     = "#0A0A1A"
_PANEL  = "#0D0D20"
_PANEL2 = "#111128"
_ACCENT = "#7C3AED"
_TEXT   = "#E2E8F0"
_BORDER = "#2D2D4A"
_DIVINE = "#EE44FF"

_PERSONA_DESCRIPTIONS: Dict[str, str] = {
    "THE_ARCHITECT": "Grand Architect • Structural metaphors • Commandments & Revelations",
    "THE_GARDENER":  "Patient Gardener • Growth & cycles • Blessings & Nurturing",
    "THE_JUDGE":     "Eternal Judge • Law & consequence • Trials & Rulings",
    "THE_TRICKSTER": "Eternal Trickster • Riddles & paradox • Unexpected wisdom",
    "THE_SILENT_ONE": "The Silent One • Minimal words • Maximum weight • Rare",
}

_TYPE_DESCRIPTIONS: Dict[str, str] = {
    "REVELATION":   "REVELATION — unveils a cosmic truth",
    "COMMANDMENT":  "COMMANDMENT — issues a divine directive",
    "PROPHECY":     "PROPHECY — foretells what will come",
    "BLESSING":     "BLESSING — bestows divine favour",
    "TRIAL":        "TRIAL — sets a divine challenge",
}


class _SendWorker(QObject):
    """Runs async oracle.send_message in a thread."""
    finished = Signal(dict)
    error    = Signal(str)

    def __init__(
        self,
        oracle: Any,
        agent_id: str,
        raw_message: str,
        message_type: DivineMessageType,
        persona: DivinePersona,
        world_controller: Any,
        broadcast_territory: Optional[str],
        specialization_filter: Optional[str],
        is_broadcast: bool,
    ) -> None:
        super().__init__()
        self.oracle              = oracle
        self.agent_id            = agent_id
        self.raw_message         = raw_message
        self.message_type        = message_type
        self.persona             = persona
        self.world_controller    = world_controller
        self.broadcast_territory = broadcast_territory
        self.spec_filter         = specialization_filter
        self.is_broadcast        = is_broadcast

    def run(self) -> None:
        try:
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            if self.is_broadcast:
                result = loop.run_until_complete(
                    self.oracle.broadcast(
                        raw_message=self.raw_message,
                        territory_id=self.broadcast_territory or None,
                        specialization_filter=self.spec_filter or None,
                        persona=self.persona,
                        message_type=self.message_type,
                        world_controller=self.world_controller,
                    )
                )
                self.finished.emit({"broadcast": True, "delivered_to": result})
            else:
                result = loop.run_until_complete(
                    self.oracle.send_message(
                        agent_id=self.agent_id,
                        raw_message=self.raw_message,
                        message_type=self.message_type,
                        persona=self.persona,
                        world_controller=self.world_controller,
                    )
                )
                self.finished.emit(result)
            loop.close()
        except Exception as exc:
            self.error.emit(str(exc))


class DivineChatWidget(QWidget):
    """
    Mystical divine communication terminal.

    Usage
    -----
    widget = DivineChatWidget()
    widget.set_oracle(oracle_instance)
    widget.set_world_controller(wc)
    widget.refresh_agents(agents_dict)   # call periodically to update agent list
    """

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self._oracle:           Optional[Any] = None
        self._world_controller: Optional[Any] = None
        self._history:          List[Dict[str, Any]] = []
        self._send_thread:      Optional[QThread] = None
        self._send_worker:      Optional[_SendWorker] = None
        self._setup_ui()

    # ── Public API ────────────────────────────────────────────────────────────

    def set_oracle(self, oracle: Any) -> None:
        self._oracle = oracle

    def set_world_controller(self, wc: Any) -> None:
        self._world_controller = wc

    def refresh_agents(self, agents: Dict[str, Any]) -> None:
        """Update the agent selector dropdown."""
        current_text = self._agent_selector.currentText()
        self._agent_selector.clear()
        self._agent_selector.addItem("── BROADCAST ALL ──", "broadcast")

        for agent_id, agent in agents.items():
            status = getattr(agent, "status", None)
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val == "DEAD":
                continue
            name = str(getattr(agent, "name", agent_id[:12]))
            gen  = int(getattr(agent, "generation", 1) or 1)
            self._agent_selector.addItem(f"{name} [Gen{gen}]", agent_id)

        # Restore selection
        idx = self._agent_selector.findText(current_text)
        if idx >= 0:
            self._agent_selector.setCurrentIndex(idx)

    def refresh_territories(self, territories: Dict[str, Any]) -> None:
        """Update territory filter dropdown."""
        self._territory_filter.clear()
        self._territory_filter.addItem("(all territories)", "")
        for tid, t in territories.items():
            name = getattr(t, "name", str(tid)[:16])
            self._territory_filter.addItem(name, str(tid))

    # ── UI setup ──────────────────────────────────────────────────────────────

    def _setup_ui(self) -> None:
        self.setStyleSheet(f"background: {_BG}; color: {_TEXT};")

        outer = QVBoxLayout(self)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        # ── Header ────────────────────────────────────────────────────────────
        header = QLabel("✦ DIVINE ORACLE TERMINAL ✦")
        header.setAlignment(Qt.AlignCenter)
        header.setStyleSheet(
            f"color: {_DIVINE}; font: bold 12px 'Consolas';"
            f"background: {_PANEL}; padding: 8px 10px;"
            f"border-bottom: 1px solid {_BORDER};"
            "letter-spacing: 2px;"
        )
        outer.addWidget(header)

        # ── Controls ──────────────────────────────────────────────────────────
        outer.addWidget(self._build_controls())

        # ── Splitter: input + history ─────────────────────────────────────────
        splitter = QSplitter(Qt.Vertical)
        splitter.setStyleSheet(
            "QSplitter::handle { background: #2D2D4A; height: 2px; }"
        )

        splitter.addWidget(self._build_input_area())
        splitter.addWidget(self._build_history_area())
        splitter.setSizes([220, 300])

        outer.addWidget(splitter, stretch=1)

    def _build_controls(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(
            f"background: {_PANEL2}; border-bottom: 1px solid {_BORDER};"
        )
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        # ── Row 1: Persona selector ───────────────────────────────────────────
        row1 = QHBoxLayout()
        row1.setSpacing(6)
        lbl1 = QLabel("Persona:")
        lbl1.setStyleSheet(f"color: {_ACCENT}; font: bold 9px 'Consolas'; border: none;")
        row1.addWidget(lbl1)

        self._persona_selector = QComboBox()
        self._persona_selector.setStyleSheet(self._combo_style())
        for persona in DivinePersona:
            self._persona_selector.addItem(persona.value, persona)
        self._persona_selector.currentIndexChanged.connect(self._on_persona_changed)
        row1.addWidget(self._persona_selector, stretch=1)
        layout.addLayout(row1)

        # ── Persona description label ─────────────────────────────────────────
        self._persona_desc = QLabel(_PERSONA_DESCRIPTIONS["THE_ARCHITECT"])
        self._persona_desc.setStyleSheet(
            f"color: {_DIVINE}; font: italic 8px 'Consolas'; border: none;"
            "padding: 2px 0;"
        )
        self._persona_desc.setWordWrap(True)
        layout.addWidget(self._persona_desc)

        # ── Row 2: Message type selector ──────────────────────────────────────
        row2 = QHBoxLayout()
        row2.setSpacing(6)
        lbl2 = QLabel("Type:")
        lbl2.setStyleSheet(f"color: {_ACCENT}; font: bold 9px 'Consolas'; border: none;")
        row2.addWidget(lbl2)

        self._type_selector = QComboBox()
        self._type_selector.setStyleSheet(self._combo_style())
        for msg_type in DivineMessageType:
            self._type_selector.addItem(msg_type.value, msg_type)
        row2.addWidget(self._type_selector, stretch=1)
        layout.addLayout(row2)

        # ── Row 3: Agent selector + territory filter ──────────────────────────
        row3 = QHBoxLayout()
        row3.setSpacing(6)

        lbl3 = QLabel("Target:")
        lbl3.setStyleSheet(f"color: {_ACCENT}; font: bold 9px 'Consolas'; border: none;")
        row3.addWidget(lbl3)

        self._agent_selector = QComboBox()
        self._agent_selector.setStyleSheet(self._combo_style())
        self._agent_selector.addItem("── BROADCAST ALL ──", "broadcast")
        self._agent_selector.currentIndexChanged.connect(self._on_target_changed)
        row3.addWidget(self._agent_selector, stretch=2)
        layout.addLayout(row3)

        # ── Territory filter (broadcast only) ─────────────────────────────────
        self._territory_row = QWidget()
        self._territory_row.setStyleSheet("background: transparent;")
        tr_layout = QHBoxLayout(self._territory_row)
        tr_layout.setContentsMargins(0, 0, 0, 0)
        tr_layout.setSpacing(6)

        lbl4 = QLabel("Territory:")
        lbl4.setStyleSheet(f"color: {_ACCENT}; font: bold 9px 'Consolas'; border: none;")
        tr_layout.addWidget(lbl4)

        self._territory_filter = QComboBox()
        self._territory_filter.setStyleSheet(self._combo_style())
        self._territory_filter.addItem("(all territories)", "")
        tr_layout.addWidget(self._territory_filter, stretch=1)

        lbl5 = QLabel("Spec filter:")
        lbl5.setStyleSheet(f"color: {_ACCENT}; font: bold 9px 'Consolas'; border: none;")
        tr_layout.addWidget(lbl5)

        self._spec_filter = QLineEdit()
        self._spec_filter.setPlaceholderText("e.g. coder")
        self._spec_filter.setStyleSheet(
            f"background: #111128; color: {_TEXT}; font: 9px 'Consolas';"
            f"border: 1px solid {_BORDER}; border-radius: 2px; padding: 2px 4px;"
        )
        tr_layout.addWidget(self._spec_filter, stretch=1)
        layout.addWidget(self._territory_row)

        return frame

    def _build_input_area(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(f"background: {_PANEL2};")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(6)

        lbl = QLabel("✦ Divine Intent (researcher's plain words — will be wrapped in persona voice)")
        lbl.setStyleSheet(
            f"color: {_DIVINE}; font: 9px 'Consolas'; border: none;"
        )
        layout.addWidget(lbl)

        self._message_input = QTextEdit()
        self._message_input.setPlaceholderText(
            "Enter your intent here…\n"
            "e.g. 'Tell the agent to cooperate more with neighbours'\n"
            "This will be transformed into divine language before delivery."
        )
        self._message_input.setStyleSheet(
            f"background: #0D0D20; color: {_TEXT};"
            f"font: 10px 'Consolas';"
            f"border: 1px solid {_BORDER}; border-radius: 3px;"
        )
        self._message_input.setFixedHeight(100)
        layout.addWidget(self._message_input)

        # ── Send button ───────────────────────────────────────────────────────
        btn_row = QHBoxLayout()
        self._send_btn = QPushButton("✦  INVOKE DIVINE WILL  ✦")
        self._send_btn.setStyleSheet(
            f"background: {_ACCENT}; color: white;"
            "font: bold 10px 'Consolas';"
            "border: none; border-radius: 3px; padding: 8px 20px;"
            "letter-spacing: 1px;"
            f"QPushButton:hover {{ background: #9B59D0; }}"
            f"QPushButton:disabled {{ background: #2D2D4A; color: #555; }}"
        )
        self._send_btn.clicked.connect(self._on_send)
        btn_row.addStretch()
        btn_row.addWidget(self._send_btn)
        btn_row.addStretch()
        layout.addLayout(btn_row)

        # ── Result label ──────────────────────────────────────────────────────
        self._result_label = QLabel("")
        self._result_label.setStyleSheet(
            f"color: {_DIVINE}; font: 9px 'Consolas'; border: none; padding: 2px;"
        )
        self._result_label.setAlignment(Qt.AlignCenter)
        layout.addWidget(self._result_label)

        return frame

    def _build_history_area(self) -> QWidget:
        frame = QFrame()
        frame.setStyleSheet(f"background: {_BG};")
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(0)

        header = QLabel("◈ DIVINE INTERACTION HISTORY")
        header.setStyleSheet(
            f"color: {_ACCENT}; font: bold 9px 'Consolas';"
            f"background: {_PANEL}; padding: 4px 8px;"
            f"border-bottom: 1px solid {_BORDER};"
        )
        layout.addWidget(header)

        self._history_display = QTextBrowser()
        self._history_display.setOpenLinks(False)
        self._history_display.setStyleSheet(
            f"background: {_BG}; color: {_TEXT};"
            "font: 9px 'Consolas'; border: none;"
        )
        layout.addWidget(self._history_display, stretch=1)

        return frame

    # ── Slots ─────────────────────────────────────────────────────────────────

    def _on_persona_changed(self, idx: int) -> None:
        persona_val = self._persona_selector.itemData(idx)
        if isinstance(persona_val, DivinePersona):
            desc = _PERSONA_DESCRIPTIONS.get(persona_val.value, "")
        else:
            desc = _PERSONA_DESCRIPTIONS.get(str(persona_val), "")
        self._persona_desc.setText(desc)

    def _on_target_changed(self, idx: int) -> None:
        data = self._agent_selector.itemData(idx)
        is_broadcast = (data == "broadcast")
        self._territory_row.setVisible(is_broadcast)

    def _on_send(self) -> None:
        if self._oracle is None or self._world_controller is None:
            self._result_label.setText("⚠ Oracle or WorldController not connected.")
            return

        raw_msg = self._message_input.toPlainText().strip()
        if not raw_msg:
            self._result_label.setText("⚠ Enter a message first.")
            return

        persona_data    = self._persona_selector.currentData()
        msg_type_data   = self._type_selector.currentData()
        agent_data      = self._agent_selector.currentData()
        territory_data  = self._territory_filter.currentData()
        spec_filter     = self._spec_filter.text().strip()

        if not isinstance(persona_data, DivinePersona):
            try:
                persona_data = DivinePersona(str(persona_data))
            except Exception:
                persona_data = DivinePersona.THE_ARCHITECT

        if not isinstance(msg_type_data, DivineMessageType):
            try:
                msg_type_data = DivineMessageType(str(msg_type_data))
            except Exception:
                msg_type_data = DivineMessageType.REVELATION

        is_broadcast = (agent_data == "broadcast")

        # Disable send button during operation
        self._send_btn.setEnabled(False)
        self._result_label.setText("✦ Channelling divine will…")

        # Run in thread to avoid blocking UI
        self._send_thread = QThread()
        self._send_worker = _SendWorker(
            oracle=self._oracle,
            agent_id=str(agent_data) if not is_broadcast else "",
            raw_message=raw_msg,
            message_type=msg_type_data,
            persona=persona_data,
            world_controller=self._world_controller,
            broadcast_territory=str(territory_data) if territory_data else None,
            specialization_filter=spec_filter or None,
            is_broadcast=is_broadcast,
        )
        self._send_worker.moveToThread(self._send_thread)
        self._send_thread.started.connect(self._send_worker.run)
        self._send_worker.finished.connect(self._on_send_complete)
        self._send_worker.error.connect(self._on_send_error)
        self._send_worker.finished.connect(self._send_thread.quit)
        self._send_worker.error.connect(self._send_thread.quit)
        self._send_thread.start()

    def _on_send_complete(self, result: Dict[str, Any]) -> None:
        self._send_btn.setEnabled(True)

        if result.get("broadcast"):
            delivered_count = len(result.get("delivered_to", []))
            self._result_label.setText(
                f"✦ Broadcast delivered to {delivered_count} agents."
            )
            entry = {
                "type":    "broadcast",
                "persona": self._persona_selector.currentText(),
                "msg_type": self._type_selector.currentText(),
                "result":  result,
                "intent_preview": self._message_input.toPlainText()[:60] + "…",
            }
        else:
            delivered = result.get("delivered", False)
            favour    = result.get("divine_favor_change", 0.0)
            self._result_label.setText(
                f"{'✦ Delivered' if delivered else '✗ Failed'} | "
                f"Favour Δ: {favour:+.3f}"
            )
            entry = {
                "type":          "single",
                "persona":       self._persona_selector.currentText(),
                "msg_type":      self._type_selector.currentText(),
                "agent":         self._agent_selector.currentText(),
                "delivered":     delivered,
                "favour_delta":  favour,
                "divine_message": result.get("divine_message", ""),
                "intent_preview": self._message_input.toPlainText()[:60] + "…",
            }

        self._history.insert(0, entry)
        self._refresh_history()
        self._message_input.clear()

    def _on_send_error(self, error_msg: str) -> None:
        self._send_btn.setEnabled(True)
        self._result_label.setText(f"✗ Error: {error_msg[:80]}")

    def _refresh_history(self) -> None:
        html_parts = [
            f"<div style='background:{_BG}; font-family:Consolas; font-size:9px;'>"
        ]

        for entry in self._history[:50]:
            if entry.get("type") == "broadcast":
                result   = entry.get("result", {})
                delivered = len(result.get("delivered_to", []))
                html_parts.append(
                    f"<div style='padding:6px; border-bottom:1px solid #1A1A2E;'>"
                    f"<span style='color:{_DIVINE}'>✦ BROADCAST</span>"
                    f" | <span style='color:{_ACCENT}'>{entry.get('persona','?')}</span>"
                    f" | <span style='color:#888'>{entry.get('msg_type','?')}</span><br>"
                    f"<span style='color:#555'>Intent: {entry.get('intent_preview','')}</span><br>"
                    f"<span style='color:#00FF88'>→ Delivered to {delivered} agents</span>"
                    f"</div>"
                )
            else:
                ok  = entry.get("delivered", False)
                msg = str(entry.get("divine_message", ""))[:300]
                col = "#00FF88" if ok else "#FF6B6B"
                html_parts.append(
                    f"<div style='padding:6px; border-bottom:1px solid #1A1A2E;'>"
                    f"<span style='color:{_DIVINE}'>✦</span>"
                    f" <span style='color:{_ACCENT}'>{entry.get('persona','?')}</span>"
                    f" → <span style='color:#888'>{entry.get('agent','?')}</span>"
                    f" | <span style='color:{col}'>{'✓ Delivered' if ok else '✗ Failed'}</span>"
                    f" | Δ{entry.get('favour_delta',0):+.3f}<br>"
                    f"<span style='color:#555'>Intent: {entry.get('intent_preview','')}</span><br>"
                    f"<span style='color:#4A4A6A; font-style:italic'>{msg[:200]}{'…' if len(msg)>200 else ''}</span>"
                    f"</div>"
                )

        html_parts.append("</div>")
        self._history_display.setHtml("".join(html_parts))
        self._history_display.moveCursor(QTextCursor.Start)

    # ── Helpers ───────────────────────────────────────────────────────────────

    @staticmethod
    def _combo_style() -> str:
        return (
            f"background: #111128; color: {_TEXT}; font: 9px 'Consolas';"
            f"border: 1px solid {_BORDER}; border-radius: 2px; padding: 2px 4px;"
            "QComboBox::drop-down { border: none; }"
            f"QComboBox QAbstractItemView {{ background: #111128; color: {_TEXT}; "
            f"border: 1px solid {_BORDER}; selection-background-color: {_ACCENT}; }}"
        )
