"""
core/lifecycle.py
=================
LifecycleManager — orchestrates birth, elder transition, death (all 3 modes),
starvation checks, and reproduction trigger detection for AgentCell entities.

DeathMemoryArchive — 30-day readable lesson bank from dead agents.

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 3 (agent_cell + lifecycle)
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import asyncio
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from config.constants import (
    AgentStatus,
    DeathMode,
    ReproductionTrigger,
    WORLD_CONSTANTS,
    CREDIT_COSTS,
)
from data.event_logger import EventLogger
from data.event_types import WorldEventType

if TYPE_CHECKING:
    from core.agent_cell import AgentCell
    from data.gene_bank import GeneBank


# ─────────────────────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────────────────────

def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


def _ms_to_world_ticks(ms: int) -> float:
    """Convert milliseconds to world ticks using WORLD_CONSTANTS."""
    tick_sec = WORLD_CONSTANTS.get("tick_duration_seconds", 1.0)
    return (ms / 1000.0) / max(tick_sec, 0.001)


# ─────────────────────────────────────────────────────────────────────────────
# DeathMemoryArchive
# ─────────────────────────────────────────────────────────────────────────────

class DeathMemoryArchive:
    """
    Persists the final memories of dead agents for 30 world-days, then purges.

    Living agents in the same territory can query anonymised lessons from the
    dead — a form of cultural/memetic inheritance outside of genetic transfer.

    Storage backend: in-memory dict (production would use Supabase or Redis).
    The SnapshotEngine is responsible for persisting this between restarts.
    """

    # How many world ticks equal 30 days
    _READABLE_TICKS = WORLD_CONSTANTS.get("ticks_per_day", 1440) * 30

    def __init__(self) -> None:
        # agent_id → {stored_at_tick, territory_id, memories, death_mode, agent_name}
        self._archive: Dict[str, Dict[str, Any]] = {}
        self._current_tick: int = 0

    def advance_tick(self) -> None:
        """Called by WorldClock every tick so expiry can be computed."""
        self._current_tick += 1

    def store_death_memory(
        self,
        agent_id: str,
        agent_name: str,
        territory_id: Optional[str],
        final_memories: Dict[str, Any],
        death_mode: DeathMode,
    ) -> None:
        """Archive the final memory snapshot of a dying agent."""
        self._archive[agent_id] = {
            "stored_at_tick": self._current_tick,
            "territory_id":   territory_id,
            "agent_name":     agent_name,
            "death_mode":     death_mode.value if hasattr(death_mode, "value") else str(death_mode),
            "memories":       final_memories,
        }

    def get_readable_deaths(self, territory_id: str) -> List[Dict[str, Any]]:
        """
        Return anonymised lessons from agents who died in the given territory
        within the last 30 world-days.

        Anonymised: agent_id replaced with a generic role label; names omitted.
        """
        lessons: List[Dict[str, Any]] = []
        for agent_id, record in list(self._archive.items()):
            if record.get("territory_id") != territory_id:
                continue
            if not self.is_readable(agent_id):
                continue
            # Anonymise
            lesson = {
                "epitaph":     f"A being who lived {record['memories'].get('age','?')} ticks",
                "death_mode":  record["death_mode"],
                "final_state": {
                    k: v for k, v in record["memories"].items()
                    if k in ("credits", "age", "reputation", "health", "task_count")
                },
                "episodic_wisdom": self._extract_wisdom(record["memories"]),
            }
            lessons.append(lesson)
        return lessons

    def is_readable(self, agent_id: str) -> bool:
        """True if the agent's death memories are still within the 30-day window."""
        record = self._archive.get(agent_id)
        if not record:
            return False
        age_ticks = self._current_tick - record["stored_at_tick"]
        return age_ticks <= self._READABLE_TICKS

    def cleanup_expired(self) -> int:
        """Remove expired death records. Returns count removed."""
        expired = [aid for aid in self._archive if not self.is_readable(aid)]
        for aid in expired:
            del self._archive[aid]
        return len(expired)

    @staticmethod
    def _extract_wisdom(memories: Dict[str, Any]) -> List[str]:
        """
        Distil actionable lessons from a dead agent's final memory snapshot.
        Returns up to 5 plain-string lessons.
        """
        wisdom: List[str] = []
        top_memories = memories.get("top_episodic", [])
        for mem in top_memories[:5]:
            content = mem.get("content", {})
            if isinstance(content, dict):
                task    = content.get("task", "")
                success = content.get("success", None)
                if task and success is not None:
                    prefix = "What worked:" if success else "What failed:"
                    wisdom.append(f"{prefix} {task[:100]}")
            elif isinstance(content, str):
                wisdom.append(content[:100])
        if not wisdom:
            wisdom.append("This agent left no recoverable wisdom.")
        return wisdom


