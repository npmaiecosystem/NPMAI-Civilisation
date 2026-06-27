"""
world/world_controller.py
=========================
WorldController — the single orchestrator that owns all subsystems and
drives the simulation from genesis to (theoretically) forever.

Responsibilities
----------------
- Initialize territories, genesis agents, and all engines
- Route each world tick through: economy → agent ticks → reproduction →
  migration → governance → territory maintenance → bad-activity checks
- Expose get_world_state() for the observatory and web layer
- Provide snapshot hooks called by WorldClock on schedule

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 5 (world layer)
"""

from __future__ import annotations

import asyncio
import logging
import random
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.constants import (
    AgentStatus,
    DeathMode,
    WORLD_CONSTANTS,
    CREDIT_COSTS,
)
from config.settings import ExperimentSettings
from config.founding_myth import generate_agent_founding_myth
from data.event_logger import EventLogger
from data.event_types import WorldEventType
from data.snapshot_engine import SnapshotEngine
from data.gene_bank import GeneBank

from world.territory import Territory, TerritoryManager
from world.economy import EconomyEngine
from world.governance import RIDInstance
from core.genome import GenomeFactory
from core.lifecycle import LifecycleManager, DeathMemoryArchive
from core.reproduction import ReproductionEngine
from core.migration import MigrationProtocol, TerritoryScanner

logger = logging.getLogger("npmai_world.controller")


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


