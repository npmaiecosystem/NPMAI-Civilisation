"""
world/world_clock.py
====================
WorldClock — the heartbeat of the NPMAI Agentic World.

Drives the async event loop that advances the simulation one tick at a
time, triggers snapshots, elections, and world-level snapshots on schedule,
and provides pause/resume/run_n_ticks primitives for testing.

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 5 (world layer)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime, timezone
from typing import Any, Optional, TYPE_CHECKING

from config.constants import WORLD_CONSTANTS
from data.event_logger import EventLogger
from data.event_types import WorldEventType, WorldEvent

if TYPE_CHECKING:
    pass

logger = logging.getLogger("npmai_world.clock")

class WorldClock:
    """
    Async tick engine for the NPMAI Agentic World.

    Configuration (all from WORLD_CONSTANTS or constructor)
    ---------------------------------------------------------
    tick_duration_seconds : wall-clock seconds between ticks (default 10)
    ticks_per_day         : how many ticks = 1 experiment day (default 1440)
    snapshot_interval     : ticks between agent snapshots (default 100)
    election_interval     : ticks between territory elections (default 500)
    world_snapshot_interval: ticks between full world snapshots (default 1000)

    Usage
    -----
    clock = WorldClock(tick_duration_seconds=10.0)
    await clock.start(world_controller)    # runs forever

    # or for tests:
    await clock.run_n_ticks(50, world_controller)
    """

    def __init__(
        self,
        tick_duration_seconds: float = 10.0,
    ) -> None:
        # Time config
        self.tick_duration_seconds: float = tick_duration_seconds or float(
            WORLD_CONSTANTS.get("tick_duration_seconds", 10.0)
        )
        self._ticks_per_day: int = int(WORLD_CONSTANTS.get("ticks_per_day", 1440))
        self._snapshot_interval: int = int(WORLD_CONSTANTS.get("snapshot_interval_ticks", 100))
        self._election_interval: int = int(WORLD_CONSTANTS.get("election_interval_ticks", 500))
        self._world_snapshot_interval: int = int(WORLD_CONSTANTS.get("world_snapshot_interval_ticks", 1000))

        # State (never reset after start — tick_count is monotonically increasing)
        self.tick_count:        int   = 0
        self.experiment_day:    int   = 0
        self.world_age_hours:   float = 0.0
        self.is_running:        bool  = False

        # Control
        self._paused:      bool  = False
        self._stop_signal: bool  = False
        self._real_start:  Optional[float] = None   # wall-clock start time

        self._logger: Optional[EventLogger] = None

    async def _ensure_logger(self) -> EventLogger:
        if self._logger is None:
            self._logger = await EventLogger.get_instance()
        return self._logger

    # ── Main loop ─────────────────────────────────────────────────────────────

    async def start(self, world_controller: Any) -> None:
        """
        Infinite async tick loop. Call with asyncio.create_task() to run
        alongside other coroutines, or await directly in main().

        Graceful shutdown: call clock.stop() from another coroutine or via
        KeyboardInterrupt / SIGTERM handling in main.py.
        """
        self.is_running   = True
        self._stop_signal = False
        self._real_start  = time.monotonic()
        await self._ensure_logger()
        await self._logger.log(
            WorldEvent(
                event_type=WorldEventType.WORLD_INITIALIZED,
                agent_id=None,
                territory_id=None,
                data={
                    "tick_duration_seconds": self.tick_duration_seconds,
                    "ticks_per_day":         self._ticks_per_day,
                    "start_time":            datetime.now(timezone.utc).isoformat(),
                    },
                    )
                    )

        try:
            while not self._stop_signal:
                if self._paused:
                    await asyncio.sleep(0.5)
                    continue

                tick_start = asyncio.get_event_loop().time()

                await self._advance_tick(world_controller)

                # Adaptive sleep: subtract processing time so tick cadence
                # stays close to tick_duration_seconds even under load.
                elapsed = asyncio.get_event_loop().time() - tick_start
                sleep_for = max(0.0, self.tick_duration_seconds - elapsed)
                await asyncio.sleep(sleep_for)

        except asyncio.CancelledError:
            logger.info("WorldClock task cancelled gracefully at tick %d", self.tick_count)
        except Exception as exc:
            logger.exception("WorldClock crashed at tick %d: %s", self.tick_count, exc)
            raise
        finally:
            self.is_running = False
            await self._logger.log(
                WorldEvent(
                    event_type=WorldEventType.WORLD_STOPPED,
                    agent_id=None,
                    territory_id=None,
                    data={"final_tick": self.tick_count, "experiment_day": self.experiment_day},
                    )
            )

    async def _advance_tick(self, world_controller: Any) -> None:
        """Execute a single world tick and trigger scheduled events."""
        self.tick_count    += 1
        self.experiment_day = self.tick_count // self._ticks_per_day
        self.world_age_hours = (self.tick_count * self.tick_duration_seconds) / 3600.0

        tick = self.tick_count

        # ── Core world processing ─────────────────────────────────────────────
        try:
            await world_controller.process_tick(tick)
        except Exception as exc:
            logger.error("world_controller.process_tick(%d) raised: %s", tick, exc, exc_info=True)
            await self._logger.log(
                WorldEvent(
                    event_type=WorldEventType.SYSTEM_ERROR,
                    agent_id=None,
                    territory_id=None,
                    data={"tick": tick, "error": str(exc), "context": "process_tick"},
                    )
                    )

        # ── Periodic: agent snapshots ─────────────────────────────────────────
        if tick % self._snapshot_interval == 0:
            try:
                await world_controller.take_snapshots(tick)
            except Exception as exc:
                logger.warning("Snapshot at tick %d failed: %s", tick, exc)

        # ── Periodic: territory elections ─────────────────────────────────────
        if tick % self._election_interval == 0:
            try:
                await world_controller.run_elections(tick)
            except Exception as exc:
                logger.warning("Elections at tick %d failed: %s", tick, exc)

        # ── Periodic: full world snapshot ─────────────────────────────────────
        if tick % self._world_snapshot_interval == 0:
            try:
                await world_controller.take_world_snapshot(tick)
            except Exception as exc:
                logger.warning("World snapshot at tick %d failed: %s", tick, exc)

        # ── Log every N ticks to reduce Supabase pressure ────────────────────
        if tick % 50 == 0:
            await self._logger.log(
                WorldEvent(
                    event_type=WorldEventType.CLOCK_TICK,
                    agent_id=None,
                    territory_id=None,
                    data=self.get_time_info(),
                    )
                    )

    # ── Control ───────────────────────────────────────────────────────────────

    def pause(self) -> None:
        """Pause the clock. The current tick completes before pausing."""
        self._paused = True
        logger.info("WorldClock paused at tick %d", self.tick_count)

    def resume(self) -> None:
        """Resume a paused clock."""
        self._paused = False
        logger.info("WorldClock resumed from tick %d", self.tick_count)

    def stop(self) -> None:
        """Signal the clock loop to stop after the current tick."""
        self._stop_signal = True
        logger.info("WorldClock stop signal sent at tick %d", self.tick_count)

    # ── Testing helper ────────────────────────────────────────────────────────

    async def run_n_ticks(self, n: int, world_controller: Any) -> None:
        """
        Run exactly `n` ticks synchronously (no sleep between ticks).
        Useful for unit tests and integration tests without real-time delay.
        Scheduled events (snapshots, elections) still fire on their intervals.
        """
        self.is_running = True
        try:
            for _ in range(n):
                await self._advance_tick(world_controller)
        finally:
            self.is_running = False

    # ── Info ──────────────────────────────────────────────────────────────────

    def get_time_info(self) -> dict:
        """Return current clock state for the observatory and logs."""
        real_elapsed = time.monotonic() - self._real_start if self._real_start else 0.0
        return {
            "tick_count":            self.tick_count,
            "experiment_day":        self.experiment_day,
            "world_age_hours":       round(self.world_age_hours, 4),
            "tick_duration_seconds": self.tick_duration_seconds,
            "is_running":            self.is_running,
            "is_paused":             self._paused,
            "real_elapsed_seconds":  round(real_elapsed, 2),
            "next_snapshot_tick":    (
                self._snapshot_interval - (self.tick_count % self._snapshot_interval)
                if self._snapshot_interval else 0
            ),
            "next_election_tick": (
                self._election_interval - (self.tick_count % self._election_interval)
                if self._election_interval else 0
            ),
        }

    def __repr__(self) -> str:
        return (
            f"<WorldClock tick={self.tick_count} day={self.experiment_day} "
            f"running={self.is_running} paused={self._paused}>"
        )
