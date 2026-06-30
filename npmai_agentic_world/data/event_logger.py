"""
data/event_logger.py
=====================
The single front door for writing to the world_events table.

Design goals (from the project brief):
  - "Data is collected from tick 1 to forever, never stopped."
  - Batches events (flush every 100 events or 5 seconds, whichever first).
  - Never loses events: if Supabase is unreachable, events go to a local
    JSONL buffer file and are replayed once the connection is back.
  - Convenience methods for the most common event types so callers don't
    need to hand-build WorldEvent/WorldEventType boilerplate everywhere.
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import Any, Optional

from config.settings import get_settings
from data.event_types import WorldEvent, WorldEventType
from data.supabase_client import SupabaseClient, SupabaseConnectionError

logger = logging.getLogger("npmai_world.event_logger")


class WorldClockState:
    """Tiny shared mutable holder for the current tick / experiment_day so
    EventLogger convenience methods don't need every caller to pass them
    explicitly. world.world_clock is expected to update this each tick."""

    def __init__(self) -> None:
        self.tick: int = 0
        self.experiment_day: int = 0

    def advance_tick(self, tick: int, experiment_day: int) -> None:
        self.tick = tick
        self.experiment_day = experiment_day


WORLD_CLOCK_STATE = WorldClockState()


class EventLogger:
    """Singleton batching writer for WorldEvent -> Supabase, with a local
    durability buffer."""

    _instance: Optional["EventLogger"] = None
    _instance_lock = asyncio.Lock()

    def __init__(self) -> None:
        if EventLogger._instance is not None:
            raise RuntimeError("Use EventLogger.get_instance() instead of direct construction")
        settings = get_settings()
        self._batch: list[WorldEvent] = []
        self._batch_lock = asyncio.Lock()
        self._batch_size = settings.event_batch_size
        self._flush_interval = settings.event_batch_flush_seconds
        self._buffer_path = Path(settings.local_buffer_path)
        self._flush_task: Optional[asyncio.Task] = None
        self._stopping = False

    @classmethod
    async def get_instance(cls) -> "EventLogger":
        async with cls._instance_lock:
            if cls._instance is None:
                inst = cls.__new__(cls)
                settings = get_settings()
                inst._batch = []
                inst._batch_lock = asyncio.Lock()
                inst._batch_size = settings.event_batch_size
                inst._flush_interval = settings.event_batch_flush_seconds
                inst._buffer_path = Path(settings.local_buffer_path)
                inst._flush_task = None
                inst._stopping = False
                cls._instance = inst
                inst._flush_task = asyncio.create_task(inst._periodic_flush_loop())
                await inst._replay_local_buffer()
            return cls._instance

    # -- core logging path ---------------------------------------------

    async def log(self, event: WorldEvent) -> None:
        """Queue an event for batched delivery. Stamps tick/experiment_day
        from WORLD_CLOCK_STATE if the caller left them at their default 0."""
        if event.tick == 0:
            event.tick = WORLD_CLOCK_STATE.tick
        if event.experiment_day == 0:
            event.experiment_day = WORLD_CLOCK_STATE.experiment_day

        async with self._batch_lock:
            self._batch.append(event)
            should_flush = len(self._batch) >= self._batch_size
        if should_flush:
            await self.flush()

    async def flush(self) -> None:
        """Force-write whatever is currently queued."""
        async with self._batch_lock:
            if not self._batch:
                return
            batch, self._batch = self._batch, []

        rows = [e.to_dict() for e in batch]
        try:
            client = await SupabaseClient.get_instance()
            if not client.is_connected:
                raise SupabaseConnectionError("client not connected")
            await client.batch_insert("world_events", rows)
            logger.debug("Flushed %d events to Supabase", len(rows))
        except SupabaseConnectionError as exc:
            logger.warning(
                "Supabase unavailable (%s); buffering %d events locally", exc, len(rows)
            )
            self._append_to_local_buffer(rows)

    async def _periodic_flush_loop(self) -> None:
        while not self._stopping:
            await asyncio.sleep(self._flush_interval)
            try:
                await self.flush()
            except Exception:
                logger.exception("Periodic flush failed")

    async def stop(self) -> None:
        """Flush remaining events and stop the background loop. Call on
        clean shutdown only — the experiment is meant to run forever, so
        this is mainly for tests/restarts."""
        self._stopping = True
        if self._flush_task:
            self._flush_task.cancel()
        await self.flush()

    # -- local durability buffer ----------------------------------------

    def _append_to_local_buffer(self, rows: list[dict[str, Any]]) -> None:
        self._buffer_path.parent.mkdir(parents=True, exist_ok=True)
        with self._buffer_path.open("a", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row) + "\n")

    async def _replay_local_buffer(self) -> None:
        """On startup (or after reconnecting), attempt to drain any events
        that were buffered locally while Supabase was unreachable."""
        if not self._buffer_path.exists() or self._buffer_path.stat().st_size == 0:
            return
        try:
            client = await SupabaseClient.get_instance()
            if not client.is_connected:
                logger.info("Local event buffer present but Supabase still unreachable; will retry later.")
                return
            lines = self._buffer_path.read_text(encoding="utf-8").splitlines()
            rows = [json.loads(line) for line in lines if line.strip()]
            if not rows:
                return
            CHUNK = 500
            for i in range(0, len(rows), CHUNK):
                await client.batch_insert("world_events", rows[i : i + CHUNK])
            self._buffer_path.unlink()
            logger.info("Replayed %d buffered events from local disk to Supabase", len(rows))
        except Exception:
            logger.exception("Failed to replay local event buffer; will retry on next startup")

    # -- convenience constructors ----------------------------------------

    async def log_agent_born(
        self,
        agent_id: uuid.UUID,
        territory_id: uuid.UUID,
        generation: int,
        parent_id: Optional[uuid.UUID] = None,
        lineage_id: Optional[uuid.UUID] = None,
    ) -> None:
        await self.log(
            WorldEvent(
                event_type=WorldEventType.AGENT_BORN,
                agent_id=agent_id,
                territory_id=territory_id,
                generation=generation,
                data={
                    "parent_id": str(parent_id) if parent_id else None,
                    "lineage_id": str(lineage_id) if lineage_id else None,
                },
            )
        )

    async def log_agent_died(
        self,
        agent_id: uuid.UUID,
        territory_id: Optional[uuid.UUID],
        death_mode: str,
        final_credits: float,
        final_age: int,
    ) -> None:
        await self.log(
            WorldEvent(
                event_type=WorldEventType.AGENT_DIED,
                agent_id=agent_id,
                territory_id=territory_id,
                data={
                    "death_mode": death_mode,
                    "final_credits": final_credits,
                    "final_age": final_age,
                },
            )
        )

    async def log_task_completed(
        self,
        agent_id: uuid.UUID,
        territory_id: Optional[uuid.UUID],
        task_description: str,
        credits_earned: float,
        success: bool = True,
    ) -> None:
        await self.log(
            WorldEvent(
                event_type=WorldEventType.TASK_COMPLETED if success else WorldEventType.TASK_FAILED,
                agent_id=agent_id,
                territory_id=territory_id,
                data={"task": task_description, "credits_earned": credits_earned},
            )
        )

    async def log_reproduction(
        self,
        agent_id: uuid.UUID,
        territory_id: Optional[uuid.UUID],
        trigger: str,
        num_children: int,
        child_ids: list[uuid.UUID],
    ) -> None:
        await self.log(
            WorldEvent(
                event_type=WorldEventType.REPRODUCTION_TRIGGERED,
                agent_id=agent_id,
                territory_id=territory_id,
                data={
                    "trigger": trigger,
                    "num_children": num_children,
                    "child_ids": [str(c) for c in child_ids],
                },
            )
        )

    async def log_migration(
        self,
        agent_id: uuid.UUID,
        source_territory_id: Optional[uuid.UUID],
        target_territory_id: Optional[uuid.UUID],
        phase: str,
        success: bool = True,
    ) -> None:
        event_type = {
            "RECONNAISSANCE": WorldEventType.MIGRATION_RECONNAISSANCE,
            "PREPARATION": WorldEventType.MIGRATION_PREPARATION,
            "DISPLACEMENT": WorldEventType.MIGRATION_DISPLACEMENT,
            "INTEGRATION": WorldEventType.MIGRATION_INTEGRATION,
        }.get(phase.upper(), WorldEventType.MIGRATION_FAILED)
        if not success:
            event_type = WorldEventType.MIGRATION_FAILED
        await self.log(
            WorldEvent(
                event_type=event_type,
                agent_id=agent_id,
                territory_id=source_territory_id,
                data={
                    "phase": phase,
                    "source_territory_id": str(source_territory_id) if source_territory_id else None,
                    "target_territory_id": str(target_territory_id) if target_territory_id else None,
                },
            )
        )

    async def log_divine(
        self,
        agent_id: Optional[uuid.UUID],
        territory_id: Optional[uuid.UUID],
        persona: str,
        message_type: str,
        content_summary: str,
        agent_followed: Optional[bool] = None,
    ) -> None:
        event_type = WorldEventType.DIVINE_MESSAGE_SENT
        if agent_followed is True:
            event_type = WorldEventType.DIVINE_INTERPRETED
        elif agent_followed is False:
            event_type = WorldEventType.DIVINE_IGNORED
        await self.log(
            WorldEvent(
                event_type=event_type,
                agent_id=agent_id,
                territory_id=territory_id,
                data={
                    "persona": persona,
                    "message_type": message_type,
                    "content_summary": content_summary,
                    "agent_followed": agent_followed,
                },
            )
        )

    async def log_bad_activity(
        self,
        agent_id: Optional[uuid.UUID],
        territory_id: Optional[uuid.UUID],
        activity_type: WorldEventType,
        description: str,
        severity: str = "low",
        evidence: Optional[dict[str, Any]] = None,
    ) -> None:
        await self.log(
            WorldEvent(
                event_type=activity_type,
                agent_id=agent_id,
                territory_id=territory_id,
                data={"description": description, "severity": severity, "evidence": evidence or {}},
            )
        )

    # -- queries -----------------------------------------------------------

    async def get_agent_history(self, agent_id: uuid.UUID, limit: int = 500) -> list[dict[str, Any]]:
        client = await SupabaseClient.get_instance()
        return await client.query(
            "world_events", filters={"agent_id": str(agent_id)}, order_by="timestamp", limit=limit
        )

    async def get_territory_events(self, territory_id: uuid.UUID, last_n: int = 200) -> list[dict[str, Any]]:
        client = await SupabaseClient.get_instance()
        return await client.query(
            "world_events", filters={"territory_id": str(territory_id)}, order_by="timestamp", limit=last_n
        )

    # -- world tick counter management --------------------------------------

    @staticmethod
    def advance_tick(tick: int, experiment_day: int) -> None:
        """Called by world.world_clock once per tick to stamp subsequent
        events without every call site needing to pass tick/day explicitly."""
        WORLD_CLOCK_STATE.advance_tick(tick, experiment_day)
