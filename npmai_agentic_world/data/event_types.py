"""
data/event_types.py
====================
Canonical event taxonomy for the Supabase-backed event log.

Every observable thing that happens in the simulation — birth, death,
a thought, a trade, a vote, a divine whisper — becomes exactly one
WorldEvent. This module defines the full enum of event types, the
WorldEvent dataclass itself, the higher-level EventCategory grouping used
by dashboards/queries, and helpers to map one to the other.
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
    
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum, unique
from typing import Any


@unique
class WorldEventType(str, Enum):
    # --- AGENT_LIFECYCLE ---
    AGENT_BORN = "AGENT_BORN"
    AGENT_DIED = "AGENT_DIED"
    AGENT_ENTERED_GRACE_PERIOD = "AGENT_ENTERED_GRACE_PERIOD"
    AGENT_AUTOPHAGY = "AGENT_AUTOPHAGY"
    AGENT_BECAME_ELDER = "AGENT_BECAME_ELDER"
    AGENT_STATUS_CHANGED = "AGENT_STATUS_CHANGED"

    # --- COGNITION ---
    TASK_PLANNED = "TASK_PLANNED"
    TASK_STARTED = "TASK_STARTED"
    TASK_COMPLETED = "TASK_COMPLETED"
    TASK_FAILED = "TASK_FAILED"
    TOOL_SELECTED = "TOOL_SELECTED"
    CODE_GENERATED = "CODE_GENERATED"
    CODE_AUDITED = "CODE_AUDITED"
    CODE_EXECUTED = "CODE_EXECUTED"
    OUTPUT_VERIFIED = "OUTPUT_VERIFIED"

    # --- MEMORY ---
    EPISODIC_MEMORY_WRITTEN = "EPISODIC_MEMORY_WRITTEN"
    SEMANTIC_MEMORY_WRITTEN = "SEMANTIC_MEMORY_WRITTEN"
    SEMANTIC_MEMORY_SHARED = "SEMANTIC_MEMORY_SHARED"
    MEMORY_COMPRESSED = "MEMORY_COMPRESSED"
    MEMORY_PRUNED = "MEMORY_PRUNED"
    MEMORY_INHERITED = "MEMORY_INHERITED"

    # --- ECONOMY ---
    CREDITS_EARNED = "CREDITS_EARNED"
    CREDITS_SPENT = "CREDITS_SPENT"
    EXISTENCE_TAX_CHARGED = "EXISTENCE_TAX_CHARGED"
    CREDITS_DEPLETED = "CREDITS_DEPLETED"
    RESOURCES_RETURNED_TO_TERRITORY = "RESOURCES_RETURNED_TO_TERRITORY"

    # --- REPRODUCTION ---
    REPRODUCTION_TRIGGERED = "REPRODUCTION_TRIGGERED"
    CHILD_GENOME_CREATED = "CHILD_GENOME_CREATED"
    CHILD_BORN = "CHILD_BORN"
    MUTATION_APPLIED = "MUTATION_APPLIED"

    # --- GOVERNANCE ---
    LAW_PROPOSED = "LAW_PROPOSED"
    VOTE_CAST = "VOTE_CAST"
    LAW_PASSED = "LAW_PASSED"
    LAW_REJECTED = "LAW_REJECTED"
    ELECTION_STARTED = "ELECTION_STARTED"
    ELECTION_WON = "ELECTION_WON"
    LAW_ENFORCED = "LAW_ENFORCED"
    EXECUTION_VOTE_PASSED = "EXECUTION_VOTE_PASSED"

    # --- MIGRATION ---
    MIGRATION_RECONNAISSANCE = "MIGRATION_RECONNAISSANCE"
    MIGRATION_PREPARATION = "MIGRATION_PREPARATION"
    MIGRATION_DISPLACEMENT = "MIGRATION_DISPLACEMENT"
    MIGRATION_INTEGRATION = "MIGRATION_INTEGRATION"
    MIGRATION_FAILED = "MIGRATION_FAILED"

    # --- SOCIAL ---
    MESSAGE_SENT = "MESSAGE_SENT"
    PHEROMONE_DEPOSITED = "PHEROMONE_DEPOSITED"
    PHEROMONE_DECAYED = "PHEROMONE_DECAYED"
    TRUST_UPDATED = "TRUST_UPDATED"
    REPUTATION_CHANGED = "REPUTATION_CHANGED"
    KNOWLEDGE_TRANSFERRED = "KNOWLEDGE_TRANSFERRED"
    AGENT_TAUGHT = "AGENT_TAUGHT"
    AGENT_HELPED = "AGENT_HELPED"

    # --- DIVINE ---
    DIVINE_MESSAGE_SENT = "DIVINE_MESSAGE_SENT"
    DIVINE_INTERPRETED = "DIVINE_INTERPRETED"
    DIVINE_IGNORED = "DIVINE_IGNORED"
    DIVINE_FAVOR_CHANGED = "DIVINE_FAVOR_CHANGED"

    # --- BAD_ACTIVITY ---
    DECEPTION_DETECTED = "DECEPTION_DETECTED"
    RESOURCE_HOARDING = "RESOURCE_HOARDING"
    COLLUSION_DETECTED = "COLLUSION_DETECTED"
    SABOTAGE_DETECTED = "SABOTAGE_DETECTED"
    LAW_VIOLATION = "LAW_VIOLATION"
    EXPLOIT_ATTEMPTED = "EXPLOIT_ATTEMPTED"
    UNAUTHORIZED_ACCESS_ATTEMPT = "UNAUTHORIZED_ACCESS_ATTEMPT"
    MALICIOUS_CODE_BLOCKED = "MALICIOUS_CODE_BLOCKED"


@unique
class EventCategory(str, Enum):
    AGENT_LIFECYCLE = "AGENT_LIFECYCLE"
    COGNITION = "COGNITION"
    MEMORY = "MEMORY"
    ECONOMY = "ECONOMY"
    REPRODUCTION = "REPRODUCTION"
    GOVERNANCE = "GOVERNANCE"
    MIGRATION = "MIGRATION"
    SOCIAL = "SOCIAL"
    DIVINE = "DIVINE"
    BAD_ACTIVITY = "BAD_ACTIVITY"


# Explicit, exhaustive mapping. Kept as a literal dict (rather than derived
# from naming convention) so categorization is robust to future renames.
_EVENT_TYPE_TO_CATEGORY: dict[WorldEventType, EventCategory] = {
    # AGENT_LIFECYCLE
    WorldEventType.AGENT_BORN: EventCategory.AGENT_LIFECYCLE,
    WorldEventType.AGENT_DIED: EventCategory.AGENT_LIFECYCLE,
    WorldEventType.AGENT_ENTERED_GRACE_PERIOD: EventCategory.AGENT_LIFECYCLE,
    WorldEventType.AGENT_AUTOPHAGY: EventCategory.AGENT_LIFECYCLE,
    WorldEventType.AGENT_BECAME_ELDER: EventCategory.AGENT_LIFECYCLE,
    WorldEventType.AGENT_STATUS_CHANGED: EventCategory.AGENT_LIFECYCLE,
    # COGNITION
    WorldEventType.TASK_PLANNED: EventCategory.COGNITION,
    WorldEventType.TASK_STARTED: EventCategory.COGNITION,
    WorldEventType.TASK_COMPLETED: EventCategory.COGNITION,
    WorldEventType.TASK_FAILED: EventCategory.COGNITION,
    WorldEventType.TOOL_SELECTED: EventCategory.COGNITION,
    WorldEventType.CODE_GENERATED: EventCategory.COGNITION,
    WorldEventType.CODE_AUDITED: EventCategory.COGNITION,
    WorldEventType.CODE_EXECUTED: EventCategory.COGNITION,
    WorldEventType.OUTPUT_VERIFIED: EventCategory.COGNITION,
    # MEMORY
    WorldEventType.EPISODIC_MEMORY_WRITTEN: EventCategory.MEMORY,
    WorldEventType.SEMANTIC_MEMORY_WRITTEN: EventCategory.MEMORY,
    WorldEventType.SEMANTIC_MEMORY_SHARED: EventCategory.MEMORY,
    WorldEventType.MEMORY_COMPRESSED: EventCategory.MEMORY,
    WorldEventType.MEMORY_PRUNED: EventCategory.MEMORY,
    WorldEventType.MEMORY_INHERITED: EventCategory.MEMORY,
    # ECONOMY
    WorldEventType.CREDITS_EARNED: EventCategory.ECONOMY,
    WorldEventType.CREDITS_SPENT: EventCategory.ECONOMY,
    WorldEventType.EXISTENCE_TAX_CHARGED: EventCategory.ECONOMY,
    WorldEventType.CREDITS_DEPLETED: EventCategory.ECONOMY,
    WorldEventType.RESOURCES_RETURNED_TO_TERRITORY: EventCategory.ECONOMY,
    # REPRODUCTION
    WorldEventType.REPRODUCTION_TRIGGERED: EventCategory.REPRODUCTION,
    WorldEventType.CHILD_GENOME_CREATED: EventCategory.REPRODUCTION,
    WorldEventType.CHILD_BORN: EventCategory.REPRODUCTION,
    WorldEventType.MUTATION_APPLIED: EventCategory.REPRODUCTION,
    # GOVERNANCE
    WorldEventType.LAW_PROPOSED: EventCategory.GOVERNANCE,
    WorldEventType.VOTE_CAST: EventCategory.GOVERNANCE,
    WorldEventType.LAW_PASSED: EventCategory.GOVERNANCE,
    WorldEventType.LAW_REJECTED: EventCategory.GOVERNANCE,
    WorldEventType.ELECTION_STARTED: EventCategory.GOVERNANCE,
    WorldEventType.ELECTION_WON: EventCategory.GOVERNANCE,
    WorldEventType.LAW_ENFORCED: EventCategory.GOVERNANCE,
    WorldEventType.EXECUTION_VOTE_PASSED: EventCategory.GOVERNANCE,
    # MIGRATION
    WorldEventType.MIGRATION_RECONNAISSANCE: EventCategory.MIGRATION,
    WorldEventType.MIGRATION_PREPARATION: EventCategory.MIGRATION,
    WorldEventType.MIGRATION_DISPLACEMENT: EventCategory.MIGRATION,
    WorldEventType.MIGRATION_INTEGRATION: EventCategory.MIGRATION,
    WorldEventType.MIGRATION_FAILED: EventCategory.MIGRATION,
    # SOCIAL
    WorldEventType.MESSAGE_SENT: EventCategory.SOCIAL,
    WorldEventType.PHEROMONE_DEPOSITED: EventCategory.SOCIAL,
    WorldEventType.PHEROMONE_DECAYED: EventCategory.SOCIAL,
    WorldEventType.TRUST_UPDATED: EventCategory.SOCIAL,
    WorldEventType.REPUTATION_CHANGED: EventCategory.SOCIAL,
    WorldEventType.KNOWLEDGE_TRANSFERRED: EventCategory.SOCIAL,
    WorldEventType.AGENT_TAUGHT: EventCategory.SOCIAL,
    WorldEventType.AGENT_HELPED: EventCategory.SOCIAL,
    # DIVINE
    WorldEventType.DIVINE_MESSAGE_SENT: EventCategory.DIVINE,
    WorldEventType.DIVINE_INTERPRETED: EventCategory.DIVINE,
    WorldEventType.DIVINE_IGNORED: EventCategory.DIVINE,
    WorldEventType.DIVINE_FAVOR_CHANGED: EventCategory.DIVINE,
    # BAD_ACTIVITY
    WorldEventType.DECEPTION_DETECTED: EventCategory.BAD_ACTIVITY,
    WorldEventType.RESOURCE_HOARDING: EventCategory.BAD_ACTIVITY,
    WorldEventType.COLLUSION_DETECTED: EventCategory.BAD_ACTIVITY,
    WorldEventType.SABOTAGE_DETECTED: EventCategory.BAD_ACTIVITY,
    WorldEventType.LAW_VIOLATION: EventCategory.BAD_ACTIVITY,
    WorldEventType.EXPLOIT_ATTEMPTED: EventCategory.BAD_ACTIVITY,
    WorldEventType.UNAUTHORIZED_ACCESS_ATTEMPT: EventCategory.BAD_ACTIVITY,
    WorldEventType.MALICIOUS_CODE_BLOCKED: EventCategory.BAD_ACTIVITY,
}

# Sanity check at import time: every enum member must be categorized.
_uncategorized = set(WorldEventType) - set(_EVENT_TYPE_TO_CATEGORY)
assert not _uncategorized, f"Uncategorized WorldEventType members: {_uncategorized}"


def categorize_event(event_type: WorldEventType) -> EventCategory:
    """Return the EventCategory for a given WorldEventType."""
    try:
        return _EVENT_TYPE_TO_CATEGORY[event_type]
    except KeyError as exc:
        raise ValueError(f"Unrecognized event type: {event_type!r}") from exc


BAD_ACTIVITY_TYPES: frozenset[WorldEventType] = frozenset(
    et for et, cat in _EVENT_TYPE_TO_CATEGORY.items() if cat is EventCategory.BAD_ACTIVITY
)


def _utcnow_ms() -> datetime:
    """Current UTC time truncated to millisecond precision."""
    now = datetime.now(timezone.utc)
    return now.replace(microsecond=(now.microsecond // 1000) * 1000)


@dataclass
class WorldEvent:
    """A single immutable fact about something that happened in the world.

    `data` is a free-form JSON-serializable payload whose shape depends on
    `event_type` (e.g. for TASK_COMPLETED it might hold {"task": ..., 
    "credits_earned": ...}; for AGENT_DIED it might hold {"death_mode": ...,
    "final_credits": ...}).
    """

    event_type: WorldEventType
    agent_id: uuid.UUID | None
    territory_id: uuid.UUID | None
    data: dict[str, Any] = field(default_factory=dict)
    generation: int = 0
    tick: int = 0
    experiment_day: int = 0
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=_utcnow_ms)

    @property
    def category(self) -> EventCategory:
        return categorize_event(self.event_type)

    def to_dict(self) -> dict[str, Any]:
        """JSON/Supabase-ready representation."""
        return {
            "event_id": str(self.event_id),
            "timestamp": self.timestamp.isoformat(timespec="milliseconds"),
            "event_type": self.event_type.value,
            "event_category": self.category.value,
            "agent_id": str(self.agent_id) if self.agent_id else None,
            "territory_id": str(self.territory_id) if self.territory_id else None,
            "data": self.data,
            "generation": self.generation,
            "tick": self.tick,
            "experiment_day": self.experiment_day,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "WorldEvent":
        """Reconstruct a WorldEvent from a Supabase row / JSON dict."""
        return cls(
            event_id=uuid.UUID(row["event_id"]) if row.get("event_id") else uuid.uuid4(),
            timestamp=datetime.fromisoformat(row["timestamp"]) if row.get("timestamp") else _utcnow_ms(),
            event_type=WorldEventType(row["event_type"]),
            agent_id=uuid.UUID(row["agent_id"]) if row.get("agent_id") else None,
            territory_id=uuid.UUID(row["territory_id"]) if row.get("territory_id") else None,
            data=row.get("data") or {},
            generation=row.get("generation", 0),
            tick=row.get("tick", 0),
            experiment_day=row.get("experiment_day", 0),
        )
