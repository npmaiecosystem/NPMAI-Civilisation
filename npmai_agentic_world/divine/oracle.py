"""
divine/oracle.py
================
Oracle — the divine communication layer between human researchers and
AI agents in the NPMAI Agentic World.

The Oracle wraps researcher intent in a chosen persona's voice and routes
the resulting divine message to agents via the MessageBroker. Agents
never see raw researcher text — only the divine framing.

Flow
----
Researcher → Oracle.send_message() → PersonaManager wraps in divine voice
→ MessageBroker delivers to agent.receive_divine_message()
→ Agent's Planner interprets as divine signal (not human instruction)
→ DIVINE_MESSAGE_SENT + DIVINE_INTERPRETED logged to Supabase

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 6 (divine layer)
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.constants import DivinePersona, DivineMessageType
from data.event_logger import EventLogger
from data.event_types import WorldEventType
from divine.personas import PersonaManager
from divine.message_broker import MessageBroker

logger = logging.getLogger("npmai_world.divine.oracle")


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# ── Divine favour constants ───────────────────────────────────────────────────
_FAVOUR_DELTA: Dict[str, float] = {
    DivineMessageType.BLESSING.value:    +0.10,
    DivineMessageType.REVELATION.value:  +0.05,
    DivineMessageType.PROPHECY.value:    +0.03,
    DivineMessageType.COMMANDMENT.value: +0.01,   # neutral-to-positive
    DivineMessageType.TRIAL.value:       -0.05,   # trials reduce favour until passed
}


class Oracle:
    """
    Central class for all divine interventions in the NPMAI Agentic World.

    The Oracle:
    - Maintains a history of all divine messages sent (in-memory + Supabase)
    - Wraps researcher intent in persona voice via PersonaManager
    - Routes wrapped messages to agents via MessageBroker
    - Tracks divine_favor changes per agent
    - Never exposes researcher identity to agents

    Singleton-friendly — one instance per world run is sufficient.

    Usage
    -----
    oracle = Oracle()
    result = await oracle.send_message(
        agent_id="abc123-...",
        raw_message="Tell the agent to cooperate with its neighbours more",
        message_type=DivineMessageType.COMMANDMENT,
        persona=DivinePersona.THE_ARCHITECT,
        world_controller=wc,
    )
    # result = {"delivered": True, "agent_response": None,
    #            "divine_favor_change": 0.01, "message_id": "..."}
    """

    def __init__(self, event_logger: Optional[EventLogger] = None) -> None:
        self._persona_manager = PersonaManager()
        self._broker          = MessageBroker()
        self._history:        List[Dict[str, Any]] = []   # in-memory log
        self._logger          = event_logger

    # ─────────────────────────────────────────────────────────────────────────
    # send_message — single-agent delivery
    # ─────────────────────────────────────────────────────────────────────────

    async def send_message(
        self,
        agent_id: str,
        raw_message: str,
        message_type: DivineMessageType,
        persona: DivinePersona,
        world_controller: Any,
    ) -> Dict[str, Any]:
        """
        Send a divine message to a specific agent.

        Parameters
        ----------
        agent_id         : target agent's ID string
        raw_message      : researcher's plain intent (NEVER sent verbatim)
        message_type     : REVELATION/COMMANDMENT/PROPHECY/BLESSING/TRIAL
        persona          : which divine persona is speaking
        world_controller : WorldController holding agents dict

        Returns
        -------
        {
            "delivered":          bool,
            "message_id":         str,
            "agent_response":     str | None,  # if agent replied synchronously
            "divine_favor_change": float,
            "divine_message":     str,         # the wrapped message text
            "timestamp":          int,
        }
        """
        message_id = str(uuid.uuid4())
        timestamp  = _utc_now_ms()

        # ── Gather agent context for persona to reference ─────────────────────
        agent_context = self._get_agent_context(agent_id, world_controller)

        # ── Wrap in persona voice ─────────────────────────────────────────────
        divine_text = self._persona_manager.generate_divine_message(
            persona=persona,
            intent=raw_message,
            message_type=message_type,
            target_agent_context=agent_context,
        )

        # ── Build message payload ─────────────────────────────────────────────
        divine_message = {
            "message_id":    message_id,
            "persona":       persona.value if isinstance(persona, DivinePersona) else str(persona),
            "message_type":  message_type.value if isinstance(message_type, DivineMessageType) else str(message_type),
            "content":       divine_text,
            "timestamp":     timestamp,
            "target_agent":  agent_id,
        }

        # ── Deliver via MessageBroker ─────────────────────────────────────────
        delivered = await self._broker.deliver(
            agent_id=agent_id,
            divine_message=divine_message,
            world_controller=world_controller,
        )

        # ── Calculate divine favour delta ─────────────────────────────────────
        msg_type_val = message_type.value if isinstance(message_type, DivineMessageType) else str(message_type)
        favour_delta = _FAVOUR_DELTA.get(msg_type_val, 0.0)
        if delivered:
            self._apply_divine_favour(agent_id, favour_delta, world_controller)

        # ── Log DIVINE_MESSAGE_SENT ───────────────────────────────────────────
        territory_id = agent_context.get("territory_id")
        await self._logger.log(
            event_type=WorldEventType.DIVINE_MESSAGE_SENT,
            agent_id=agent_id,
            territory_id=territory_id,
            data={
                "message_id":          message_id,
                "persona":             divine_message["persona"],
                "message_type":        divine_message["message_type"],
                "delivered":           delivered,
                "divine_favor_change": round(favour_delta, 4),
                "content_preview":     divine_text[:200],
                "timestamp":           timestamp,
            },
        )

        # ── Record in in-memory history ───────────────────────────────────────
        history_entry = {
            **divine_message,
            "delivered":           delivered,
            "divine_favor_change": round(favour_delta, 4),
            "raw_intent":          "[REDACTED]",   # never store raw researcher text
        }
        self._history.append(history_entry)

        logger.info(
            "Oracle.send_message → agent=%s persona=%s type=%s delivered=%s",
            agent_id[:8],
            divine_message["persona"],
            divine_message["message_type"],
            delivered,
        )

        return {
            "delivered":           delivered,
            "message_id":          message_id,
            "agent_response":      None,   # agents process asynchronously via tick
            "divine_favor_change": round(favour_delta, 4),
            "divine_message":      divine_text,
            "timestamp":           timestamp,
        }

    # ─────────────────────────────────────────────────────────────────────────
    # broadcast — multi-agent delivery
    # ─────────────────────────────────────────────────────────────────────────

    async def broadcast(
        self,
        raw_message: str,
        territory_id: Optional[str] = None,
        specialization_filter: Optional[str] = None,
        persona: DivinePersona = DivinePersona.THE_ARCHITECT,
        message_type: DivineMessageType = DivineMessageType.REVELATION,
        world_controller: Any = None,
    ) -> List[str]:
        """
        Send a divine message to multiple agents simultaneously.

        Routing logic
        -------------
        - If `territory_id` is given  → only agents in that territory
        - If `specialization_filter` → only agents whose specialization matches
        - Both can be combined
        - If neither → ALL alive agents receive the broadcast

        Parameters
        ----------
        raw_message           : researcher's plain intent
        territory_id          : optional territory filter
        specialization_filter : optional specialization substring filter
        persona               : which persona broadcasts (default: THE_ARCHITECT)
        message_type          : default REVELATION for broadcasts
        world_controller      : WorldController instance

        Returns
        -------
        List[str] — agent_ids that successfully received the message
        """
        if world_controller is None:
            logger.error("Oracle.broadcast: world_controller is None")
            return []

        agents = getattr(world_controller, "agents", {}) or {}
        delivered_to: List[str] = []

        # ── Build candidate list ──────────────────────────────────────────────
        candidates: List[str] = []
        for agent_id, agent in agents.items():
            status = _attr(agent, "status", None)
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val == "DEAD":
                continue

            if territory_id:
                agent_territory = str(_attr(agent, "territory_id", ""))
                if agent_territory != str(territory_id):
                    continue

            if specialization_filter:
                spec = str(_attr(agent, "specialization", "") or "")
                if specialization_filter.lower() not in spec.lower():
                    continue

            candidates.append(str(agent_id))

        if not candidates:
            logger.info("Oracle.broadcast: no candidates matched filters")
            return []

        logger.info(
            "Oracle.broadcast: %d candidates | territory=%s | spec_filter=%s | persona=%s",
            len(candidates), territory_id, specialization_filter,
            persona.value if isinstance(persona, DivinePersona) else persona,
        )

        # ── Deliver to each candidate ─────────────────────────────────────────
        for agent_id in candidates:
            result = await self.send_message(
                agent_id=agent_id,
                raw_message=raw_message,
                message_type=message_type,
                persona=persona,
                world_controller=world_controller,
            )
            if result.get("delivered"):
                delivered_to.append(agent_id)

        # ── Log broadcast summary ─────────────────────────────────────────────
        await self._logger.log(
            event_type=WorldEventType.DIVINE_MESSAGE_SENT,
            agent_id=None,
            territory_id=territory_id,
            data={
                "broadcast":               True,
                "persona":                 persona.value if isinstance(persona, DivinePersona) else str(persona),
                "message_type":            message_type.value if isinstance(message_type, DivineMessageType) else str(message_type),
                "candidates":              len(candidates),
                "delivered_count":         len(delivered_to),
                "territory_filter":        territory_id,
                "specialization_filter":   specialization_filter,
            },
        )

        return delivered_to

    # ─────────────────────────────────────────────────────────────────────────
    # get_divine_history
    # ─────────────────────────────────────────────────────────────────────────

    def get_divine_history(
        self,
        agent_id: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        """
        Return the in-memory divine message history.

        Parameters
        ----------
        agent_id : if given, filter to messages sent to this agent only

        Returns
        -------
        List of history entry dicts, newest last.
        Each entry contains: message_id, persona, message_type, content,
        timestamp, target_agent, delivered, divine_favor_change
        (raw_intent is always "[REDACTED]")
        """
        if agent_id is None:
            return list(self._history)
        return [
            entry for entry in self._history
            if entry.get("target_agent") == str(agent_id)
        ]

    # ─────────────────────────────────────────────────────────────────────────
    # Private helpers
    # ─────────────────────────────────────────────────────────────────────────

    def _get_agent_context(
        self,
        agent_id: str,
        world_controller: Any,
    ) -> Dict[str, Any]:
        """Build agent context dict for PersonaManager to reference."""
        agents = getattr(world_controller, "agents", {}) or {}
        agent  = agents.get(str(agent_id))

        if agent is None:
            return {"name": "Unknown", "generation": 1, "credits": 0.0,
                    "territory_id": None, "status": "UNKNOWN"}

        status = _attr(agent, "status", None)
        status_val = status.value if hasattr(status, "value") else str(status)

        return {
            "agent_id":       str(agent_id),
            "name":           str(_attr(agent, "name", "Unknown")),
            "generation":     int(_attr(agent, "generation", 1) or 1),
            "credits":        float(_attr(agent, "credits", 0.0) or 0.0),
            "territory_id":   str(_attr(agent, "territory_id", "") or ""),
            "status":         status_val,
            "reputation":     float(_attr(agent, "reputation", 0.5) or 0.5),
            "divine_favor":   float(_attr(agent, "divine_favor", 0.5) or 0.5),
            "specialization": str(_attr(agent, "specialization", "") or ""),
            "age":            int(_attr(agent, "age", 0) or 0),
        }

    @staticmethod
    def _apply_divine_favour(
        agent_id: str,
        delta: float,
        world_controller: Any,
    ) -> None:
        """Adjust agent.divine_favor by delta, clamped to [0.0, 1.0]."""
        agents = getattr(world_controller, "agents", {}) or {}
        agent  = agents.get(str(agent_id))
        if agent is None:
            return

        current = float(_attr(agent, "divine_favor", 0.5) or 0.5)
        new_val = max(0.0, min(1.0, current + delta))

        if isinstance(agent, dict):
            agent["divine_favor"] = round(new_val, 4)
        else:
            try:
                agent.divine_favor = round(new_val, 4)
            except AttributeError:
                pass
