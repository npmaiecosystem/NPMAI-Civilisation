"""
core/reproduction.py
=====================
ReproductionEngine: the only place that turns "an agent should reproduce"
into "N new AgentCell instances exist in the world."

=== Assumed interfaces from Sessions 2-3 ===
This module is written against the following contract for objects it does
not itself define. If your actual core/agent_cell.py / core/lifecycle.py
differ in a field or method name, that's the only place to reconcile —
everything below is otherwise self-contained.

AgentCell (core/agent_cell.py, extends npmai_agents.AgentBrain):
    .agent_id: uuid.UUID
    .name: str
    .generation: int
    .parent_id: Optional[uuid.UUID]
    .lineage_id: uuid.UUID
    .born_at: datetime
    .genome: core.genome.Genome
    .memory: core.memory_system.AgentMemorySystem
    .credits: float
    .age: int
    .health: float
    .status: config.constants.AgentStatus
    .max_age: int
    .territory_id: Optional[uuid.UUID]
    .relationships: dict[str, float]
    .reputation: float
    .divine_favor: float
    Constructor:
        AgentCell(genome=..., territory_id=..., generation=...,
                  parent_id=None, lineage_id=None, name=None, credits=0.0)
    Optional method (used for the reproduction viability smoke test; if
    absent we fall back to a structural-only check):
        async .run_planning_smoke_test(task: str) -> bool
            Invokes the Planner role ONLY (no Tool Manager / Coder /
            Auditor / subprocess execution) on a trivial task and reports
            whether it produced a parseable plan.

core/lifecycle.LifecycleManager:
    async .transition_to_elder(agent: AgentCell) -> None

core/memory_system.AgentMemorySystem:
    .prepare_inheritance_package() -> dict
    .receive_inheritance(package: dict) -> None

world/territory.Territory (duck-typed; dict or object both supported):
    .territory_id, .population (list of agent_ids), .resources (dict)
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import logging
import random
import uuid
from dataclasses import dataclass
from typing import Any, Optional

from config.constants import *  # noqa: F401,F403  (AgentStatus, ReproductionTrigger, CREDIT_COSTS, WORLD_CONSTANTS, MUTATION_RATES)
from config.settings import ExperimentSettings
from data.event_logger import EventLogger
from data.event_types import WorldEvent, WorldEventType

from core.genome import GenomeFactory, Genome

logger = logging.getLogger("npmai_world.reproduction")


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` whether it's a real object, a dict, or
    something dict-like — keeps this module resilient to small interface
    drift in Territory/AgentCell across sessions."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


@dataclass
class MutationIntensity:
    prompt_rate: float
    capability_rate: float
    parameter_rate: float


class ReproductionEngine:
    """Orchestrates the full reproduction lifecycle: validation, child
    count, mutation intensity, genome + memory inheritance, viability
    gating, diversity adjustment, credit accounting, and event logging.
    """

    # -- public entry point ------------------------------------------------

    async def reproduce(
        self,
        parent: Any,  # AgentCell
        trigger: "ReproductionTrigger",  # noqa: F821
        territory: Any,
        partner: Optional[Any] = None,  # AgentCell
    ) -> list[Any]:
        """Orchestrate full reproduction for `parent`. Never raises on
        ordinary failure modes (insufficient credits, invalid genome,
        elder/dead parent) — instead logs the failure and returns []. This
        is deliberate: reproduction happens inside the world tick loop,
        where one agent's bad luck must not crash the simulation."""
        event_logger = await EventLogger.get_instance()
        territory_id = _attr(territory, "territory_id")

        validation_error = self._validate_parent(parent)
        if validation_error:
            logger.info("Reproduction blocked for agent %s: %s", _attr(parent, "agent_id"), validation_error)
            await event_logger.log(
                WorldEvent(
                    event_type=WorldEventType.REPRODUCTION_TRIGGERED,
                    agent_id=_attr(parent, "agent_id"),
                    territory_id=territory_id,
                    generation=_attr(parent, "generation", 0),
                    data={"trigger": trigger.value, "success": False, "reason": validation_error},
                )
            )
            return []

        num_children = self._num_children_for_trigger(trigger)
        intensity = self._mutation_intensity_for_trigger(trigger)
        reproduce_cost = abs(CREDIT_COSTS["reproduce_per_child"])
        starting_credits_each = self.calculate_child_starting_credits(parent, trigger, num_children)

        children: list[Any] = []
        for child_index in range(num_children):
            if _attr(parent, "credits", 0.0) < reproduce_cost:
                logger.info(
                    "Agent %s exhausted credits after producing %d/%d children",
                    _attr(parent, "agent_id"), len(children), num_children,
                )
                break

            child_genome = GenomeFactory.create_child_genome(
                parent=_attr(parent, "genome"),
                partner=_attr(partner, "genome") if partner is not None else None,
                trigger=trigger,
                generation=_attr(parent, "generation", 0) + 1,
                prompt_rate_override=intensity.prompt_rate,
                capability_rate_override=intensity.capability_rate,
                parameter_rate_override=intensity.parameter_rate,
            )

            inheritance_package = self._safe_prepare_inheritance(parent)
            child = await self._spawn_agent_cell(
                child_genome=child_genome,
                inheritance_package=inheritance_package,
                parent=parent,
                territory=territory,
                starting_credits=starting_credits_each,
            )

            viable = await self._run_viability_check(child)
            if not viable:
                logger.info("Child of agent %s failed viability check (child %d/%d)", _attr(parent, "agent_id"), child_index + 1, num_children)
                await event_logger.log(
                    WorldEvent(
                        event_type=WorldEventType.CHILD_GENOME_CREATED,
                        agent_id=_attr(parent, "agent_id"),
                        territory_id=territory_id,
                        generation=_attr(parent, "generation", 0) + 1,
                        data={"viable": False, "child_index": child_index, "trigger": trigger.value},
                    )
                )
                continue

            # Charge the parent only for children that actually survive
            # the viability gate — non-viable attempts are free in credits
            # (the genome material is simply discarded).
            self._deduct_credits(parent, reproduce_cost)
            children.append(child)

            await event_logger.log(
                WorldEvent(
                    event_type=WorldEventType.CHILD_GENOME_CREATED,
                    agent_id=_attr(parent, "agent_id"),
                    territory_id=territory_id,
                    generation=_attr(parent, "generation", 0) + 1,
                    data={"viable": True, "child_id": str(_attr(child, "agent_id")), "trigger": trigger.value},
                )
            )
            await event_logger.log_agent_born(
                agent_id=_attr(child, "agent_id"),
                territory_id=territory_id,
                generation=_attr(child, "generation", 0),
                parent_id=_attr(parent, "agent_id"),
                lineage_id=_attr(child, "lineage_id"),
            )

        existing_population = _attr(territory, "population", []) or []
        children = self._prepare_diversity_check(children, existing_population)

        await event_logger.log_reproduction(
            agent_id=_attr(parent, "agent_id"),
            territory_id=territory_id,
            trigger=trigger.value,
            num_children=len(children),
            child_ids=[_attr(c, "agent_id") for c in children],
        )

        if trigger == ReproductionTrigger.AGE:
            await self._transition_to_elder(parent)

        return children

    # -- validation ----------------------------------------------------

    @staticmethod
    def _validate_parent(parent: Any) -> Optional[str]:
        """Return None if `parent` is eligible to reproduce, else a short
        human-readable reason string."""
        status = _attr(parent, "status")
        if status == AgentStatus.DEAD:
            return "parent is dead"
        if status == AgentStatus.ELDER:
            return "parent is already an elder"
        if status == AgentStatus.MIGRATING:
            return "parent is mid-migration"
        credits = _attr(parent, "credits", 0.0)
        if credits is None or credits < abs(CREDIT_COSTS["reproduce_per_child"]):
            return "parent does not have enough credits to reproduce"
        genome = _attr(parent, "genome")
        if genome is None:
            return "parent has no genome"
        return None

    # -- child count / mutation intensity --------------------------------

    @staticmethod
    def _num_children_for_trigger(trigger: "ReproductionTrigger") -> int:  # noqa: F821
        value = trigger.value if hasattr(trigger, "value") else str(trigger)
        if value == "ERROR":
            return random.randint(10, 20)
        if value == "SUCCESS":
            return random.randint(1, 3)
        if value == "AGE":
            return 1
        # Unknown trigger: treat conservatively as a single, gently-mutated child.
        logger.warning("Unrecognized reproduction trigger %r; defaulting to 1 child", trigger)
        return 1

    @staticmethod
    def _mutation_intensity_for_trigger(trigger: "ReproductionTrigger") -> MutationIntensity:  # noqa: F821
        """Exact rates specified for this session's ReproductionEngine
        (intentionally distinct from — and takes precedence over —
        GenomeFactory's own internal crisis/default table, via the
        override parameters added to create_child_genome)."""
        value = trigger.value if hasattr(trigger, "value") else str(trigger)
        if value == "ERROR":
            return MutationIntensity(prompt_rate=MUTATION_RATES["PROMPT_crisis"], capability_rate=0.15, parameter_rate=0.25)
        if value == "SUCCESS":
            return MutationIntensity(prompt_rate=MUTATION_RATES["PROMPT_default"], capability_rate=0.05, parameter_rate=0.10)
        if value == "AGE":
            # "aggressive (all rates x2)" -- read relative to the SUCCESS
            # baseline, since AGE is generational turnover rather than crisis.
            return MutationIntensity(
                prompt_rate=min(MUTATION_RATES["PROMPT_default"] * 2, 1.0),
                capability_rate=0.10,
                parameter_rate=0.20,
            )
        return MutationIntensity(
            prompt_rate=MUTATION_RATES["PROMPT_default"],
            capability_rate=MUTATION_RATES["CAPABILITY_default"],
            parameter_rate=MUTATION_RATES["PARAMETER_default"],
        )

    # -- viability ----------------------------------------------------------

    async def _run_viability_check(self, child: Any) -> bool:
        """Structural validity (always run) plus an optional live planning
        smoke test if the AgentCell implementation exposes one.

        Returns True iff all checks pass.
        """
        genome: Optional[Genome] = _attr(child, "genome")
        if genome is None:
            return False

        if genome.capability_chromosome.count_active() < MUTATION_RATES["min_tools_after_mutation"]:
            return False

        if not genome.prompt_dna.exons and not genome.prompt_dna.introns:
            return False  # totally empty prompt DNA can never express a personality

        bounds = genome.parameter_genes._BOUNDS  # noqa: SLF001 - read-only introspection of own bounds
        for name, (lo, hi) in bounds.items():
            value = getattr(genome.parameter_genes, name)
            if value is None or value < lo or value > hi:
                return False

        smoke_test = getattr(child, "run_planning_smoke_test", None)
        if callable(smoke_test):
            try:
                ok = await smoke_test("Say hello and confirm you can plan a simple task.")
                return bool(ok)
            except Exception:  # noqa: BLE001
                logger.exception("Planning smoke test raised an exception; treating as non-viable")
                return False
        else:
            logger.debug(
                "AgentCell has no run_planning_smoke_test(); falling back to structural-only viability check"
            )
            return True

    # -- diversity -----------------------------------------------------

    def _prepare_diversity_check(self, children: list[Any], existing_population: list[Any]) -> list[Any]:
        """Bump mutation on near-duplicate siblings, and flag the cohort
        if the whole population (existing + new) is converging genetically.
        Mutates `children`'s genomes in place where adjustment is needed
        and returns the (same) list."""
        if len(children) < 2:
            self.population_homogeneity_flagged = False
            return children

        SIMILARITY_THRESHOLD = 0.95  # >95% identical bits => "too similar"
        REMUTATE_RATE = 0.20

        for i in range(len(children)):
            for j in range(i + 1, len(children)):
                sim = self._chromosome_similarity(children[i], children[j])
                if sim >= SIMILARITY_THRESHOLD:
                    logger.info(
                        "Siblings %s and %s are %.1f%% similar; re-mutating the younger one",
                        _attr(children[i], "agent_id"), _attr(children[j], "agent_id"), sim * 100,
                    )
                    genome_j = _attr(children[j], "genome")
                    genome_j.capability_chromosome = GenomeFactory._mutate_capability_chromosome(
                        genome_j.capability_chromosome, rate=REMUTATE_RATE, trigger=None
                    )
                    genome_j.infer_specialization()

        # Population-wide homogeneity flag: sample up to 50 chromosomes
        # from existing_population + children and average pairwise similarity.
        sample_pool = list(existing_population) + list(children)
        sample_pool = random.sample(sample_pool, min(50, len(sample_pool))) if len(sample_pool) > 50 else sample_pool

        similarities: list[float] = []
        for i in range(len(sample_pool)):
            for j in range(i + 1, len(sample_pool)):
                gi, gj = _attr(sample_pool[i], "genome"), _attr(sample_pool[j], "genome")
                if gi is None or gj is None:
                    continue
                similarities.append(self._chromosome_similarity(sample_pool[i], sample_pool[j]))

        avg_similarity = sum(similarities) / len(similarities) if similarities else 0.0
        self.population_homogeneity_flagged = avg_similarity >= 0.85
        if self.population_homogeneity_flagged:
            logger.warning(
                "Population homogeneity flagged (avg pairwise similarity=%.2f); "
                "next generation should use elevated mutation.", avg_similarity,
            )

        return children

    @staticmethod
    def _chromosome_similarity(agent_a: Any, agent_b: Any) -> float:
        bits_a = _attr(_attr(agent_a, "genome"), "capability_chromosome").bits
        bits_b = _attr(_attr(agent_b, "genome"), "capability_chromosome").bits
        matches = sum(1 for x, y in zip(bits_a, bits_b) if x == y)
        return matches / max(len(bits_a), 1)

    # -- credits -------------------------------------------------------

    def calculate_child_starting_credits(self, parent: Any, trigger: "ReproductionTrigger", num_children: int) -> float:  # noqa: F821
        """Per-child starting credit grant.

        SUCCESS: parent voluntarily transfers 20% of its credits *above
        the world's starting-credits baseline* ("excess"), split evenly
        among the children.
        ERROR / AGE: children start with a flat minimum-viable credit
        balance (2.0) regardless of the parent's own balance — crisis and
        generational-turnover reproduction is subsidized by the territory
        rather than drawn from an already-struggling or aging parent.
        """
        value = trigger.value if hasattr(trigger, "value") else str(trigger)
        if num_children <= 0:
            return 0.0
        if value == "SUCCESS":
            parent_credits = _attr(parent, "credits", 0.0) or 0.0
            baseline = WORLD_CONSTANTS["starting_credits"]
            excess = max(0.0, parent_credits - baseline)
            transfer_pool = 0.20 * excess
            return transfer_pool / num_children
        return 2.0  # ERROR / AGE / unknown

    @staticmethod
    def _deduct_credits(parent: Any, amount: float) -> None:
        current = _attr(parent, "credits", 0.0) or 0.0
        new_balance = max(0.0, current - amount)
        if isinstance(parent, dict):
            parent["credits"] = new_balance
        else:
            parent.credits = new_balance

    # -- inheritance / spawning -----------------------------------------

    @staticmethod
    def _safe_prepare_inheritance(parent: Any) -> dict:
        memory = _attr(parent, "memory")
        if memory is None:
            return {}
        prepare = getattr(memory, "prepare_inheritance_package", None)
        if not callable(prepare):
            return {}
        try:
            return prepare()
        except Exception:  # noqa: BLE001
            logger.exception("Failed to prepare inheritance package for agent %s", _attr(parent, "agent_id"))
            return {}

    @staticmethod
    async def _spawn_agent_cell(
        child_genome: Genome,
        inheritance_package: dict,
        parent: Any,
        territory: Any,
        starting_credits: float,
    ) -> Any:
        """Construct the new AgentCell and apply its inherited memory.

        Imported lazily to avoid a hard circular dependency between
        core.agent_cell and core.reproduction (agent_cell may itself import
        from reproduction for self-triggered reproduction).
        """
        from core.agent_cell import AgentCell  # local import: see docstring

        territory_id = _attr(territory, "territory_id")
        lineage_id = _attr(parent, "lineage_id") or uuid.uuid4()

        child = AgentCell(
            genome=child_genome,
            territory_id=territory_id,
            generation=_attr(parent, "generation", 0) + 1,
            parent_id=_attr(parent, "agent_id"),
            lineage_id=lineage_id,
            credits=starting_credits,
        )

        memory = _attr(child, "memory")
        if memory is not None and inheritance_package:
            receive = getattr(memory, "receive_inheritance", None)
            if callable(receive):
                try:
                    receive(inheritance_package)
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to apply inheritance package to child of agent %s", _attr(parent, "agent_id"))

        return child

    # -- elder transition ------------------------------------------------

    @staticmethod
    async def _transition_to_elder(parent: Any) -> None:
        try:
            from core.lifecycle import LifecycleManager  # local import: see docstring

            manager = LifecycleManager()
            await manager.transition_to_elder(parent)
        except Exception:  # noqa: BLE001
            logger.exception(
                "Failed to transition agent %s to elder via LifecycleManager; "
                "setting status directly as a fallback", _attr(parent, "agent_id"),
            )
            if isinstance(parent, dict):
                parent["status"] = AgentStatus.ELDER
            else:
                parent.status = AgentStatus.ELDER