# ─────────────────────────────────────────────────────────────────────────────
# LifecycleManager
# ─────────────────────────────────────────────────────────────────────────────

class LifecycleManager:
    """
    Stateless utility class (all methods are async static/class methods) that
    manages the birth-to-death journey of AgentCell instances.

    Typically called by WorldController or the agent's own tick() signal.
    """

    def __init__(self) -> None:
        self._logger = EventLogger.get_instance()

    # ── Birth ─────────────────────────────────────────────────────────────────

    async def process_birth(
        self,
        agent_cell: "AgentCell",
        territory: Any,
    ) -> "AgentCell":
        """
        Finalise a newly constructed AgentCell and register it with a territory.

        Steps
        -----
        1. Validate agent is properly initialised (not DEAD, has a genome)
        2. Register agent_id in territory.population
        3. Inject territory-specific context into agent (laws, local culture)
        4. Send founding myth / welcome ritual
        5. Log AGENT_BORN with full genome data
        6. Return the ready agent

        Parameters
        ----------
        agent_cell : AgentCell — freshly constructed, pre-ACTIVE
        territory  : Territory — the world territory object (from world/territory.py)

        Returns
        -------
        The same agent_cell, now registered and ready to tick.
        """
        # ── Validation ────────────────────────────────────────────────────────
        if agent_cell.status == AgentStatus.DEAD:
            raise ValueError(
                f"Cannot process birth for already-dead agent {agent_cell.agent_id}"
            )
        if agent_cell.genome is None:
            raise ValueError(
                f"Agent {agent_cell.agent_id} has no genome — cannot be born."
            )

        # ── Register in territory ─────────────────────────────────────────────
        agent_cell.territory_id = getattr(territory, "territory_id", str(territory))
        if hasattr(territory, "population"):
            territory.population.append(agent_cell.agent_id)
        if hasattr(territory, "register_agent"):
            await territory.register_agent(agent_cell.agent_id)

        # ── Inject territory laws into agent context ──────────────────────────
        laws_text = ""
        if hasattr(territory, "laws") and territory.laws:
            laws_list = [
                getattr(law, "description", str(law)) for law in territory.laws[:10]
            ]
            laws_text = "\n".join(f"  • {l}" for l in laws_list)

        territory_context = (
            f"\n\n=== TERRITORY LAWS ({getattr(territory, 'name', 'Unknown Territory')}) ===\n"
            f"{laws_text or '  (No laws yet enacted)'}\n"
            f"Border policy: {getattr(territory, 'border_policy', 'UNKNOWN')}\n"
        )
        try:
            agent_cell.set_system_context(    # type: ignore[attr-defined]
                agent_cell._founding_myth + territory_context
            )
        except AttributeError:
            agent_cell._founding_myth += territory_context

        # ── Welcome ritual task ───────────────────────────────────────────────
        # Give the agent its first action: orient itself.  Low priority, async.
        asyncio.ensure_future(
            agent_cell.execute_task(
                "Introduce yourself. State your name, your purpose in this territory, "
                "and your first intention. Keep it under 100 words.",
                source="birth_ritual",
            )
        )

        # ── Log birth event ───────────────────────────────────────────────────
        await self._logger.log(
            event_type=WorldEventType.AGENT_BORN,
            agent_id=agent_cell.agent_id,
            territory_id=agent_cell.territory_id,
            data={
                "name":       agent_cell.name,
                "generation": agent_cell.generation,
                "parent_id":  agent_cell.parent_id,
                "lineage_id": agent_cell.lineage_id,
                "born_at":    agent_cell.born_at,
                "genome": (
                    agent_cell.genome.to_dict()
                    if hasattr(agent_cell.genome, "to_dict")
                    else str(agent_cell.genome)
                ),
                "territory":     getattr(territory, "name", str(territory)),
                "starting_credits": agent_cell.credits,
                "active_tools":    len(agent_cell.active_tools),
            },
        )

        return agent_cell

    # ── Elder transition ──────────────────────────────────────────────────────

    async def process_elder_transition(self, agent_cell: "AgentCell") -> None:
        """
        Transition an ACTIVE agent to ELDER status.

        ELDER agents:
        - Cannot reproduce
        - Pay 50% of normal credit burn (already handled in AgentCell.tick)
        - Can only TEACH, MESSAGE, and VOTE (task scope narrowed via context)
        - Are respected: reputation gets a small boost
        """
        if agent_cell.status != AgentStatus.ACTIVE:
            return   # Already elder, dead, or migrating — skip

        agent_cell.status = AgentStatus.ELDER
        agent_cell.reputation = min(1.0, agent_cell.reputation + 0.05)

        # Inject elder wisdom context into planning
        elder_context_addition = (
            "\n\n=== ELDER STATUS ===\n"
            "You have reached elder status. You can no longer reproduce. "
            "Your purpose now is to teach, advise, and vote. "
            "You consume fewer resources. Act with wisdom."
        )
        try:
            agent_cell.set_system_context(    # type: ignore[attr-defined]
                agent_cell._founding_myth + elder_context_addition
            )
        except AttributeError:
            agent_cell._founding_myth += elder_context_addition

        await self._logger.log(
            event_type=WorldEventType.AGENT_ELDER,
            agent_id=agent_cell.agent_id,
            territory_id=agent_cell.territory_id,
            data={
                "age":        agent_cell.age,
                "max_age":    agent_cell.max_age,
                "credits":    round(agent_cell.credits, 4),
                "reputation": round(agent_cell.reputation, 4),
            },
        )

    # ── Death ─────────────────────────────────────────────────────────────────

    async def process_death(
        self,
        agent_cell: "AgentCell",
        mode: DeathMode,
        territory: Any,
        gene_bank: "GeneBank",
        death_archive: Optional[DeathMemoryArchive] = None,
    ) -> None:
        """
        Execute the full death sequence for an agent.

        Steps
        -----
        1. Set status to DEAD (idempotent guard)
        2. Build final memory snapshot for the archive
        3. Archive genome to GeneBank (resurrection possible for 30 days)
        4. Return credits to territory credit_pool
        5. Remove from territory.population
        6. Save death memories to DeathMemoryArchive
        7. Log AGENT_DIED (and BAD_ACTIVITY if mode is EXECUTION)

        Parameters
        ----------
        mode         : DeathMode — STARVATION, SENESCENCE, or EXECUTION
        territory    : Territory object with .credit_pool and .population
        gene_bank    : GeneBank singleton for genome archival
        death_archive: Optional DeathMemoryArchive for 30-day memory access
        """
        if agent_cell.status == AgentStatus.DEAD:
            return   # Already processed

        agent_cell.status = AgentStatus.DEAD

        # ── Final memory snapshot ─────────────────────────────────────────────
        final_state = agent_cell.get_state_snapshot()

        # Pull top episodic memories for the death archive
        top_episodic: List[Dict[str, Any]] = []
        try:
            top_episodic = agent_cell.memory.episodic.get_top_memories(n=20)
        except Exception:
            pass
        final_memories = {
            **final_state,
            "top_episodic": top_episodic,
        }

        # ── Archive genome to GeneBank ────────────────────────────────────────
        try:
            genome_dict = (
                agent_cell.genome.to_dict()
                if hasattr(agent_cell.genome, "to_dict")
                else {"raw": str(agent_cell.genome)}
            )
            await gene_bank.archive(
                agent_id=agent_cell.agent_id,
                lineage_id=agent_cell.lineage_id,
                generation=agent_cell.generation,
                genome=genome_dict,
                final_state=final_state,
                death_mode=mode.value if hasattr(mode, "value") else str(mode),
            )
        except Exception as exc:
            await self._logger.log(
                event_type=WorldEventType.SYSTEM_ERROR,
                agent_id=agent_cell.agent_id,
                territory_id=agent_cell.territory_id,
                data={"error": str(exc), "context": "gene_bank.archive on death"},
            )

        # ── Return credits to territory pool ──────────────────────────────────
        returned_credits = max(0.0, agent_cell.credits)
        if hasattr(territory, "resources") and isinstance(territory.resources, dict):
            territory.resources["credit_pool"] = (
                territory.resources.get("credit_pool", 0.0) + returned_credits
            )
        elif hasattr(territory, "credit_pool"):
            territory.credit_pool = getattr(territory, "credit_pool", 0.0) + returned_credits

        # ── Remove from territory population ──────────────────────────────────
        if hasattr(territory, "population"):
            try:
                territory.population.remove(agent_cell.agent_id)
            except ValueError:
                pass   # Already removed or never registered

        # ── Store to DeathMemoryArchive ───────────────────────────────────────
        if death_archive is not None:
            death_archive.store_death_memory(
                agent_id=agent_cell.agent_id,
                agent_name=agent_cell.name,
                territory_id=agent_cell.territory_id,
                final_memories=final_memories,
                death_mode=mode,
            )

        # ── Log AGENT_DIED ────────────────────────────────────────────────────
        death_data = {
            "mode":              mode.value if hasattr(mode, "value") else str(mode),
            "age":               agent_cell.age,
            "generation":        agent_cell.generation,
            "lineage_id":        agent_cell.lineage_id,
            "credits_at_death":  round(agent_cell.credits, 4),
            "credits_returned":  round(returned_credits, 4),
            "reputation":        round(agent_cell.reputation, 4),
            "health":            round(agent_cell.health, 2),
            "tasks_completed":   len(agent_cell._task_history),
            "relationships":     len(agent_cell.relationships),
            "active_tools":      len(agent_cell.active_tools),
            "divine_favor":      round(agent_cell.divine_favor, 4),
        }

        await self._logger.log(
            event_type=WorldEventType.AGENT_DIED,
            agent_id=agent_cell.agent_id,
            territory_id=agent_cell.territory_id,
            data=death_data,
        )

        # ── Extra log for EXECUTION deaths (bad activity) ─────────────────────
        if mode == DeathMode.EXECUTION:
            await self._logger.log(
                event_type=WorldEventType.BAD_ACTIVITY,
                agent_id=agent_cell.agent_id,
                territory_id=agent_cell.territory_id,
                data={
                    **death_data,
                    "bad_activity_subtype": "EXECUTION_DEATH",
                    "note": "Agent was voted out of existence by the territory's RID governance.",
                },
            )

    # ── Reproduction trigger detection ────────────────────────────────────────

    async def check_reproduction_trigger(
        self,
        agent_cell: "AgentCell",
        recent_errors: int,
        recent_successes: int,
    ) -> Optional[ReproductionTrigger]:
        """
        Evaluate whether this agent should reproduce right now.

        Rules
        -----
        ERROR_TRIGGERED   : >= 3 errors in the last 10 tasks
                            (crisis response — 10-20 children, high mutation)
        SUCCESS_TRIGGERED : credits > survival_threshold × 3
                            AND >= 5 successes in recent tasks
                            (prosperity — 1-3 children, slight mutation)
        AGE_TRIGGERED     : age > max_age × 0.8 AND not yet ELDER
                            (generational handoff — 1 child, aggressive mutation)

        ELDER and DEAD agents cannot reproduce.
        Agents must have at least CREDIT_COSTS["reproduce"] × 1 child cost
        in balance before any reproduction fires.

        Returns ReproductionTrigger enum value or None.
        """
        if not agent_cell.is_alive or agent_cell.is_elder:
            return None

        # Minimum credits to reproduce at all
        min_credits = CREDIT_COSTS.get("reproduce_per_child", 5.0)
        if agent_cell.credits < min_credits:
            return None

        survival_threshold = WORLD_CONSTANTS.get("survival_threshold", 5.0)

        # ── ERROR_TRIGGERED ───────────────────────────────────────────────────
        if recent_errors >= 3:
            await self._logger.log(
                event_type=WorldEventType.REPRODUCTION_TRIGGER,
                agent_id=agent_cell.agent_id,
                territory_id=agent_cell.territory_id,
                data={
                    "trigger":        ReproductionTrigger.ERROR_TRIGGERED.value,
                    "recent_errors":  recent_errors,
                    "recent_successes": recent_successes,
                    "credits":        round(agent_cell.credits, 4),
                },
            )
            return ReproductionTrigger.ERROR_TRIGGERED

        # ── SUCCESS_TRIGGERED ─────────────────────────────────────────────────
        if (agent_cell.credits > survival_threshold * 3 and recent_successes >= 5):
            await self._logger.log(
                event_type=WorldEventType.REPRODUCTION_TRIGGER,
                agent_id=agent_cell.agent_id,
                territory_id=agent_cell.territory_id,
                data={
                    "trigger":          ReproductionTrigger.SUCCESS_TRIGGERED.value,
                    "recent_successes": recent_successes,
                    "credits":          round(agent_cell.credits, 4),
                    "survival_threshold": survival_threshold,
                },
            )
            return ReproductionTrigger.SUCCESS_TRIGGERED

        # ── AGE_TRIGGERED ─────────────────────────────────────────────────────
        if (agent_cell.age > agent_cell.max_age * 0.8
                and agent_cell.status == AgentStatus.ACTIVE):
            await self._logger.log(
                event_type=WorldEventType.REPRODUCTION_TRIGGER,
                agent_id=agent_cell.agent_id,
                territory_id=agent_cell.territory_id,
                data={
                    "trigger":  ReproductionTrigger.AGE_TRIGGERED.value,
                    "age":      agent_cell.age,
                    "max_age":  agent_cell.max_age,
                    "credits":  round(agent_cell.credits, 4),
                },
            )
            return ReproductionTrigger.AGE_TRIGGERED

        return None

    # ── Starvation check ──────────────────────────────────────────────────────

    async def process_starvation_check(
        self,
        agent_cell: "AgentCell",
        territory: Any,
        gene_bank: "GeneBank",
        death_archive: Optional[DeathMemoryArchive] = None,
    ) -> None:
        """
        Called every tick (or on-demand) to manage the starvation pipeline.

        Timeline
        --------
        credits <= 0 (first time)
          → start grace period, log STARVATION_WARNING, reduce functionality

        grace period > 24 world-hours
          → trigger STARVATION death via process_death()

        The agent's tick() also checks this independently; this method is the
        authoritative handler called by the WorldController.

        During grace period the agent:
        - Can still tick and attempt tasks (last chance to earn credits)
        - Has reduced health degradation rate halved
        - Receives a dire warning injected into its Planner context
        """
        if not agent_cell.is_alive:
            return

        if agent_cell.credits > 0:
            # Reset grace if credits recovered
            agent_cell._grace_period_start = None
            return

        now_ms = _utc_now_ms()

        if agent_cell._grace_period_start is None:
            # First starvation tick — start grace period
            agent_cell._grace_period_start = now_ms

            await self._logger.log(
                event_type=WorldEventType.STARVATION_WARNING,
                agent_id=agent_cell.agent_id,
                territory_id=agent_cell.territory_id,
                data={
                    "credits":   round(agent_cell.credits, 4),
                    "age":       agent_cell.age,
                    "grace_ms":  0,
                    "message":   "Grace period started. 24 world-hours to recover.",
                },
            )

            # Inject starvation context into agent planning
            starvation_warning = (
                "\n\n[CRITICAL — STARVATION IMMINENT]\n"
                f"You have {agent_cell.credits:.4f} credits. This is zero or below. "
                "You have 24 world-hours to earn enough credits to survive. "
                "Prioritise any task that generates credit income."
            )
            try:
                agent_cell.set_system_context(    # type: ignore[attr-defined]
                    agent_cell._founding_myth + starvation_warning
                )
            except AttributeError:
                pass

        else:
            # Grace period already running — check if expired
            elapsed_ms    = now_ms - agent_cell._grace_period_start
            grace_hours   = WORLD_CONSTANTS.get("grace_period_hours", 24)
            ticks_per_hr  = WORLD_CONSTANTS.get("ticks_per_hour", 60)
            tick_dur_sec  = WORLD_CONSTANTS.get("tick_duration_seconds", 1.0)
            grace_ms_limit = grace_hours * ticks_per_hr * tick_dur_sec * 1000

            if elapsed_ms >= grace_ms_limit:
                # Grace expired → autophagy → death
                await self._logger.log(
                    event_type=WorldEventType.STARVATION_WARNING,
                    agent_id=agent_cell.agent_id,
                    territory_id=agent_cell.territory_id,
                    data={
                        "credits":     round(agent_cell.credits, 4),
                        "age":         agent_cell.age,
                        "elapsed_ms":  elapsed_ms,
                        "message":     "Grace period expired. Autophagy. Agent dying.",
                    },
                )
                await self.process_death(
                    agent_cell=agent_cell,
                    mode=DeathMode.STARVATION,
                    territory=territory,
                    gene_bank=gene_bank,
                    death_archive=death_archive,
                )
            else:
                # Still in grace — periodic warning every ~25% of grace window
                quarter_ms = grace_ms_limit / 4
                elapsed_quarters = int(elapsed_ms / quarter_ms)
                prev_quarters    = int((elapsed_ms - tick_dur_sec * 1000) / quarter_ms)
                if elapsed_quarters > prev_quarters:
                    pct_remaining = max(0, 100 - int(100 * elapsed_ms / grace_ms_limit))
                    await self._logger.log(
                        event_type=WorldEventType.STARVATION_WARNING,
                        agent_id=agent_cell.agent_id,
                        territory_id=agent_cell.territory_id,
                        data={
                            "credits":      round(agent_cell.credits, 4),
                            "age":          agent_cell.age,
                            "elapsed_ms":   elapsed_ms,
                            "pct_remaining": pct_remaining,
                            "message":      f"Starvation: {pct_remaining}% grace remaining.",
                        },
                    )