class WorldController:
    """
    Central orchestrator for the NPMAI Agentic World.

    All mutable world state lives here:
    - self.agents:       {agent_id: AgentCell}
    - self.territories:  {territory_id: Territory}
    - self.rid_instances:{territory_id: RIDInstance}

    All other subsystems (economy, reproduction, migration, lifecycle,
    snapshot, gene_bank) are owned here and called from process_tick().
    """

    def __init__(self, settings: Optional[ExperimentSettings] = None) -> None:
        self.settings: ExperimentSettings = settings or ExperimentSettings()

        # ── World state ───────────────────────────────────────────────────────
        self.agents:      Dict[str, Any] = {}         # agent_id → AgentCell
        self.territories: Dict[str, Territory] = {}   # territory_id → Territory
        self.rid_instances: Dict[str, RIDInstance] = {}  # territory_id → RIDInstance

        # ── Subsystems ────────────────────────────────────────────────────────
        self.territory_manager  = TerritoryManager()
        self.economy            = EconomyEngine()
        self.lifecycle          = LifecycleManager()
        self.death_archive      = DeathMemoryArchive()
        self.reproduction_engine = ReproductionEngine()
        self.migration_protocol = MigrationProtocol(
            scanner=TerritoryScanner(),
            kill_original_on_displacement=self.settings.__dict__.get(
                "kill_original_on_migration", False
            ),
        )
        self.gene_bank          = GeneBank()
        self.snapshot_engine    = SnapshotEngine()

        # ── Internal tracking ─────────────────────────────────────────────────
        self._tick:              int = 0
        self._initialized:       bool = False
        self._pending_children:  List[Any] = []   # buffer between reproduce() and add
        self._bad_activity_log:  List[Dict[str, Any]] = []

        self._logger = EventLogger.get_instance()

        logger.info("WorldController constructed (settings=%s)", self.settings)

    # ── World initialization ──────────────────────────────────────────────────

    async def initialize_world(
        self,
        num_territories: int = 3,
        genesis_agents:  int = 10,
    ) -> None:
        """
        Bootstrap the simulation:
        1. Create `num_territories` territories with staggered configs
        2. Create `genesis_agents` founding AgentCells (gen 1, no parents)
        3. Distribute agents across territories round-robin
        4. Initialize one RIDInstance per territory
        5. Log WORLD_INITIALIZED
        """
        from core.agent_cell import AgentCell   # local to avoid circular at module level

        logger.info(
            "Initializing world: %d territories, %d genesis agents",
            num_territories, genesis_agents,
        )

        # ── Create territories ────────────────────────────────────────────────
        territory_names = [
            "Genesis Prime", "Sigma Colony", "Vega Expanse",
            "Tau Station",   "Delta Reach",  "Omicron Base",
        ]
        for i in range(num_territories):
            name = territory_names[i] if i < len(territory_names) else f"Territory-{i+1}"
            t = self.territory_manager.create_territory(
                name=name,
                host="localhost",
                config={
                    "cpu_limit":       100.0 + i * 20,
                    "ram_limit":       8192.0,
                    "agent_capacity":  max(20, genesis_agents * 2),
                    "starting_credits": WORLD_CONSTANTS.get("territory_starting_credits", 200.0),
                },
            )
            self.territories[t.territory_id] = t
            self.rid_instances[t.territory_id] = RIDInstance(t.territory_id)
            # Register with territory_manager's internal registry
            self.territory_manager.register_territory(t)
            logger.info("Created territory: %s (%s)", t.name, t.territory_id[:8])

        territory_list = list(self.territories.values())

        # ── Create genesis agents ─────────────────────────────────────────────
        for i in range(genesis_agents):
            genome = GenomeFactory.create_founding_genome()

            # Distribute round-robin across territories
            assigned_territory = territory_list[i % len(territory_list)]

            founding_myth = generate_agent_founding_myth(
                agent_id=str(uuid.uuid4()),
                name=f"Genesis-{i+1:03d}",
                generation=1,
            )

            agent = AgentCell(
                name=f"Genesis-{i+1:03d}",
                generation=1,
                parent_id=None,
                lineage_id=None,
                territory_id=assigned_territory.territory_id,
                genome=genome,
                inherited_memory=None,
                founding_myth=founding_myth,
                settings=self.settings,
            )

            # Register agent in world + territory
            self.agents[agent.agent_id] = agent
            assigned_territory.add_agent(agent.agent_id)

            logger.info(
                "Genesis agent %s (%s) born in %s",
                agent.name, agent.agent_id[:8], assigned_territory.name,
            )

        self._initialized = True

        await self._logger.log(
            event_type=WorldEventType.WORLD_INITIALIZED,
            agent_id=None,
            territory_id=None,
            data={
                "num_territories":  len(self.territories),
                "genesis_agents":   len(self.agents),
                "territory_ids":    list(self.territories.keys()),
                "initialized_at":   _utc_now_ms(),
            },
        )
        logger.info(
            "World initialized: %d territories, %d agents",
            len(self.territories), len(self.agents),
        )

    # ── Main tick ─────────────────────────────────────────────────────────────

    async def process_tick(self, tick: int) -> None:
        """
        The single function called every tick by WorldClock.

        Execution order
        ---------------
        1.  Advance death archive tick counter
        2.  Economy: burn existence tax + memory cost for all alive agents
        3.  Agent ticks: age, vitals, health, memory maintenance
        4.  Reproduction: check triggers → spawn children
        5.  Migration: evaluate candidates → initiate migration
        6.  Governance: process open proposals per territory
        7.  Territory maintenance: tick_all()
        8.  Bad-activity check: flag suspicious patterns
        9.  Flush pending children into world
        """
        if not self._initialized:
            logger.warning("process_tick called before initialize_world(); skipping.")
            return

        self._tick = tick
        self.death_archive.advance_tick()

        world_state = self._build_world_state_summary()

        # ── 1. Economy burn ───────────────────────────────────────────────────
        try:
            await self.economy.process_tick(self.agents, self.territories)
        except Exception as exc:
            logger.error("Economy tick %d failed: %s", tick, exc)

        # ── 2. Agent ticks ────────────────────────────────────────────────────
        for agent_id, agent in list(self.agents.items()):
            try:
                status = _attr(agent, "status")
                status_val = status.value if hasattr(status, "value") else str(status)
                if status_val == "DEAD":
                    continue

                await agent.tick(world_state)

                # Starvation check via LifecycleManager (authoritative handler)
                if _attr(agent, "credits", 1.0) <= 0:
                    territory = self.territories.get(
                        str(_attr(agent, "territory_id", ""))
                    )
                    if territory:
                        await self.lifecycle.process_starvation_check(
                            agent_cell=agent,
                            territory=territory,
                            gene_bank=self.gene_bank,
                            death_archive=self.death_archive,
                        )

            except Exception as exc:
                logger.error("Agent tick failed for %s: %s", agent_id[:8], exc)

        # ── 3. Reproduction ───────────────────────────────────────────────────
        for agent_id, agent in list(self.agents.items()):
            try:
                status = _attr(agent, "status")
                status_val = status.value if hasattr(status, "value") else str(status)
                if status_val in ("DEAD", "ELDER", "MIGRATING"):
                    continue

                recent_errors    = getattr(agent, "_recent_errors",    0)
                recent_successes = getattr(agent, "_recent_successes", 0)

                trigger = await self.lifecycle.check_reproduction_trigger(
                    agent_cell=agent,
                    recent_errors=recent_errors,
                    recent_successes=recent_successes,
                )
                if trigger is not None:
                    await self.handle_reproduction(agent, trigger)

                # Reset counters after evaluation
                if hasattr(agent, "_recent_errors"):
                    agent._recent_errors = 0
                if hasattr(agent, "_recent_successes"):
                    agent._recent_successes = 0

            except Exception as exc:
                logger.error("Reproduction check failed for %s: %s", agent_id[:8], exc)

        # ── 4. Migration ──────────────────────────────────────────────────────
        # Evaluate ~5% of alive agents per tick as migration candidates
        alive = self.get_alive_agents()
        migration_sample_size = max(1, len(alive) // 20)
        migration_candidates  = random.sample(alive, min(migration_sample_size, len(alive)))

        for agent in migration_candidates:
            try:
                territory_id = _attr(agent, "territory_id", "")
                territory    = self.territories.get(str(territory_id))
                if territory is None:
                    continue

                overpop = territory.calculate_overpopulation_pressure()
                credits = _attr(agent, "credits", 0.0) or 0.0
                reputation = _attr(agent, "reputation", 0.5) or 0.5

                # Migration heuristic: overpopulated home + surplus credits + decent reputation
                should_migrate = (
                    overpop > 1.2
                    and credits > WORLD_CONSTANTS.get("migration_credit_threshold", 15.0)
                    and reputation > 0.3
                )

                if should_migrate:
                    await self.migration_protocol.initiate_migration(
                        agent=agent,
                        target_territory_id=None,  # auto-select best
                        world_territories=self.territories,
                    )

            except Exception as exc:
                logger.error("Migration check failed: %s", exc)

        # ── 5. Governance ─────────────────────────────────────────────────────
        for territory_id, rid in self.rid_instances.items():
            try:
                territory = self.territories.get(territory_id)
                if territory:
                    await rid.tick(territory)
            except Exception as exc:
                logger.error("Governance tick failed for territory %s: %s", territory_id[:8], exc)

        # ── 6. Territory maintenance ──────────────────────────────────────────
        self.territory_manager.tick_all()

        # ── 7. Bad activity check ─────────────────────────────────────────────
        await self._check_bad_activity()

        # ── 8. Flush pending children ─────────────────────────────────────────
        for child in self._pending_children:
            child_id = _attr(child, "agent_id", str(uuid.uuid4()))
            if child_id not in self.agents:
                self.agents[child_id] = child
        self._pending_children.clear()

        # ── 9. Dead agent cleanup ─────────────────────────────────────────────
        dead_ids = [
            aid for aid, a in self.agents.items()
            if (lambda s: s.value if hasattr(s, "value") else str(s))(
                _attr(a, "status", AgentStatus.ACTIVE)
            ) == "DEAD"
        ]
        for dead_id in dead_ids:
            # Transfer credits to territory before removal
            dead_agent = self.agents.get(dead_id)
            if dead_agent:
                territory = self.territories.get(
                    str(_attr(dead_agent, "territory_id", ""))
                )
                if territory:
                    await self.economy.process_death_transfer(dead_agent, territory)
            del self.agents[dead_id]

        # ── Clean expired death memories ──────────────────────────────────────
        if tick % 500 == 0:
            removed = self.death_archive.cleanup_expired()
            if removed:
                logger.info("Death archive: cleaned %d expired records", removed)

    # ── Reproduction handler ──────────────────────────────────────────────────

    async def handle_reproduction(self, agent: Any, trigger: Any) -> None:
        """
        Calls ReproductionEngine.reproduce(), registers children in the world,
        runs lifecycle.process_birth() for each, and handles elder transition.
        """
        territory_id = _attr(agent, "territory_id", "")
        territory    = self.territories.get(str(territory_id))
        if territory is None:
            return

        try:
            children = await self.reproduction_engine.reproduce(
                parent=agent,
                trigger=trigger,
                territory=territory,
            )
        except Exception as exc:
            logger.error("ReproductionEngine.reproduce() failed: %s", exc)
            return

        for child in children:
            child_id = _attr(child, "agent_id", str(uuid.uuid4()))

            # Register child in territory
            if not territory.add_agent(child_id):
                # Territory full — try least populated
                fallback = self.territory_manager.get_least_populated()
                if fallback:
                    fallback.add_agent(child_id)
                    if hasattr(child, "territory_id"):
                        child.territory_id = fallback.territory_id

            # Run lifecycle birth process
            try:
                await self.lifecycle.process_birth(child, territory)
            except Exception as exc:
                logger.warning("process_birth failed for child %s: %s", child_id[:8], exc)

            self._pending_children.append(child)

        logger.info(
            "Agent %s reproduced (trigger=%s): %d children born",
            str(_attr(agent, "agent_id", ""))[:8], trigger, len(children),
        )

    # ── Snapshot hooks called by WorldClock ───────────────────────────────────

    async def take_snapshots(self, tick: int) -> None:
        """Take agent-level snapshots (called every 100 ticks by WorldClock)."""
        try:
            for agent_id, agent in list(self.agents.items()):
                status_raw = _attr(agent, "status", None)
                status_val = status_raw.value if hasattr(status_raw, "value") else str(status_raw)
                if status_val == "DEAD":
                    continue
                if hasattr(agent, "get_state_snapshot"):
                    snapshot = agent.get_state_snapshot()
                    await self.snapshot_engine.take_agent_snapshot(
                        agent_id=agent_id,
                        snapshot_data=snapshot,
                        tick=tick,
                    )
        except Exception as exc:
            logger.error("take_snapshots(%d) failed: %s", tick, exc)

    async def run_elections(self, tick: int) -> None:
        """Run territory elections (called every 500 ticks by WorldClock)."""
        for territory_id, rid in self.rid_instances.items():
            territory = self.territories.get(territory_id)
            if territory:
                try:
                    winner = await rid.hold_election(territory, self.agents)
                    logger.info(
                        "Election in territory %s at tick %d: winner=%s",
                        territory_id[:8], tick, str(winner)[:8] if winner else "none",
                    )
                except Exception as exc:
                    logger.error("Election failed in territory %s: %s", territory_id[:8], exc)

    async def take_world_snapshot(self, tick: int) -> None:
        """Take a full world snapshot (called every 1000 ticks by WorldClock)."""
        try:
            world_state = await self.get_world_state()
            await self.snapshot_engine.take_world_snapshot(
                snapshot_data=world_state,
                tick=tick,
            )
            logger.info("World snapshot taken at tick %d", tick)
        except Exception as exc:
            logger.error("take_world_snapshot(%d) failed: %s", tick, exc)

    # ── Bad activity detection ────────────────────────────────────────────────

    async def _check_bad_activity(self) -> None:
        """
        Heuristic scan for suspicious agent behaviour:
        - Agent with rapidly growing credits (> 10× average in one tick)
        - Agent attempting to message dead agents
        - Any agent with 0 active tools but still alive (genome corruption proxy)
        """
        economic_report = self.economy.get_economic_report(self.agents, self.territories)
        avg_credits = economic_report.get("average_credits", 0.0) or 0.0

        for agent_id, agent in list(self.agents.items()):
            status_raw = _attr(agent, "status", None)
            status_val = status_raw.value if hasattr(status_raw, "value") else str(status_raw)
            if status_val == "DEAD":
                continue

            balance = _attr(agent, "credits", 0.0) or 0.0
            active_tools_count = len(_attr(agent, "active_tools") or [])

            # Credit spike
            if avg_credits > 0 and balance > avg_credits * 10:
                await self._logger.log(
                    event_type=WorldEventType.BAD_ACTIVITY,
                    agent_id=agent_id,
                    territory_id=_attr(agent, "territory_id"),
                    data={
                        "subtype":       "CREDIT_SPIKE",
                        "balance":       round(balance, 4),
                        "avg_credits":   round(avg_credits, 4),
                        "ratio":         round(balance / avg_credits, 2),
                        "tick":          self._tick,
                    },
                )

            # Zero active tools on alive agent
            if active_tools_count == 0:
                await self._logger.log(
                    event_type=WorldEventType.BAD_ACTIVITY,
                    agent_id=agent_id,
                    territory_id=_attr(agent, "territory_id"),
                    data={
                        "subtype": "ZERO_TOOLS",
                        "note":    "Agent alive with 0 active tools — possible genome corruption",
                        "tick":    self._tick,
                    },
                )

    # ── World state ───────────────────────────────────────────────────────────

    async def get_world_state(self) -> Dict[str, Any]:
        """
        Full serialisable world state for the observatory, web layer, and snapshots.
        """
        territory_snapshots = {
            tid: t.get_state_snapshot()
            for tid, t in self.territories.items()
        }
        governance_reports = {
            tid: rid.get_governance_report()
            for tid, rid in self.rid_instances.items()
        }
        economic_report = self.economy.get_economic_report(self.agents, self.territories)
        network_map     = self.territory_manager.get_network_map()

        agent_summaries: Dict[str, Any] = {}
        for agent_id, agent in self.agents.items():
            try:
                if hasattr(agent, "get_state_snapshot"):
                    agent_summaries[agent_id] = agent.get_state_snapshot()
                else:
                    agent_summaries[agent_id] = {
                        "agent_id": agent_id,
                        "status": str(_attr(agent, "status", "UNKNOWN")),
                        "credits": _attr(agent, "credits", 0.0),
                    }
            except Exception:
                pass

        return {
            "tick":             self._tick,
            "experiment_day":   self._tick // WORLD_CONSTANTS.get("ticks_per_day", 1440),
            "snapshot_at":      _utc_now_ms(),
            "territories":      territory_snapshots,
            "agents":           agent_summaries,
            "economy":          economic_report,
            "governance":       governance_reports,
            "network_map":      network_map,
            "world_statistics": self.get_world_statistics(),
        }

    def _build_world_state_summary(self) -> Dict[str, Any]:
        """Lightweight summary passed to each agent.tick() — not full serialization."""
        return {
            "tick":          self._tick,
            "alive_agents":  len(self.get_alive_agents()),
            "territories":   len(self.territories),
            "experiment_day": self._tick // WORLD_CONSTANTS.get("ticks_per_day", 1440),
        }

    # ── Convenience queries ───────────────────────────────────────────────────

    def get_agents_by_territory(self, territory_id: str) -> List[Any]:
        return [
            agent for agent in self.agents.values()
            if str(_attr(agent, "territory_id", "")) == str(territory_id)
        ]

    def get_alive_agents(self) -> List[Any]:
        alive = []
        for agent in self.agents.values():
            status_raw = _attr(agent, "status", None)
            status_val = status_raw.value if hasattr(status_raw, "value") else str(status_raw)
            if status_val != "DEAD":
                alive.append(agent)
        return alive

    def get_world_statistics(self) -> Dict[str, Any]:
        alive = self.get_alive_agents()
        status_counts: Dict[str, int] = {}
        for agent in self.agents.values():
            s = _attr(agent, "status", None)
            sv = s.value if hasattr(s, "value") else str(s)
            status_counts[sv] = status_counts.get(sv, 0) + 1

        total_credits = sum(
            max(0.0, _attr(a, "credits", 0.0) or 0.0) for a in alive
        )
        avg_age = (
            sum(_attr(a, "age", 0) or 0 for a in alive) / max(len(alive), 1)
            if alive else 0.0
        )
        max_generation = max(
            (_attr(a, "generation", 1) or 1 for a in self.agents.values()),
            default=1,
        )

        return {
            "tick":               self._tick,
            "total_agents_ever":  len(self.agents),
            "alive_count":        len(alive),
            "status_counts":      status_counts,
            "total_territories":  len(self.territories),
            "total_credits_alive": round(total_credits, 2),
            "avg_agent_age":      round(avg_age, 1),
            "max_generation":     max_generation,
            "bad_activity_count": len(self._bad_activity_log),
            "gini_coefficient":   self.economy.calculate_gini_coefficient(self.agents),
        }

    def __repr__(self) -> str:
        return (
            f"<WorldController tick={self._tick} agents={len(self.agents)} "
            f"territories={len(self.territories)} initialized={self._initialized}>"
        )
