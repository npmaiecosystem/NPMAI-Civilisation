"""
divine/message_broker.py
========================
MessageBroker — low-level delivery layer that routes divine messages
from the Oracle to individual AgentCell instances via the WorldController.

The broker does not know about personas or divine framing —
it only knows about agent lookup and method dispatch.

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 6 (divine layer)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Dict, Optional

logger = logging.getLogger("npmai_world.divine.broker")


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


class MessageBroker:
    """
    Routes a fully-formed divine message dict to a specific agent.

    Delivery Protocol
    -----------------
    1. Look up `agent_id` in `world_controller.agents`
    2. Verify agent is alive (status != DEAD)
    3. Call `agent.receive_divine_message(divine_message)`
    4. Return True on success, False on failure

    The broker is intentionally thin — all business logic lives in Oracle.

    Usage
    -----
    broker = MessageBroker()
    success = await broker.deliver(
        agent_id="abc123",
        divine_message={
            "persona": "THE_ARCHITECT",
            "message_type": "COMMANDMENT",
            "content": "...",
            "timestamp": ...,
        },
        world_controller=wc,
    )
    """

    async def deliver(
        self,
        agent_id: str,
        divine_message: Dict[str, Any],
        world_controller: Any,
    ) -> bool:
        """
        Deliver a divine message to a specific agent.

        Parameters
        ----------
        agent_id       : target agent's ID
        divine_message : fully-formed message dict (built by Oracle)
        world_controller : WorldController instance holding self.agents

        Returns
        -------
        True  — message delivered successfully
        False — agent not found, dead, or receive_divine_message raised
        """
        # ── Validate world controller ─────────────────────────────────────────
        if world_controller is None:
            logger.error("MessageBroker.deliver: world_controller is None")
            return False

        agents = getattr(world_controller, "agents", None)
        if agents is None:
            logger.error("MessageBroker.deliver: world_controller has no .agents dict")
            return False

        # ── Find agent ────────────────────────────────────────────────────────
        agent = agents.get(str(agent_id))
        if agent is None:
            logger.warning(
                "MessageBroker: agent %s not found in world (total agents=%d)",
                agent_id[:8], len(agents),
            )
            return False

        # ── Check agent is alive ──────────────────────────────────────────────
        status = getattr(agent, "status", None)
        status_val = status.value if hasattr(status, "value") else str(status)
        if status_val == "DEAD":
            logger.info(
                "MessageBroker: agent %s is DEAD — divine message not delivered",
                agent_id[:8],
            )
            return False

        # ── Deliver via receive_divine_message ────────────────────────────────
        receive_fn = getattr(agent, "receive_divine_message", None)
        if receive_fn is None:
            logger.error(
                "MessageBroker: agent %s has no receive_divine_message() method",
                agent_id[:8],
            )
            return False

        try:
            if hasattr(receive_fn, "__await__") or hasattr(receive_fn, "__call__"):
                import asyncio
                if asyncio.iscoroutinefunction(receive_fn):
                    await receive_fn(divine_message)
                else:
                    receive_fn(divine_message)
            logger.debug(
                "MessageBroker: delivered %s message to agent %s",
                divine_message.get("message_type", "UNKNOWN"),
                agent_id[:8],
            )
            return True
        except Exception as exc:
            logger.error(
                "MessageBroker: delivery to agent %s raised %s: %s",
                agent_id[:8], type(exc).__name__, exc,
            )
            return False

    async def deliver_to_territory(
        self,
        territory_id: str,
        divine_message: Dict[str, Any],
        world_controller: Any,
        specialization_filter: Optional[str] = None,
    ) -> Dict[str, bool]:
        """
        Deliver a divine message to all alive agents in a territory.

        Parameters
        ----------
        territory_id          : target territory
        divine_message        : fully-formed message dict
        world_controller      : WorldController instance
        specialization_filter : if set, only agents whose specialization
                                contains this string receive the message

        Returns
        -------
        dict mapping agent_id → delivery_success
        """
        results: Dict[str, bool] = {}

        agents = getattr(world_controller, "agents", {}) or {}

        for agent_id, agent in agents.items():
            # ── Territory filter ──────────────────────────────────────────────
            agent_territory = getattr(agent, "territory_id", None)
            if str(agent_territory) != str(territory_id):
                continue

            # ── Status filter ─────────────────────────────────────────────────
            status = getattr(agent, "status", None)
            status_val = status.value if hasattr(status, "value") else str(status)
            if status_val == "DEAD":
                continue

            # ── Specialization filter ─────────────────────────────────────────
            if specialization_filter:
                spec = getattr(agent, "specialization", "") or ""
                if specialization_filter.lower() not in spec.lower():
                    continue

            # ── Deliver ───────────────────────────────────────────────────────
            success = await self.deliver(
                agent_id=str(agent_id),
                divine_message=divine_message,
                world_controller=world_controller,
            )
            results[str(agent_id)] = success

        return results

