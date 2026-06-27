"""
world/territory.py
==================
Territory infrastructure for the NPMAI Agentic World.

TerritoryLaw  — a law enacted by RID governance
Territory      — a computational territory (host + resource pool + population)
TerritoryManager — registry and coordinator for all territories

Author : Sonu Kumar · NPMAI ECOSYSTEM
Session: 5 (world layer)
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config.constants import BorderPolicy, WORLD_CONSTANTS, CREDIT_COSTS
from data.event_logger import EventLogger
from data.event_types import WorldEventType


def _utc_now_ms() -> int:
    return int(datetime.now(timezone.utc).timestamp() * 1000)


# ─────────────────────────────────────────────────────────────────────────────
# TerritoryLaw
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class TerritoryLaw:
    """
    A law passed via RID governance and enforced on every agent action.

    Fields
    ------
    law_id          : unique identifier
    title           : short human-readable title
    rule_text       : full natural-language rule (used for keyword violation check)
    passed_at       : UTC ms timestamp when vote concluded
    vote_count      : number of unique voters
    passed_by       : list of agent_ids who voted FOR
    status          : "ACTIVE" or "REPEALED"
    banned_specializations : optional explicit list for border-policy enforcement
    min_reputation_required: optional float for RESTRICTED border entry
    """
    law_id:   str = field(default_factory=lambda: str(uuid.uuid4()))
    title:    str = ""
    rule_text: str = ""
    passed_at: int = field(default_factory=_utc_now_ms)
    vote_count: int = 0
    passed_by:  List[str] = field(default_factory=list)
    status:     str = "ACTIVE"
    banned_specializations: List[str] = field(default_factory=list)
    min_reputation_required: Optional[float] = None

    # ── helpers ───────────────────────────────────────────────────────────────

    def is_active(self) -> bool:
        return self.status == "ACTIVE"

    def repeal(self) -> None:
        self.status = "REPEALED"

    def to_dict(self) -> Dict[str, Any]:
        return {
            "law_id":   self.law_id,
            "title":    self.title,
            "rule_text": self.rule_text,
            "passed_at": self.passed_at,
            "vote_count": self.vote_count,
            "passed_by": self.passed_by,
            "status":   self.status,
            "banned_specializations": self.banned_specializations,
            "min_reputation_required": self.min_reputation_required,
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "TerritoryLaw":
        return cls(**{k: v for k, v in d.items() if k in cls.__dataclass_fields__})


# ─────────────────────────────────────────────────────────────────────────────
# Territory
# ─────────────────────────────────────────────────────────────────────────────

class Territory:
    """
    A computational territory in the NPMAI Agentic World.

    Each territory is a bounded environment with:
    - A fixed resource envelope (CPU, RAM, agent capacity)
    - A credit pool shared by the territory's economy
    - A population of agent_ids
    - An active law stack enforced by the Auditor role
    - A governance border policy (OPEN / RESTRICTED / CLOSED)
    - A health score updated every tick

    Territories are independent nodes in the network; in Phase 2+ each
    runs on a different host.
    """

    def __init__(
        self,
        territory_id: Optional[str] = None,
        name: str = "Unnamed Territory",
        host: str = "localhost",
        cpu_limit:       float = 100.0,   # percentage points
        ram_limit:       float = 8192.0,  # MB
        agent_capacity:  int   = 50,
        starting_credits: float = 100.0,
    ) -> None:
        self.territory_id: str  = territory_id or str(uuid.uuid4())
        self.name:         str  = name
        self.host:         str  = host

        # ── Resources ─────────────────────────────────────────────────────────
        self.resources: Dict[str, Any] = {
            "cpu":          cpu_limit,
            "ram":          ram_limit,
            "capacity":     agent_capacity,
            "credit_pool":  starting_credits,
            "cpu_used":     0.0,
            "ram_used":     0.0,
        }

        # ── Population ────────────────────────────────────────────────────────
        self._population:    List[str]           = []   # agent_ids
        self._laws:          List[TerritoryLaw]  = []
        self._border_policy: BorderPolicy        = BorderPolicy.OPEN
        self._age:           int                 = 0    # ticks
        self._health:        float               = 1.0  # 0.0 – 1.0

    # ── Properties ────────────────────────────────────────────────────────────

    @property
    def population(self) -> List[str]:
        return self._population

    @population.setter
    def population(self, value: List[str]) -> None:
        self._population = value

    @property
    def laws(self) -> List[TerritoryLaw]:
        return self._laws

    @property
    def credit_pool(self) -> float:
        return self.resources.get("credit_pool", 0.0)

    @credit_pool.setter
    def credit_pool(self, value: float) -> None:
        self.resources["credit_pool"] = max(0.0, value)

    @property
    def border_policy(self) -> BorderPolicy:
        return self._border_policy

    @border_policy.setter
    def border_policy(self, value: BorderPolicy) -> None:
        self._border_policy = value

    @property
    def health(self) -> float:
        return self._health

    @property
    def age(self) -> int:
        return self._age

    @property
    def resource_usage(self) -> Dict[str, Any]:
        cap = self.resources.get("capacity", 1)
        pop = len(self._population)
        return {
            "population":         pop,
            "capacity":           cap,
            "population_pct":     round(pop / max(cap, 1) * 100, 1),
            "cpu_used":           self.resources.get("cpu_used", 0.0),
            "cpu_total":          self.resources.get("cpu", 0.0),
            "ram_used":           self.resources.get("ram_used", 0.0),
            "ram_total":          self.resources.get("ram", 0.0),
            "credit_pool":        self.credit_pool,
        }

    # ── Population management ─────────────────────────────────────────────────

    def add_agent(self, agent_id: str) -> bool:
        """Register an agent. Returns False if territory is at capacity."""
        capacity = self.resources.get("capacity", 50)
        if len(self._population) >= capacity:
            return False
        if agent_id not in self._population:
            self._population.append(agent_id)
        return True

    def remove_agent(self, agent_id: str) -> None:
        try:
            self._population.remove(agent_id)
        except ValueError:
            pass

    def get_population_count(self) -> int:
        return len(self._population)

    def get_resource_availability(self) -> Dict[str, float]:
        """Fraction of each resource still available (0.0 – 1.0)."""
        cpu_total  = max(self.resources.get("cpu", 1.0), 1.0)
        ram_total  = max(self.resources.get("ram", 1.0), 1.0)
        capacity   = max(self.resources.get("capacity", 1), 1)
        return {
            "cpu":      max(0.0, 1.0 - self.resources.get("cpu_used", 0.0) / cpu_total),
            "ram":      max(0.0, 1.0 - self.resources.get("ram_used", 0.0) / ram_total),
            "capacity": max(0.0, 1.0 - len(self._population) / capacity),
            "credits":  min(1.0, self.credit_pool / max(WORLD_CONSTANTS.get("starting_credits", 10.0) * 10, 1.0)),
        }

    def calculate_overpopulation_pressure(self) -> float:
        """
        0.0 = empty, 1.0 = exactly at capacity, >1.0 = overpopulated.
        Drives migration push signals for agents inside this territory.
        """
        cap = self.resources.get("capacity", 1)
        return len(self._population) / max(cap, 1)

    # ── Law management ────────────────────────────────────────────────────────

    def add_law(self, law: TerritoryLaw) -> None:
        # Prevent duplicate law_ids
        existing_ids = {l.law_id for l in self._laws}
        if law.law_id not in existing_ids:
            self._laws.append(law)

    def repeal_law(self, law_id: str) -> bool:
        for law in self._laws:
            if law.law_id == law_id:
                law.repeal()
                return True
        return False

    def get_active_laws(self) -> List[TerritoryLaw]:
        return [l for l in self._laws if l.is_active()]

    def is_law_violated(self, action: str) -> Optional[TerritoryLaw]:
        """
        Simple keyword-based violation check.
        Scans action text against each active law's rule_text.
        Returns the first violated law, or None if action is clean.

        The Auditor LLM is the real enforcement engine; this is the fast
        pre-check the WorldController uses to flag obvious violations.
        """
        action_lower = action.lower()
        for law in self.get_active_laws():
            # Extract prohibited keywords from rule_text heuristically:
            # look for patterns like "no X", "prohibit X", "ban X", "forbidden: X"
            rule_lower = law.rule_text.lower()
            for pattern_prefix in ("no ", "prohibit ", "ban ", "forbidden ", "illegal "):
                idx = rule_lower.find(pattern_prefix)
                while idx != -1:
                    start = idx + len(pattern_prefix)
                    # Take up to the next punctuation or end
                    end = start
                    while end < len(rule_lower) and rule_lower[end] not in ".,;:!?\n":
                        end += 1
                    keyword = rule_lower[start:end].strip()
                    if keyword and keyword in action_lower:
                        return law
                    idx = rule_lower.find(pattern_prefix, idx + 1)
        return None

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick(self) -> None:
        """
        Called every world tick by TerritoryManager.tick_all().
        - Increments age
        - Recalculates resource usage proxies (population-proportional)
        - Updates health score
        - Taxes credit_pool mildly (territory maintenance)
        """
        self._age += 1

        pop       = len(self._population)
        capacity  = max(self.resources.get("capacity", 1), 1)
        cpu_total = self.resources.get("cpu", 100.0)
        ram_total = self.resources.get("ram", 8192.0)

        # Approximate resource usage: each agent consumes a proportional slice
        agent_fraction = pop / capacity
        self.resources["cpu_used"] = round(cpu_total * agent_fraction * 0.6, 2)
        self.resources["ram_used"] = round(ram_total * agent_fraction * 0.5, 2)

        # Health degrades under sustained overpopulation and credit poverty
        overpop_pressure = self.calculate_overpopulation_pressure()
        credit_health    = min(1.0, self.credit_pool / max(WORLD_CONSTANTS.get("starting_credits", 10.0) * capacity, 1.0))
        law_stability    = min(1.0, len(self.get_active_laws()) / max(5, 1))

        self._health = round(
            0.4 * max(0.0, 1.0 - max(0.0, overpop_pressure - 1.0))
            + 0.3 * credit_health
            + 0.3 * law_stability,
            4,
        )

        # Mild territory maintenance cost from the credit pool
        maintenance = CREDIT_COSTS.get("territory_maintenance_per_tick", 0.05)
        self.resources["credit_pool"] = max(0.0, self.credit_pool - maintenance)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def get_state_snapshot(self) -> Dict[str, Any]:
        return {
            "territory_id":   self.territory_id,
            "name":           self.name,
            "host":           self.host,
            "age":            self._age,
            "health":         self._health,
            "border_policy":  self._border_policy.value if hasattr(self._border_policy, "value") else str(self._border_policy),
            "population":     list(self._population),
            "population_count": len(self._population),
            "resources":      dict(self.resources),
            "resource_usage": self.resource_usage,
            "resource_availability": self.get_resource_availability(),
            "overpopulation_pressure": round(self.calculate_overpopulation_pressure(), 4),
            "active_laws":    [l.to_dict() for l in self.get_active_laws()],
            "total_laws":     len(self._laws),
            "snapshot_at":    _utc_now_ms(),
        }

    def to_dict(self) -> Dict[str, Any]:
        return {
            **self.get_state_snapshot(),
            "all_laws": [l.to_dict() for l in self._laws],
        }

    @classmethod
    def from_dict(cls, d: Dict[str, Any]) -> "Territory":
        t = cls(
            territory_id=d.get("territory_id"),
            name=d.get("name", "Unknown"),
            host=d.get("host", "localhost"),
            cpu_limit=d.get("resources", {}).get("cpu", 100.0),
            ram_limit=d.get("resources", {}).get("ram", 8192.0),
            agent_capacity=d.get("resources", {}).get("capacity", 50),
            starting_credits=d.get("resources", {}).get("credit_pool", 100.0),
        )
        t._population = d.get("population", [])
        t._age        = d.get("age", 0)
        t._health     = d.get("health", 1.0)
        raw_policy    = d.get("border_policy", "OPEN")
        try:
            t._border_policy = BorderPolicy(raw_policy)
        except ValueError:
            t._border_policy = BorderPolicy.OPEN
        for law_dict in d.get("all_laws", []):
            t._laws.append(TerritoryLaw.from_dict(law_dict))
        return t

    def __repr__(self) -> str:
        return (
            f"<Territory id={self.territory_id[:8]} name={self.name!r} "
            f"pop={len(self._population)}/{self.resources.get('capacity')} "
            f"health={self._health:.2f} age={self._age}>"
        )


# ─────────────────────────────────────────────────────────────────────────────
# TerritoryManager
# ─────────────────────────────────────────────────────────────────────────────

class TerritoryManager:
    """Registry and coordinator for all territories in the simulation."""

    def __init__(self) -> None:
        self._territories: Dict[str, Territory] = {}
        self._logger = EventLogger.get_instance()

    # ── CRUD ──────────────────────────────────────────────────────────────────

    def create_territory(
        self,
        name: str,
        host: str = "localhost",
        config: Optional[Dict[str, Any]] = None,
    ) -> Territory:
        cfg = config or {}
        t = Territory(
            name=name,
            host=host,
            cpu_limit=cfg.get("cpu_limit", 100.0),
            ram_limit=cfg.get("ram_limit", 8192.0),
            agent_capacity=cfg.get("agent_capacity", 50),
            starting_credits=cfg.get("starting_credits",
                                     WORLD_CONSTANTS.get("territory_starting_credits", 200.0)),
        )
        self._territories[t.territory_id] = t
        return t

    def get_territory(self, territory_id: str) -> Optional[Territory]:
        return self._territories.get(territory_id)

    def get_all_territories(self) -> List[Territory]:
        return list(self._territories.values())

    def register_territory(self, territory: Territory) -> None:
        self._territories[territory.territory_id] = territory

    # ── Queries ───────────────────────────────────────────────────────────────

    def get_least_populated(self) -> Optional[Territory]:
        if not self._territories:
            return None
        return min(
            self._territories.values(),
            key=lambda t: t.calculate_overpopulation_pressure(),
        )

    def get_most_resourced(self) -> Optional[Territory]:
        if not self._territories:
            return None
        return max(
            self._territories.values(),
            key=lambda t: sum(t.get_resource_availability().values()),
        )

    def get_open_territories(self) -> List[Territory]:
        return [
            t for t in self._territories.values()
            if t.border_policy in (BorderPolicy.OPEN, BorderPolicy.RESTRICTED)
        ]

    # ── Tick ──────────────────────────────────────────────────────────────────

    def tick_all(self) -> None:
        for territory in self._territories.values():
            territory.tick()

    # ── Network map ───────────────────────────────────────────────────────────

    def get_network_map(self) -> Dict[str, Any]:
        """
        Returns a lightweight network-topology dict for the observatory
        and Three.js visualizer.

        Format
        ------
        {
          "nodes": [{"id": territory_id, "name": ..., "host": ...,
                     "population": N, "health": 0.8, ...}],
          "edges": []   # populated in Phase 2 when territories exchange agents
        }
        """
        nodes = []
        for t in self._territories.values():
            nodes.append({
                "id":          t.territory_id,
                "name":        t.name,
                "host":        t.host,
                "population":  t.get_population_count(),
                "capacity":    t.resources.get("capacity", 50),
                "health":      round(t.health, 4),
                "age":         t.age,
                "credit_pool": round(t.credit_pool, 2),
                "border_policy": (
                    t.border_policy.value if hasattr(t.border_policy, "value")
                    else str(t.border_policy)
                ),
                "active_laws": len(t.get_active_laws()),
            })
        return {"nodes": nodes, "edges": []}

    def __repr__(self) -> str:
        return f"<TerritoryManager territories={len(self._territories)}>"
