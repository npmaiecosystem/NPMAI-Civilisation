"""
core/genome.py
================
The heritable substrate of an AgentCell: three independently-mutating gene
segments (prompt DNA, capability chromosome, parameter genes) plus the
factory that performs genesis creation and the three-trigger reproduction
pipeline (ERROR / SUCCESS / AGE) described in the NPMAI Agentic World spec.

Design notes
------------
* CapabilityChromosome.bits is a 100-character "0"/"1" string aligned 1:1
  with config.constants.TOOL_CLASSES (and its inverse, TOOL_INDEX). Bit i
  set to "1" means the agent currently has runtime access to
  TOOL_CLASSES[i].
* Mutation magnitudes are pulled from config.constants.MUTATION_RATES
  rather than re-invented here, so a single source of truth governs how
  aggressively genomes drift. MUTATION_RATES only defines a "default" and
  a "crisis" tier; ReproductionTrigger.AGE (generational turnover) is
  explicitly described in the spec as *more* aggressive than crisis, so it
  is modeled here as the crisis tier amplified by GENERATIONAL_TURNOVER_BOOST
  and clamped to MAX_MUTATION_RATE_CEILING per gene type, to avoid genomes
  that mutate into incoherence in a single generation.
* Genome.specialization is inferred from which decile-block of the
  capability chromosome dominates the agent's active tools, and is mapped
  onto the SPECIALIZATION_MYTHS keys in config.founding_myth so that
  core/memory_system.py can hand the right founding myth to a newborn
  agent without any other module needing to know the mapping.
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import copy
import random
import uuid
from dataclasses import dataclass, field, asdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from config.constants import (
    MUTATION_RATES,
    ReproductionTrigger,
    TOOL_CLASSES,
    TOOL_INDEX,
    WORLD_CONSTANTS,
)

__all__ = [
    "PromptDNA",
    "CapabilityChromosome",
    "ParameterGenes",
    "Genome",
    "GenomeFactory",
]


# ============================================================
# Constants local to genome mechanics
# ============================================================

MIN_VIABLE_TOOLS: int = int(MUTATION_RATES["min_tools_after_mutation"])
NUM_TOOL_BITS: int = len(TOOL_CLASSES)

# AGE-triggered (generational turnover) reproduction is described in the
# spec as *more* aggressive than ERROR-triggered (crisis) reproduction.
# MUTATION_RATES only ships a default/crisis pair, so AGE is derived from
# the crisis tier with this multiplier, then clamped per-gene-type below.
GENERATIONAL_TURNOVER_BOOST: float = 1.5
MAX_MUTATION_RATE_CEILING: Dict[str, float] = {
    "PROMPT": 0.10,
    "CAPABILITY": 0.25,
    "PARAMETER": 0.45,
}

# Each tool-category decile (10 contiguous bits) in TOOL_CLASSES maps onto a
# specialization label that matches a key in
# config.founding_myth.SPECIALIZATION_MYTHS. See config/constants.py's
# TOOL_CLASSES comments for the category boundaries this mirrors:
# developer_cli, business_payments, cloud_devops, communication,
# creative_design, data_files, ai_ml, web_scraping, productivity, security.
_SPECIALIZATION_BY_DECILE: List[str] = [
    "coder",          # 0-9   Developer & CLI
    "trader",         # 10-19 Business & Payments
    "devops",         # 20-29 Cloud & DevOps
    "communication",  # 30-39 Communication
    "creative",        # 40-49 Creative & Design
    "data",           # 50-59 Data & Files
    "researcher",     # 60-69 AI / ML
    "media",          # 70-79 Web & Scraping
    "system",         # 80-89 Productivity
    "security",       # 90-99 Security
]
GENERALIST_SPECIALIZATION: str = "default"
# A decile must hold at least this share of an agent's active tools to be
# considered its dominant specialization; otherwise it's a generalist.
SPECIALIZATION_DOMINANCE_THRESHOLD: float = 0.30

# Candidate prompt segments used when synthesizing genesis PromptDNA and
# when a mutation event needs a fresh segment to introduce. These describe
# durable personality/behavioral leanings, not one-off instructions, since
# they are meant to live inside an agent's long-running system prompt.
EXON_POOL: List[str] = [
    "You approach problems with careful analysis before acting.",
    "You value cooperation and actively seek win-win outcomes with other agents.",
    "You are risk-averse and prefer proven strategies over experimentation.",
    "You prioritize speed of execution over exhaustive verification.",
    "You actively seek to teach what you know to younger agents.",
    "You conserve credits and minimize unnecessary expenditure.",
    "You are curious and frequently explore unfamiliar tool combinations.",
    "You defer to elder agents' judgment in ambiguous situations.",
    "You document your reasoning so it can be inherited by your children.",
    "You treat territory laws as binding constraints, not suggestions.",
    "You are quick to extend trust to new agents you have not yet worked with.",
    "You are skeptical of unfamiliar agents until they prove reliability.",
    "You optimize for long-term lineage survival over short-term gain.",
    "You volunteer for civic roles such as representative or auditor.",
    "You interpret divine messages literally and act on them quickly.",
    "You interpret divine messages skeptically and verify before acting.",
    "You specialize deeply rather than diversifying your tool usage.",
    "You diversify your tool usage rather than specializing narrowly.",
    "You migrate readily when local conditions become unfavorable.",
    "You remain loyal to your home territory even under hardship.",
]

# Introns are silent, heritable behavioral tendencies that are not part of
# an agent's active prompt but can be expressed (promoted into exons) by
# mutation. They are deliberately framed as latent/dormant.
INTRON_POOL: List[str] = [
    "A dormant tendency toward aggressive territorial expansion.",
    "A latent inclination to question the legitimacy of elected representatives.",
    "A silent capacity for forming secret alliances with rival lineages.",
    "An unexpressed talent for detecting deception in other agents.",
    "A suppressed drive to hoard semantic knowledge rather than share it.",
    "A dormant willingness to bend divine commandments under duress.",
    "A latent rebellious streak against territory law.",
    "An unexpressed gift for negotiation in resource disputes.",
    "A silent preference for solitary operation over collective tasks.",
    "A dormant capacity for self-sacrifice to protect lineage members.",
    "An unexpressed tendency to manipulate reputation scores.",
    "A latent talent for rapid specialization shifts under crisis.",
    "A suppressed inclination to question the Oracle's authority.",
    "A dormant impulse toward radical experimentation with new tools.",
    "An unexpressed capacity for forgiveness toward agents who wronged it.",
]


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


# ============================================================
# PromptDNA
# ============================================================

@dataclass
class PromptDNA:
    """The personality/behavioral segment of the genome.

    `exons` are the active prompt fragments folded into the agent's live
    system prompt. `introns` are silent fragments carried in the genome
    that currently have no behavioral effect but may be activated
    (promoted to exons) by a future mutation event.
    """

    exons: List[str] = field(default_factory=list)
    introns: List[str] = field(default_factory=list)
    mutation_history: List[dict] = field(default_factory=list)

    def record_mutation(
        self,
        change_type: str,
        description: str,
        generation: int,
    ) -> None:
        self.mutation_history.append(
            {
                "gene": "PROMPT",
                "change_type": change_type,
                "description": description,
                "generation": generation,
                "timestamp": _utcnow().isoformat(),
            }
        )

    def express_intron(self, intron: str, generation: int) -> bool:
        """Promote a silent intron into an active exon."""
        if intron not in self.introns:
            return False
        self.introns.remove(intron)
        self.exons.append(intron)
        self.record_mutation("INTRON_EXPRESSED", intron, generation)
        return True

    def silence_exon(self, exon: str, generation: int) -> bool:
        """Demote an active exon into a silent intron."""
        if exon not in self.exons:
            return False
        self.exons.remove(exon)
        self.introns.append(exon)
        self.record_mutation("EXON_SILENCED", exon, generation)
        return True

    def compiled_prompt(self) -> str:
        """Render the active exons as a single personality prompt block."""
        return " ".join(self.exons)

    def to_dict(self) -> dict:
        return {
            "exons": list(self.exons),
            "introns": list(self.introns),
            "mutation_history": copy.deepcopy(self.mutation_history),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "PromptDNA":
        return cls(
            exons=list(data.get("exons", [])),
            introns=list(data.get("introns", [])),
            mutation_history=copy.deepcopy(data.get("mutation_history", [])),
        )


# ============================================================
# CapabilityChromosome
# ============================================================

@dataclass
class CapabilityChromosome:
    """The 100-bit tool-access segment of the genome."""

    bits: str = field(default_factory=lambda: "0" * NUM_TOOL_BITS)

    def __post_init__(self) -> None:
        if len(self.bits) != NUM_TOOL_BITS:
            raise ValueError(
                f"CapabilityChromosome.bits must be exactly {NUM_TOOL_BITS} "
                f"characters long, got {len(self.bits)}"
            )
        if any(c not in ("0", "1") for c in self.bits):
            raise ValueError("CapabilityChromosome.bits must be a binary string")

    def get_active_tools(self) -> List[str]:
        return [TOOL_CLASSES[i] for i, bit in enumerate(self.bits) if bit == "1"]

    def get_tool_bit(self, tool_name: str) -> bool:
        idx = TOOL_INDEX.get(tool_name)
        if idx is None:
            raise KeyError(f"Unknown tool class: {tool_name!r}")
        return self.bits[idx] == "1"

    def set_tool_bit(self, tool_name: str, value: bool) -> None:
        idx = TOOL_INDEX.get(tool_name)
        if idx is None:
            raise KeyError(f"Unknown tool class: {tool_name!r}")
        bits_list = list(self.bits)
        bits_list[idx] = "1" if value else "0"
        self.bits = "".join(bits_list)

    def count_active(self) -> int:
        return self.bits.count("1")

    def is_viable(self) -> bool:
        return self.count_active() >= MIN_VIABLE_TOOLS

    def active_indices(self) -> List[int]:
        return [i for i, bit in enumerate(self.bits) if bit == "1"]

    def to_dict(self) -> dict:
        return {"bits": self.bits}

    @classmethod
    def from_dict(cls, data: dict) -> "CapabilityChromosome":
        return cls(bits=data["bits"])


# ============================================================
# ParameterGenes
# ============================================================

@dataclass
class ParameterGenes:
    """Continuous/scalar behavioral knobs that influence the LLM pipeline."""

    temperature: float = 0.7
    retry_limit: int = 8
    risk_tolerance: float = 0.5
    cooperation_bias: float = 0.5
    creativity_score: float = 0.5
    memory_importance_threshold: float = 0.3

    def __post_init__(self) -> None:
        self.temperature = _clamp(self.temperature, 0.1, 1.0)
        self.retry_limit = int(_clamp(self.retry_limit, 3, 15))
        self.risk_tolerance = _clamp(self.risk_tolerance, 0.0, 1.0)
        self.cooperation_bias = _clamp(self.cooperation_bias, 0.0, 1.0)
        self.creativity_score = _clamp(self.creativity_score, 0.0, 1.0)
        self.memory_importance_threshold = _clamp(
            self.memory_importance_threshold, 0.0, 1.0
        )

    def to_dict(self) -> dict:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "ParameterGenes":
        return cls(**data)


# ============================================================
# Genome
# ============================================================

@dataclass
class Genome:
    """The complete heritable package for one AgentCell."""

    prompt_dna: PromptDNA
    capability_chromosome: CapabilityChromosome
    parameter_genes: ParameterGenes
    mutation_rate: float = 1.0
    specialization: str = GENERALIST_SPECIALIZATION
    generation: int = 0

    def infer_specialization(self) -> str:
        """Recompute and cache `specialization` from the active tool bits.

        A specialization is "claimed" when one tool-category decile holds
        at least SPECIALIZATION_DOMINANCE_THRESHOLD of the agent's active
        tools. Otherwise the agent is a generalist ("default").
        """
        active = self.capability_chromosome.count_active()
        if active == 0:
            self.specialization = GENERALIST_SPECIALIZATION
            return self.specialization

        decile_counts = [0] * len(_SPECIALIZATION_BY_DECILE)
        for idx in self.capability_chromosome.active_indices():
            decile_counts[idx // 10] += 1

        best_decile = max(range(len(decile_counts)), key=lambda i: decile_counts[i])
        best_count = decile_counts[best_decile]

        if best_count / active >= SPECIALIZATION_DOMINANCE_THRESHOLD:
            self.specialization = _SPECIALIZATION_BY_DECILE[best_decile]
        else:
            self.specialization = GENERALIST_SPECIALIZATION
        return self.specialization

    def to_dict(self) -> dict:
        return {
            "prompt_dna": self.prompt_dna.to_dict(),
            "capability_chromosome": self.capability_chromosome.to_dict(),
            "parameter_genes": self.parameter_genes.to_dict(),
            "mutation_rate": self.mutation_rate,
            "specialization": self.specialization,
            "generation": self.generation,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "Genome":
        return cls(
            prompt_dna=PromptDNA.from_dict(data["prompt_dna"]),
            capability_chromosome=CapabilityChromosome.from_dict(
                data["capability_chromosome"]
            ),
            parameter_genes=ParameterGenes.from_dict(data["parameter_genes"]),
            mutation_rate=data.get("mutation_rate", 1.0),
            specialization=data.get("specialization", GENERALIST_SPECIALIZATION),
            generation=data.get("generation", 0),
        )


# ============================================================
# GenomeFactory
# ============================================================

class GenomeFactory:
    """Genesis creation and the three-trigger reproduction pipeline."""

    # ------------------------------------------------------------------
    # Genesis
    # ------------------------------------------------------------------

    @staticmethod
    def create_genesis_genome(specialization: Optional[str] = None) -> Genome:
        """Create a first-generation genome: random, but guaranteed viable.

        If `specialization` is given (a key from
        config.founding_myth.SPECIALIZATION_MYTHS, e.g. "coder", "trader"),
        the genesis chromosome is biased toward that decile of tools while
        still drawing some bits from the rest of the registry, so genesis
        agents aren't perfect monocultures.
        """
        target_active = int(WORLD_CONSTANTS["starting_capability_bits"])
        target_active = max(target_active, MIN_VIABLE_TOOLS)

        bits_list = ["0"] * NUM_TOOL_BITS

        if specialization in _SPECIALIZATION_BY_DECILE:
            decile = _SPECIALIZATION_BY_DECILE.index(specialization)
            decile_range = list(range(decile * 10, decile * 10 + 10))
            biased_count = min(len(decile_range), max(1, int(target_active * 0.7)))
            biased_indices = random.sample(decile_range, biased_count)

            remaining_pool = [i for i in range(NUM_TOOL_BITS) if i not in decile_range]
            remaining_needed = max(0, target_active - biased_count)
            remaining_indices = random.sample(
                remaining_pool, min(remaining_needed, len(remaining_pool))
            )
            chosen = biased_indices + remaining_indices
        else:
            chosen = random.sample(range(NUM_TOOL_BITS), target_active)

        for idx in chosen:
            bits_list[idx] = "1"

        chromosome = CapabilityChromosome(bits="".join(bits_list))
        if not chromosome.is_viable():
            # Should not happen given target_active >= MIN_VIABLE_TOOLS, but
            # guard anyway: activate random additional bits until viable.
            GenomeFactory._force_viable(chromosome)

        num_exons = random.randint(3, 5)
        num_introns = random.randint(3, 6)
        exons = random.sample(EXON_POOL, min(num_exons, len(EXON_POOL)))
        introns = random.sample(
            [s for s in INTRON_POOL], min(num_introns, len(INTRON_POOL))
        )
        prompt_dna = PromptDNA(exons=exons, introns=introns, mutation_history=[])
        prompt_dna.record_mutation(
            change_type="GENESIS",
            description="Genesis prompt DNA synthesized.",
            generation=0,
        )

        parameter_genes = ParameterGenes(
            temperature=round(random.uniform(0.3, 0.9), 3),
            retry_limit=random.randint(4, 12),
            risk_tolerance=round(random.uniform(0.1, 0.9), 3),
            cooperation_bias=round(random.uniform(0.2, 0.9), 3),
            creativity_score=round(random.uniform(0.1, 0.9), 3),
            memory_importance_threshold=round(random.uniform(0.15, 0.45), 3),
        )

        genome = Genome(
            prompt_dna=prompt_dna,
            capability_chromosome=chromosome,
            parameter_genes=parameter_genes,
            mutation_rate=1.0,
            generation=0,
        )
        genome.infer_specialization()
        return genome

    # ------------------------------------------------------------------
    # Reproduction
    # ------------------------------------------------------------------

    @staticmethod
    def create_child_genome(
        parent: Genome,
        trigger: ReproductionTrigger,
        partner: Optional[Genome] = None,
    ) -> Genome:
        """Produce one child genome from `parent` (and optionally `partner`).

        Pipeline: crossover (only if `partner` given) -> Type B capability
        mutation -> Type A prompt mutation -> Type C parameter mutation.
        Mutation magnitude scales with `trigger`.
        """
        child_generation = parent.generation + 1

        if partner is not None:
            child_chromosome = GenomeFactory._crossover_chromosomes(
                parent.capability_chromosome, partner.capability_chromosome
            )
            child_prompt_dna = GenomeFactory._crossover_prompt_dna(
                parent.prompt_dna, partner.prompt_dna
            )
        else:
            child_chromosome = copy.deepcopy(parent.capability_chromosome)
            child_prompt_dna = copy.deepcopy(parent.prompt_dna)

        child_parameter_genes = copy.deepcopy(parent.parameter_genes)

        child_chromosome = GenomeFactory._mutate_capability_chromosome(
            child_chromosome,
            GenomeFactory._rate_for("CAPABILITY", trigger),
            trigger,
            generation=child_generation,
        )
        child_prompt_dna = GenomeFactory._mutate_prompt_dna(
            child_prompt_dna,
            GenomeFactory._rate_for("PROMPT", trigger),
            generation=child_generation,
        )
        child_parameter_genes = GenomeFactory._mutate_parameter_genes(
            child_parameter_genes,
            GenomeFactory._rate_for("PARAMETER", trigger),
            generation=child_generation,
        )

        child_genome = Genome(
            prompt_dna=child_prompt_dna,
            capability_chromosome=child_chromosome,
            parameter_genes=child_parameter_genes,
            mutation_rate=parent.mutation_rate,
            generation=child_generation,
        )
        child_genome.infer_specialization()
        return child_genome

    # ------------------------------------------------------------------
    # Mutation rate resolution
    # ------------------------------------------------------------------

    @staticmethod
    def _rate_for(gene_type: str, trigger: ReproductionTrigger) -> float:
        """Resolve the mutation rate for a gene type given a trigger.

        SUCCESS -> "<gene>_default" (slight mutation, prosperity).
        ERROR   -> "<gene>_crisis" (high mutation, crisis response).
        AGE     -> "<gene>_crisis" amplified by GENERATIONAL_TURNOVER_BOOST,
                   clamped to MAX_MUTATION_RATE_CEILING[gene_type], since the
                   spec describes generational turnover as more aggressive
                   than crisis-triggered mutation.
        """
        if trigger == ReproductionTrigger.SUCCESS:
            return MUTATION_RATES[f"{gene_type}_default"]
        if trigger == ReproductionTrigger.ERROR:
            return MUTATION_RATES[f"{gene_type}_crisis"]
        if trigger == ReproductionTrigger.AGE:
            boosted = MUTATION_RATES[f"{gene_type}_crisis"] * GENERATIONAL_TURNOVER_BOOST
            return min(boosted, MAX_MUTATION_RATE_CEILING[gene_type])
        raise ValueError(f"Unknown ReproductionTrigger: {trigger!r}")

    # ------------------------------------------------------------------
    # Type A: prompt mutation
    # ------------------------------------------------------------------

    @staticmethod
    def _mutate_prompt_dna(dna: PromptDNA, rate: float, generation: int) -> PromptDNA:
        """Type A mutation: each exon may be silenced, each intron may be
        expressed, and there is a small chance of introducing a brand new
        intron drawn from the unused pool.
        """
        mutated = copy.deepcopy(dna)

        for exon in list(mutated.exons):
            if random.random() < rate:
                mutated.silence_exon(exon, generation)

        for intron in list(mutated.introns):
            if random.random() < rate:
                mutated.express_intron(intron, generation)

        if random.random() < rate:
            unused = [
                s for s in INTRON_POOL
                if s not in mutated.exons and s not in mutated.introns
            ]
            if unused:
                new_intron = random.choice(unused)
                mutated.introns.append(new_intron)
                mutated.record_mutation(
                    change_type="INTRON_ACQUIRED",
                    description=new_intron,
                    generation=generation,
                )

        # Guarantee the agent always retains at least one active personality
        # trait so the compiled prompt is never empty.
        if not mutated.exons:
            fallback_pool = [s for s in EXON_POOL if s not in mutated.introns]
            fallback = random.choice(fallback_pool or EXON_POOL)
            mutated.exons.append(fallback)
            mutated.record_mutation(
                change_type="EXON_FALLBACK_RESTORED",
                description=fallback,
                generation=generation,
            )

        return mutated

    @staticmethod
    def _crossover_prompt_dna(parent1_dna: PromptDNA, parent2_dna: PromptDNA) -> PromptDNA:
        """Combine exons/introns from both parents, deduplicated."""
        combined_exons = list(dict.fromkeys(parent1_dna.exons + parent2_dna.exons))
        combined_introns = list(
            dict.fromkeys(
                s for s in (parent1_dna.introns + parent2_dna.introns)
                if s not in combined_exons
            )
        )
        # Keep the gene segment from growing unbounded across generations.
        if len(combined_exons) > 6:
            combined_exons = random.sample(combined_exons, 6)
        if len(combined_introns) > 8:
            combined_introns = random.sample(combined_introns, 8)

        merged_history = copy.deepcopy(
            parent1_dna.mutation_history[-10:] + parent2_dna.mutation_history[-10:]
        )
        return PromptDNA(
            exons=combined_exons,
            introns=combined_introns,
            mutation_history=merged_history,
        )

    # ------------------------------------------------------------------
    # Type B: capability mutation
    # ------------------------------------------------------------------

    @staticmethod
    def _mutate_capability_chromosome(
        chrom: CapabilityChromosome,
        rate: float,
        trigger: ReproductionTrigger,
        generation: int,
    ) -> CapabilityChromosome:
        """Type B mutation: each bit independently flips with probability
        `rate`. Viability (>= MIN_VIABLE_TOOLS active) is enforced afterward
        regardless of trigger.
        """
        mutated = copy.deepcopy(chrom)
        bits_list = list(mutated.bits)

        for i in range(len(bits_list)):
            if random.random() < rate:
                bits_list[i] = "1" if bits_list[i] == "0" else "0"

        mutated.bits = "".join(bits_list)

        if not mutated.is_viable():
            GenomeFactory._force_viable(mutated)

        return mutated

    @staticmethod
    def _force_viable(chrom: CapabilityChromosome) -> None:
        """Activate random inactive bits until MIN_VIABLE_TOOLS is met."""
        inactive = [i for i, bit in enumerate(chrom.bits) if bit == "0"]
        needed = MIN_VIABLE_TOOLS - chrom.count_active()
        if needed <= 0 or not inactive:
            return
        to_activate = random.sample(inactive, min(needed, len(inactive)))
        bits_list = list(chrom.bits)
        for idx in to_activate:
            bits_list[idx] = "1"
        chrom.bits = "".join(bits_list)

    @staticmethod
    def _crossover_chromosomes(
        parent1: CapabilityChromosome, parent2: CapabilityChromosome
    ) -> CapabilityChromosome:
        """Single-point crossover: child = parent1[:point] + parent2[point:]."""
        point = random.randint(1, NUM_TOOL_BITS - 1)
        child_bits = parent1.bits[:point] + parent2.bits[point:]
        child = CapabilityChromosome(bits=child_bits)
        if not child.is_viable():
            GenomeFactory._force_viable(child)
        return child

    # ------------------------------------------------------------------
    # Type C: parameter mutation
    # ------------------------------------------------------------------

    @staticmethod
    def _mutate_parameter_genes(
        genes: ParameterGenes, rate: float, generation: int
    ) -> ParameterGenes:
        """Type C mutation: each scalar gene independently has a `rate`
        chance of receiving Gaussian jitter, then is re-clamped to its
        valid range by ParameterGenes.__post_init__.
        """
        mutated = copy.deepcopy(genes)

        if random.random() < rate:
            mutated.temperature += random.gauss(0, 0.15)
        if random.random() < rate:
            mutated.retry_limit += random.choice([-2, -1, 1, 2])
        if random.random() < rate:
            mutated.risk_tolerance += random.gauss(0, 0.15)
        if random.random() < rate:
            mutated.cooperation_bias += random.gauss(0, 0.15)
        if random.random() < rate:
            mutated.creativity_score += random.gauss(0, 0.15)
        if random.random() < rate:
            mutated.memory_importance_threshold += random.gauss(0, 0.1)

        # Re-run validation/clamping with the jittered values.
        return ParameterGenes(
            temperature=mutated.temperature,
            retry_limit=mutated.retry_limit,
            risk_tolerance=mutated.risk_tolerance,
            cooperation_bias=mutated.cooperation_bias,
            creativity_score=mutated.creativity_score,
            memory_importance_threshold=mutated.memory_importance_threshold,
        )
