"""
data/snapshot_engine.py
========================
Periodic point-in-time snapshots of agents, territories, and the world as
a whole, distinct from the continuous WorldEvent stream — events capture
*what happened*, snapshots capture *the full state at a moment*, which is
what timeline/replay/diff tooling in observatory/ and web/ actually wants
to render.

Cadence (from WORLD_CONSTANTS, overridable via ExperimentSettings):
  - agent snapshot      every 100 ticks
  - territory snapshot  every 100 ticks
  - world snapshot      every 1000 ticks

Callers (typically world/world_controller.py's tick loop) are expected to
call the `take_*` methods every tick; the cadence check happens inside
this module so the controller doesn't need to track its own counters.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from config.settings import get_settings
from data.supabase_client import SupabaseClient

logger = logging.getLogger("npmai_world.snapshot_engine")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _extract_state(obj: Any, fields: list[str]) -> dict[str, Any]:
    """Best-effort extraction of named attributes from an arbitrary object
    (AgentCell / Territory / WorldController instances) into a plain dict,
    so this module doesn't need a hard import-time dependency on those
    classes (avoids circular imports with core/ and world/)."""
    out: dict[str, Any] = {}
    for name in fields:
        if hasattr(obj, name):
            out[name] = getattr(obj, name)
    return out


class SnapshotEngine:
    """Facade over agent_states / territory_states tables plus an in-memory
    'world snapshot' representation (world state is summarized, not given
    its own table, since it's fully derivable from the other tables — but
    we still materialize and log it for fast timeline reads)."""

    AGENT_TABLE = "agent_states"
    TERRITORY_TABLE = "territory_states"

    def __init__(self) -> None:
        settings = get_settings()
        self._agent_interval = settings.snapshot_agent_interval_ticks
        self._territory_interval = settings.snapshot_territory_interval_ticks
        self._world_interval = settings.snapshot_world_interval_ticks

    def should_snapshot_agent(self, tick: int) -> bool:
        return self._agent_interval > 0 and tick % self._agent_interval == 0

    def should_snapshot_territory(self, tick: int) -> bool:
        return self._territory_interval > 0 and tick % self._territory_interval == 0

    def should_snapshot_world(self, tick: int) -> bool:
        return self._world_interval > 0 and tick % self._world_interval == 0

    # -- agent --------------------------------------------------------------

    async def take_agent_snapshot(self, agent_cell: Any, tick: int, force: bool = False) -> Optional[uuid.UUID]:
        """Snapshot an AgentCell. No-ops (returns None) unless `force` or
        the tick falls on the configured cadence, so callers can call this
        unconditionally every tick without extra branching."""
        if not force and not self.should_snapshot_agent(tick):
            return None

        snapshot_id = uuid.uuid4()
        state_fields = [
            "agent_id", "name", "generation", "parent_id", "lineage_id",
            "territory_id", "status", "credits", "age", "health",
            "max_age", "reputation", "divine_favor", "relationships",
            "genome", "memory_summary",
        ]
        state = _extract_state(agent_cell, state_fields)

        row = {
            "snapshot_id": str(snapshot_id),
            "agent_id": str(getattr(agent_cell, "agent_id", state.get("agent_id"))),
            "territory_id": str(state["territory_id"]) if state.get("territory_id") else None,
            "captured_at": _now_iso(),
            "tick": tick,
            "generation": state.get("generation", 0),
            "lineage_id": str(state["lineage_id"]) if state.get("lineage_id") else None,
            "status": str(state.get("status", "ACTIVE")),
            "credits": float(state.get("credits", 0.0)),
            "age": int(state.get("age", 0)),
            "health": float(state.get("health", 1.0)),
            "reputation": float(state.get("reputation", 0.0)),
            "divine_favor": float(state.get("divine_favor", 0.0)),
            "state": state,
        }
        client = await SupabaseClient.get_instance()
        await client.insert(self.AGENT_TABLE, row)
        return snapshot_id

    # -- territory ------------------------------------------------------

    async def take_territory_snapshot(self, territory: Any, tick: int, force: bool = False) -> Optional[uuid.UUID]:
        if not force and not self.should_snapshot_territory(tick):
            return None

        snapshot_id = uuid.uuid4()
        state_fields = [
            "territory_id", "name", "host", "resources", "population",
            "laws", "border_policy",
        ]
        state = _extract_state(territory, state_fields)
        population = state.get("population") or []

        row = {
            "snapshot_id": str(snapshot_id),
            "territory_id": str(state.get("territory_id")),
            "captured_at": _now_iso(),
            "tick": tick,
            "name": str(state.get("name", "")),
            "host": str(state.get("host", "")) if state.get("host") else None,
            "population_count": len(population),
            "border_policy": str(state.get("border_policy", "OPEN")),
            "resources": state.get("resources") or {},
            "state": state,
        }
        client = await SupabaseClient.get_instance()
        await client.insert(self.TERRITORY_TABLE, row)
        return snapshot_id

    # -- world ------------------------------------------------------------

    async def take_world_snapshot(self, world_controller: Any, tick: int, force: bool = False) -> Optional[dict[str, Any]]:
        """World snapshots are coarser and cheaper: rather than a dedicated
        table, we aggregate the most recent agent/territory snapshots plus
        whatever summary stats the controller exposes, and log it as a
        single composite event-shaped record via the event logger so it's
        still queryable on the unified world_events timeline.
        """
        if not force and not self.should_snapshot_world(tick):
            return None

        from data.event_logger import EventLogger
        from data.event_types import WorldEvent, WorldEventType

        summary_fields = [
            "total_agents", "total_territories", "total_credits_in_circulation",
            "births_this_period", "deaths_this_period", "experiment_day",
        ]
        summary = _extract_state(world_controller, summary_fields)
        summary["tick"] = tick

        event = WorldEvent(
            event_type=WorldEventType.AGENT_STATUS_CHANGED,  # generic carrier; see data field for real payload
            agent_id=None,
            territory_id=None,
            tick=tick,
            experiment_day=int(summary.get("experiment_day", 0)),
            data={"snapshot_kind": "WORLD_SNAPSHOT", **summary},
        )
        logger_instance = await EventLogger.get_instance()
        await logger_instance.log(event)
        logger.info("World snapshot taken at tick %d: %s", tick, summary)
        return summary

    # -- timelines & diffing -------------------------------------------

    async def get_agent_timeline(self, agent_id: uuid.UUID, limit: int = 1000) -> list[dict[str, Any]]:
        client = await SupabaseClient.get_instance()
        return await client.query(
            self.AGENT_TABLE, filters={"agent_id": str(agent_id)}, order_by="tick", descending=False, limit=limit
        )

    async def get_world_timeline(self, limit: int = 1000) -> list[dict[str, Any]]:
        """World snapshots are stored as world_events with
        data.snapshot_kind == 'WORLD_SNAPSHOT'; filter client-side since
        querying a nested JSONB key isn't part of the generic query() helper.
        """
        client = await SupabaseClient.get_instance()
        rows = await client.query("world_events", order_by="tick", descending=False, limit=limit * 5)
        timeline = [r for r in rows if (r.get("data") or {}).get("snapshot_kind") == "WORLD_SNAPSHOT"]
        return timeline[:limit]

    @staticmethod
    def diff_snapshots(snap1: dict[str, Any], snap2: dict[str, Any]) -> dict[str, Any]:
        """Shallow-diff two snapshot rows' `state` dict, returning only the
        keys whose values changed. Works for agent_states and
        territory_states rows alike since both carry a `state` field.

        Returns a dict of {field: {"before": ..., "after": ...}}.
        """
        state1 = snap1.get("state") or {}
        state2 = snap2.get("state") or {}
        all_keys = set(state1) | set(state2)
        diff: dict[str, Any] = {}
        for key in all_keys:
            before = state1.get(key)
            after = state2.get(key)
            if before != after:
                diff[key] = {"before": before, "after": after}
        return diff
