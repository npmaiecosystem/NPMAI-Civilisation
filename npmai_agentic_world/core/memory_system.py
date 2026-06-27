"""
core/memory_system.py
=======================
The three-tier memory substrate of an AgentCell: episodic (lived
experience, local FAISS+JSON), semantic (conceptual knowledge, shared per
territory via Supabase), and genetic (compressed inheritance from a
parent, embedded at birth). AgentMemorySystem composes all three and is
the only class core/agent_cell.py needs to talk to.

Design notes
------------
* FAISS is optional. When it is not installed, EpisodicMemory degrades
  gracefully to keyword-overlap search instead of vector search, exactly
  as the spec requires ("uses FAISS vector similarity if available, else
  keyword"). Embeddings themselves are produced locally with a hashing
  trick (a real, well-established technique — see Weinberger et al.,
  "Feature Hashing for Large Scale Multitask Learning") rather than a
  network call, so EpisodicMemory has zero external dependencies and is
  fully deterministic for a given description string.
* All async event logging is fire-and-forget: AgentMemorySystem's public
  methods are synchronous (so they can be called from anywhere in the
  tick loop), but they still emit WorldEvents onto the running asyncio
  event loop when one is present, via `_fire_and_forget_log`. If no loop
  is running (e.g. unit tests), logging is silently skipped rather than
  raising — memory mutation must never fail because telemetry can't be
  delivered.
* GeneticMemory.founding_myth is generated through
  config.founding_myth.generate_agent_founding_myth, keyed off whatever
  specialization core/genome.py's Genome.infer_specialization() produced,
  so a child's first LLM context already carries a personalized myth
  consistent with the tools it actually inherited.
"""

from __future__ import annotations

import asyncio
import copy
import hashlib
import json
import math
import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

from config.constants import CREDIT_COSTS, MEMORY_LIMITS, WORLD_CONSTANTS
from config.settings import ExperimentSettings
from config.founding_myth import generate_agent_founding_myth
from data.event_logger import EventLogger
from data.event_types import WorldEvent, WorldEventType

try:
    from data.supabase_client import SupabaseClient
except ImportError:  # pragma: no cover - Supabase is optional at import time
    SupabaseClient = None  # type: ignore[assignment]

try:
    import faiss  # type: ignore
    import numpy as np  # type: ignore
    _FAISS_AVAILABLE = True
except ImportError:  # pragma: no cover - FAISS is an optional dependency
    _FAISS_AVAILABLE = False

__all__ = [
    "EpisodicNode",
    "SemanticNode",
    "EpisodicMemory",
    "SemanticMemory",
    "GeneticMemory",
    "AgentMemorySystem",
]


# ============================================================
# Module-level constants & helpers
# ============================================================

VALID_OUTCOMES = ("SUCCESS", "FAILURE", "PARTIAL")

# Local memory-tuning constants not covered by config.constants, kept here
# since they are specific to how this module scores/prunes memories.
DEFAULT_EMBEDDING_DIM = 256
PRUNE_LOW_EMOTION_AGE_DAYS = 14.0
PRUNE_LOW_EMOTION_THRESHOLD = 0.15
RECENCY_HALF_LIFE_DAYS = 30.0
CHILDHOOD_AMNESIA_FACTOR = 0.5
INHERITANCE_TOP_PERCENT_FALLBACK = 0.20  # used if MEMORY_LIMITS lacks the key
EPISODIC_MAX_MB_FALLBACK = 10.0
COMPRESS_SUMMARY_MAX_ITEMS = 8


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_dt(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    return datetime.fromisoformat(value)


def _age_days(timestamp: datetime) -> float:
    delta = _utcnow() - timestamp
    return max(delta.total_seconds() / 86400.0, 0.0)


def _recency_weight(timestamp: datetime, half_life_days: float = RECENCY_HALF_LIFE_DAYS) -> float:
    """Exponential decay: 1.0 at age 0, 0.5 at age == half_life_days."""
    age = _age_days(timestamp)
    return math.exp(-math.log(2) * age / half_life_days)


def _clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


_WORD_RE = re.compile(r"[a-z0-9]+")


def _tokenize(text: str) -> List[str]:
    return _WORD_RE.findall((text or "").lower())


def _hash_embed(text: str, dim: int = DEFAULT_EMBEDDING_DIM) -> List[float]:
    """Deterministic feature-hashing embedding with no external dependency.

    Every token hashes into one of `dim` buckets with a deterministic sign,
    producing a normalized sparse vector. This is a real, established
    technique (the "hashing trick"), not a stand-in for a missing model —
    it gives EpisodicMemory genuine vector semantics to search over even
    when no embedding API/model is configured.
    """
    vec = [0.0] * dim
    tokens = _tokenize(text)
    if not tokens:
        return vec
    for tok in tokens:
        digest = hashlib.md5(tok.encode("utf-8")).hexdigest()
        h = int(digest, 16)
        idx = h % dim
        sign = 1.0 if (h // dim) % 2 == 0 else -1.0
        vec[idx] += sign
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [v / norm for v in vec]


def _cosine(a: List[float], b: List[float]) -> float:
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a)) or 1.0
    nb = math.sqrt(sum(y * y for y in b)) or 1.0
    return dot / (na * nb)


def _vaguify(description: str) -> str:
    """Make an inherited memory's description fuzzier, for childhood amnesia."""
    if not description:
        return description
    first_sentence = re.split(r"(?<=[.!?])\s+", description.strip())[0]
    vague = re.sub(r"\d+(\.\d+)?", "some", first_sentence)
    return f"A faded early memory: {vague}".strip()


def _fire_and_forget_log(event: WorldEvent) -> None:
    """Schedule an EventLogger.log(event) call if an event loop is running.

    Memory mutation must never fail because telemetry can't be delivered,
    so the absence of a running loop (e.g. in synchronous unit tests, or
    code paths called before the world clock starts) is treated as a
    silent no-op rather than an error.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return
    loop.create_task(EventLogger().log(event))


def _episodic_max_bytes() -> int:
    mb = MEMORY_LIMITS.get("episodic_max_mb", EPISODIC_MAX_MB_FALLBACK)
    return int(mb * 1024 * 1024)


def _inheritance_top_percent() -> float:
    return float(
        MEMORY_LIMITS.get(
            "episodic_inheritance_top_pct", INHERITANCE_TOP_PERCENT_FALLBACK
        )
    )


# ============================================================
# EpisodicNode
# ============================================================

@dataclass
class EpisodicNode:
    event_id: uuid.UUID = field(default_factory=uuid.uuid4)
    timestamp: datetime = field(default_factory=_utcnow)
    event_type: str = "EXPERIENCE"
    description: str = ""
    outcome: str = "PARTIAL"
    emotional_tag: float = 0.0
    credits_delta: float = 0.0
    linked_agents: List[str] = field(default_factory=list)
    causal_parent: Optional[uuid.UUID] = None
    embedding: Optional[List[float]] = None

    def __post_init__(self) -> None:
        if self.outcome not in VALID_OUTCOMES:
            raise ValueError(
                f"EpisodicNode.outcome must be one of {VALID_OUTCOMES}, "
                f"got {self.outcome!r}"
            )
        self.emotional_tag = _clamp(float(self.emotional_tag), -1.0, 1.0)

    def to_dict(self) -> dict:
        return {
            "event_id": str(self.event_id),
            "timestamp": self.timestamp.isoformat(),
            "event_type": self.event_type,
            "description": self.description,
            "outcome": self.outcome,
            "emotional_tag": self.emotional_tag,
            "credits_delta": self.credits_delta,
            "linked_agents": list(self.linked_agents),
            "causal_parent": str(self.causal_parent) if self.causal_parent else None,
            "embedding": list(self.embedding) if self.embedding is not None else None,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "EpisodicNode":
        return cls(
            event_id=uuid.UUID(data["event_id"]) if data.get("event_id") else uuid.uuid4(),
            timestamp=_parse_dt(data.get("timestamp", _utcnow())),
            event_type=data.get("event_type", "EXPERIENCE"),
            description=data.get("description", ""),
            outcome=data.get("outcome", "PARTIAL"),
            emotional_tag=data.get("emotional_tag", 0.0),
            credits_delta=data.get("credits_delta", 0.0),
            linked_agents=list(data.get("linked_agents", [])),
            causal_parent=(
                uuid.UUID(data["causal_parent"]) if data.get("causal_parent") else None
            ),
            embedding=data.get("embedding"),
        )


# ============================================================
# SemanticNode
# ============================================================

@dataclass
class SemanticNode:
    concept: str = ""
    confidence: float = 0.5
    evidence_count: int = 0
    relations: List[Tuple[str, str, float]] = field(default_factory=list)
    learned_from: str = "self"
    last_updated: datetime = field(default_factory=_utcnow)

    def __post_init__(self) -> None:
        self.confidence = _clamp(float(self.confidence), 0.0, 1.0)
        self.evidence_count = max(0, int(self.evidence_count))

    def to_dict(self) -> dict:
        return {
            "concept": self.concept,
            "confidence": self.confidence,
            "evidence_count": self.evidence_count,
            "relations": [list(r) for r in self.relations],
            "learned_from": self.learned_from,
            "last_updated": self.last_updated.isoformat(),
        }

    @classmethod
    def from_dict(cls, data: dict) -> "SemanticNode":
        return cls(
            concept=data.get("concept", ""),
            confidence=data.get("confidence", 0.5),
            evidence_count=data.get("evidence_count", 0),
            relations=[tuple(r) for r in data.get("relations", [])],
            learned_from=data.get("learned_from", "self"),
            last_updated=_parse_dt(data.get("last_updated", _utcnow())),
        )


# ============================================================
# EpisodicMemory
# ============================================================

class EpisodicMemory:
    """Temporal, emotionally-weighted memory of an agent's own experiences."""

    def __init__(
        self,
        embedding_dim: int = DEFAULT_EMBEDDING_DIM,
        use_faiss: bool = True,
        max_size_bytes: Optional[int] = None,
    ) -> None:
        self.embedding_dim = embedding_dim
        self._use_faiss = bool(use_faiss and _FAISS_AVAILABLE)
        self.max_size_bytes = max_size_bytes or _episodic_max_bytes()

        self._nodes: Dict[uuid.UUID, EpisodicNode] = {}
        self._order: List[uuid.UUID] = []  # insertion order

        self._faiss_index = None
        self._faiss_id_order: List[uuid.UUID] = []
        if self._use_faiss:
            self._faiss_index = faiss.IndexFlatL2(self.embedding_dim)

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_experience(self, node: EpisodicNode) -> None:
        if node.embedding is None:
            node.embedding = _hash_embed(node.description, self.embedding_dim)
        self._nodes[node.event_id] = node
        self._order.append(node.event_id)
        if self._use_faiss:
            vec = np.array([node.embedding], dtype="float32")
            self._faiss_index.add(vec)
            self._faiss_id_order.append(node.event_id)

    def _remove(self, event_id: uuid.UUID) -> None:
        self._nodes.pop(event_id, None)
        if event_id in self._order:
            self._order.remove(event_id)
        if self._use_faiss:
            self._rebuild_faiss_index()

    def _rebuild_faiss_index(self) -> None:
        self._faiss_index = faiss.IndexFlatL2(self.embedding_dim)
        self._faiss_id_order = []
        for event_id in self._order:
            node = self._nodes[event_id]
            vec = np.array([node.embedding], dtype="float32")
            self._faiss_index.add(vec)
            self._faiss_id_order.append(event_id)

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_recent(self, n: int) -> List[EpisodicNode]:
        ordered = sorted(self._nodes.values(), key=lambda nd: nd.timestamp, reverse=True)
        return ordered[:n]

    def search_similar(self, query: str, n: int = 5) -> List[EpisodicNode]:
        if not self._nodes:
            return []

        if self._use_faiss:
            query_vec = np.array([_hash_embed(query, self.embedding_dim)], dtype="float32")
            k = min(n, len(self._faiss_id_order))
            if k == 0:
                return []
            _, indices = self._faiss_index.search(query_vec, k)
            results = []
            for row in indices[0]:
                if 0 <= row < len(self._faiss_id_order):
                    event_id = self._faiss_id_order[row]
                    if event_id in self._nodes:
                        results.append(self._nodes[event_id])
            return results

        # Keyword fallback: rank by token overlap between query and description.
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return self.get_recent(n)

        scored: List[Tuple[float, EpisodicNode]] = []
        for node in self._nodes.values():
            node_tokens = set(_tokenize(node.description))
            if not node_tokens:
                continue
            overlap = len(query_tokens & node_tokens)
            if overlap == 0:
                continue
            score = overlap / len(query_tokens | node_tokens)
            scored.append((score, node))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [node for _, node in scored[:n]]

    def get_high_emotion(self, threshold: float) -> List[EpisodicNode]:
        matches = [n for n in self._nodes.values() if abs(n.emotional_tag) >= threshold]
        matches.sort(key=lambda nd: abs(nd.emotional_tag), reverse=True)
        return matches

    def get_inheritance_candidates(self) -> List[EpisodicNode]:
        if not self._nodes:
            return []
        scored = sorted(
            self._nodes.values(),
            key=lambda nd: abs(nd.emotional_tag) * _recency_weight(nd.timestamp),
            reverse=True,
        )
        top_pct = _inheritance_top_percent()
        count = max(1, math.ceil(len(scored) * top_pct))
        return scored[:count]

    def compress_to_summary(self) -> str:
        if not self._nodes:
            return ""
        scored = sorted(
            self._nodes.values(),
            key=lambda nd: abs(nd.emotional_tag) * _recency_weight(nd.timestamp),
            reverse=True,
        )
        top = scored[:COMPRESS_SUMMARY_MAX_ITEMS]
        lines = [
            f"- [{nd.outcome}] {nd.description} (emotion={nd.emotional_tag:+.2f}, "
            f"credits={nd.credits_delta:+.2f})"
            for nd in top
        ]
        return "\n".join(lines)

    # ------------------------------------------------------------------
    # Maintenance
    # ------------------------------------------------------------------

    def prune(self) -> int:
        """Remove low-emotion/old memories, then enforce the size cap.

        Returns the number of nodes removed.
        """
        removed = 0

        for event_id, node in list(self._nodes.items()):
            if (
                _age_days(node.timestamp) > PRUNE_LOW_EMOTION_AGE_DAYS
                and abs(node.emotional_tag) < PRUNE_LOW_EMOTION_THRESHOLD
            ):
                self._remove(event_id)
                removed += 1

        # Enforce hard size cap by evicting the lowest-scoring remaining
        # memories one at a time until under budget.
        while self.size_bytes() > self.max_size_bytes and self._nodes:
            worst_id = min(
                self._nodes,
                key=lambda eid: abs(self._nodes[eid].emotional_tag)
                * _recency_weight(self._nodes[eid].timestamp),
            )
            self._remove(worst_id)
            removed += 1

        return removed

    def size_bytes(self) -> int:
        payload = [n.to_dict() for n in self._nodes.values()]
        return len(json.dumps(payload).encode("utf-8"))

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {
            "embedding_dim": self.embedding_dim,
            "nodes": [self._nodes[eid].to_dict() for eid in self._order],
        }

    @classmethod
    def from_dict(cls, data: dict, use_faiss: bool = True) -> "EpisodicMemory":
        mem = cls(embedding_dim=data.get("embedding_dim", DEFAULT_EMBEDDING_DIM), use_faiss=use_faiss)
        for node_data in data.get("nodes", []):
            mem.add_experience(EpisodicNode.from_dict(node_data))
        return mem

    def save(self, path: str) -> None:
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.to_dict(), fh)

    def load(self, path: str) -> None:
        with open(path, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        self._nodes.clear()
        self._order.clear()
        if self._use_faiss:
            self._faiss_index = faiss.IndexFlatL2(self.embedding_dim)
            self._faiss_id_order = []
        for node_data in data.get("nodes", []):
            self.add_experience(EpisodicNode.from_dict(node_data))


# ============================================================
# SemanticMemory
# ============================================================

class SemanticMemory:
    """Conceptual hypergraph of confidence-scored beliefs and relations.

    Naming convention used by get_territory_knowledge/get_agent_knowledge:
    concepts about a specific territory are named "territory:<slug>:...",
    and concepts about a specific other agent are named "agent:<id>:...".
    Any other concept is treated as general/self knowledge.
    """

    def __init__(self) -> None:
        self.concepts: Dict[str, SemanticNode] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def add_concept(self, node: SemanticNode) -> None:
        self.concepts[node.concept] = node

    def update_confidence(self, concept: str, outcome: str) -> SemanticNode:
        if outcome not in VALID_OUTCOMES:
            raise ValueError(f"outcome must be one of {VALID_OUTCOMES}, got {outcome!r}")

        node = self.concepts.get(concept)
        if node is None:
            node = SemanticNode(concept=concept, confidence=0.5, evidence_count=0)
            self.concepts[concept] = node

        # Beta-Bernoulli style conjugate update. A weak Beta(1,1) prior is
        # folded in via the "+2" pseudo-count so a brand new concept starts
        # at confidence 0.5 and is not overconfident after a single sample.
        alpha = node.confidence * (node.evidence_count + 2)
        beta = (1.0 - node.confidence) * (node.evidence_count + 2)

        if outcome == "SUCCESS":
            alpha += 1.0
        elif outcome == "FAILURE":
            beta += 1.0
        else:  # PARTIAL
            alpha += 0.5
            beta += 0.5

        node.confidence = _clamp(alpha / (alpha + beta), 0.01, 0.99)
        node.evidence_count += 1
        node.last_updated = _utcnow()
        return node

    def add_relation(
        self, concept1: str, concept2: str, relation_type: str, confidence: float
    ) -> None:
        node = self.concepts.get(concept1)
        if node is None:
            node = SemanticNode(concept=concept1, confidence=0.5, evidence_count=0)
            self.concepts[concept1] = node

        relation = (concept2, relation_type, _clamp(float(confidence), 0.0, 1.0))
        existing = [(c, r) for c, r, _ in node.relations]
        if (concept2, relation_type) not in existing:
            node.relations.append(relation)
        node.last_updated = _utcnow()

    def merge_from(self, other_semantic: "SemanticMemory", trust_weight: float) -> int:
        """Merge another agent's semantic memory into this one.

        Returns the number of concepts touched (added or updated).
        """
        trust_weight = _clamp(float(trust_weight), 0.0, 1.0)
        touched = 0

        for concept, incoming in other_semantic.concepts.items():
            existing = self.concepts.get(concept)
            if existing is None:
                blended_confidence = 0.5 + trust_weight * (incoming.confidence - 0.5)
                self.concepts[concept] = SemanticNode(
                    concept=concept,
                    confidence=_clamp(blended_confidence, 0.0, 1.0),
                    evidence_count=max(1, round(incoming.evidence_count * trust_weight)),
                    relations=list(incoming.relations),
                    learned_from=incoming.learned_from if incoming.learned_from != "self" else "peer",
                    last_updated=_utcnow(),
                )
                touched += 1
                continue

            total_weight = existing.evidence_count + incoming.evidence_count * trust_weight
            if total_weight > 0:
                existing.confidence = _clamp(
                    (
                        existing.confidence * existing.evidence_count
                        + incoming.confidence * incoming.evidence_count * trust_weight
                    )
                    / total_weight,
                    0.0,
                    1.0,
                )
            existing.evidence_count += max(0, round(incoming.evidence_count * trust_weight))
            existing_relation_keys = {(c, r) for c, r, _ in existing.relations}
            for rel in incoming.relations:
                if (rel[0], rel[1]) not in existing_relation_keys:
                    existing.relations.append(rel)
                    existing_relation_keys.add((rel[0], rel[1]))
            existing.last_updated = _utcnow()
            touched += 1

        return touched

    # ------------------------------------------------------------------
    # Queries
    # ------------------------------------------------------------------

    def get_concept(self, concept: str) -> Optional[SemanticNode]:
        return self.concepts.get(concept)

    def search(self, query: str) -> List[SemanticNode]:
        query_tokens = set(_tokenize(query))
        if not query_tokens:
            return []

        scored: List[Tuple[float, SemanticNode]] = []
        for node in self.concepts.values():
            haystack = node.concept + " " + " ".join(r[0] for r in node.relations)
            node_tokens = set(_tokenize(haystack))
            overlap = len(query_tokens & node_tokens)
            if overlap == 0:
                continue
            score = overlap / len(query_tokens | node_tokens) * (0.5 + node.confidence)
            scored.append((score, node))
        scored.sort(key=lambda pair: pair[0], reverse=True)
        return [node for _, node in scored]

    def export_subset(self, concepts: List[str]) -> "SemanticMemory":
        subset = SemanticMemory()
        for concept in concepts:
            node = self.concepts.get(concept)
            if node is not None:
                subset.add_concept(copy.deepcopy(node))
        return subset

    def get_territory_knowledge(self) -> List[SemanticNode]:
        return [n for c, n in self.concepts.items() if c.startswith("territory:")]

    def get_agent_knowledge(self) -> List[SemanticNode]:
        return [n for c, n in self.concepts.items() if c.startswith("agent:")]

    # ------------------------------------------------------------------
    # Serialization
    # ------------------------------------------------------------------

    def to_dict(self) -> dict:
        return {"concepts": [n.to_dict() for n in self.concepts.values()]}

    @classmethod
    def from_dict(cls, data: dict) -> "SemanticMemory":
        mem = cls()
        for node_data in data.get("concepts", []):
            mem.add_concept(SemanticNode.from_dict(node_data))
        return mem

    def save_to_supabase(self, agent_id: str, territory_id: Optional[str]) -> bool:
        """Upsert every concept this agent holds into the shared territory
        semantic-memory table. No-ops (returns False) if Supabase isn't
        wired up, rather than raising — semantic memory must keep working
        locally even when the shared layer is unavailable.
        """
        if SupabaseClient is None:
            return False
        try:
            client = SupabaseClient()
            rows = [
                {
                    "agent_id": agent_id,
                    "territory_id": territory_id,
                    "concept": node.concept,
                    "confidence": node.confidence,
                    "evidence_count": node.evidence_count,
                    "relations": [list(r) for r in node.relations],
                    "learned_from": node.learned_from,
                    "last_updated": node.last_updated.isoformat(),
                }
                for node in self.concepts.values()
            ]
            if rows:
                client.table("semantic_memory").upsert(rows).execute()
            return True
        except Exception:
            return False

    def load_from_supabase(self, agent_id: str) -> bool:
        if SupabaseClient is None:
            return False
        try:
            client = SupabaseClient()
            response = client.table("semantic_memory").select("*").eq("agent_id", agent_id).execute()
            for row in getattr(response, "data", []) or []:
                self.add_concept(
                    SemanticNode(
                        concept=row["concept"],
                        confidence=row.get("confidence", 0.5),
                        evidence_count=row.get("evidence_count", 0),
                        relations=[tuple(r) for r in row.get("relations", [])],
                        learned_from=row.get("learned_from", "self"),
                        last_updated=_parse_dt(row.get("last_updated", _utcnow())),
                    )
                )
            return True
        except Exception:
            return False


# ============================================================
# GeneticMemory
# ============================================================

@dataclass
class GeneticMemory:
    inherited_episodes: List[EpisodicNode] = field(default_factory=list)
    inherited_semantic_nodes: List[SemanticNode] = field(default_factory=list)
    founding_myth: str = ""
    lineage_summary: str = ""

    def apply_childhood_amnesia(self) -> None:
        """Make inherited episodic memories fuzzy: halve emotional charge
        and vague-ify descriptions, so a child "remembers" its parent's
        life as an indistinct early instinct rather than a crisp event log.
        """
        for node in self.inherited_episodes:
            node.emotional_tag = _clamp(node.emotional_tag * CHILDHOOD_AMNESIA_FACTOR, -1.0, 1.0)
            node.description = _vaguify(node.description)

    def to_dict(self) -> dict:
        return {
            "inherited_episodes": [n.to_dict() for n in self.inherited_episodes],
            "inherited_semantic_nodes": [n.to_dict() for n in self.inherited_semantic_nodes],
            "founding_myth": self.founding_myth,
            "lineage_summary": self.lineage_summary,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "GeneticMemory":
        return cls(
            inherited_episodes=[EpisodicNode.from_dict(d) for d in data.get("inherited_episodes", [])],
            inherited_semantic_nodes=[
                SemanticNode.from_dict(d) for d in data.get("inherited_semantic_nodes", [])
            ],
            founding_myth=data.get("founding_myth", ""),
            lineage_summary=data.get("lineage_summary", ""),
        )


# ============================================================
# AgentMemorySystem
# ============================================================

class AgentMemorySystem:
    """Composes episodic, semantic, and genetic memory for one agent."""

    def __init__(
        self,
        agent_id: str,
        settings: Optional[ExperimentSettings] = None,
    ) -> None:
        self.agent_id = agent_id
        self.settings = settings or ExperimentSettings()

        embedding_dim = getattr(self.settings, "embedding_dim", DEFAULT_EMBEDDING_DIM)
        use_faiss = getattr(self.settings, "use_faiss", True)

        self.episodic = EpisodicMemory(embedding_dim=embedding_dim, use_faiss=use_faiss)
        self.semantic = SemanticMemory()
        self.genetic = GeneticMemory()

        self._tick_count = 0
        self._prune_interval = getattr(self.settings, "prune_interval_ticks", 25)
        self._sync_interval = getattr(self.settings, "memory_sync_interval_ticks", 50)

    # ------------------------------------------------------------------
    # Recording & learning
    # ------------------------------------------------------------------

    def record_experience(
        self,
        description: str,
        outcome: str,
        emotional_tag: float,
        credits_delta: float,
        linked_agents: Optional[List[str]] = None,
        causal_parent: Optional[uuid.UUID] = None,
    ) -> EpisodicNode:
        node = EpisodicNode(
            event_type="EXPERIENCE",
            description=description,
            outcome=outcome,
            emotional_tag=emotional_tag,
            credits_delta=credits_delta,
            linked_agents=list(linked_agents or []),
            causal_parent=causal_parent,
        )
        self.episodic.add_experience(node)
        _fire_and_forget_log(
            WorldEvent(
                event_type=WorldEventType.MEMORY_EXPERIENCE_RECORDED,
                agent_id=self.agent_id,
                data={
                    "event_id": str(node.event_id),
                    "outcome": outcome,
                    "emotional_tag": emotional_tag,
                    "credits_delta": credits_delta,
                },
            )
        )
        return node

    def learn_from_outcome(self, task: str, outcome: str, tool_used: str) -> SemanticNode:
        concept = f"skill:{tool_used}"
        node = self.semantic.update_confidence(concept, outcome)
        node.learned_from = node.learned_from or "self"
        self.semantic.add_relation(concept, task, "APPLIED_TO", confidence=node.confidence)
        _fire_and_forget_log(
            WorldEvent(
                event_type=WorldEventType.SEMANTIC_CONCEPT_LEARNED,
                agent_id=self.agent_id,
                data={"concept": concept, "outcome": outcome, "confidence": node.confidence},
            )
        )
        return node

    # ------------------------------------------------------------------
    # Planner context
    # ------------------------------------------------------------------

    def get_context_for_planner(self, current_task: str) -> str:
        sections: List[str] = []

        if self.genetic.founding_myth:
            sections.append(
                "=== INHERITED FOUNDING MYTH (abridged) ===\n"
                + self.genetic.founding_myth[:600]
            )
        if self.genetic.lineage_summary:
            sections.append("=== LINEAGE ===\n" + self.genetic.lineage_summary)

        episodic_summary = self.episodic.compress_to_summary()
        if episodic_summary:
            sections.append("=== SIGNIFICANT MEMORIES ===\n" + episodic_summary)

        similar = self.episodic.search_similar(current_task, n=3)
        if similar:
            lines = "\n".join(f"- [{n.outcome}] {n.description}" for n in similar)
            sections.append("=== SIMILAR PAST EXPERIENCES ===\n" + lines)

        relevant_concepts = self.semantic.search(current_task)
        if relevant_concepts:
            lines = "\n".join(
                f"- {n.concept} (confidence={n.confidence:.2f}, evidence={n.evidence_count})"
                for n in relevant_concepts[:6]
            )
            sections.append("=== RELEVANT KNOWLEDGE ===\n" + lines)

        if not sections:
            return "No prior memory relevant to this task."
        return "\n\n".join(sections)

    # ------------------------------------------------------------------
    # Inheritance
    # ------------------------------------------------------------------

    def _compose_lineage_summary(self) -> str:
        prior = self.genetic.lineage_summary
        standout = self.episodic.get_high_emotion(0.5)[:3]
        if standout:
            highlight = "; ".join(n.description for n in standout)
        else:
            highlight = "no especially significant events"
        addition = f"Agent {self.agent_id} lived through: {highlight}."
        return f"{prior} {addition}".strip() if prior else addition

    def prepare_inheritance_package(self) -> dict:
        candidates = self.episodic.get_inheritance_candidates()
        top_concepts = sorted(
            self.semantic.concepts.values(),
            key=lambda n: n.confidence * (n.evidence_count + 1),
            reverse=True,
        )[:20]

        package = {
            "parent_agent_id": self.agent_id,
            "episodes": [n.to_dict() for n in candidates],
            "semantic_nodes": [n.to_dict() for n in top_concepts],
            "lineage_summary": self._compose_lineage_summary(),
        }
        _fire_and_forget_log(
            WorldEvent(
                event_type=WorldEventType.MEMORY_INHERITED,
                agent_id=self.agent_id,
                data={"episodes_passed": len(candidates), "concepts_passed": len(top_concepts)},
            )
        )
        return package

    def receive_inheritance(
        self,
        package: dict,
        territory_name: str = "an unnamed territory",
        generation: int = 1,
        agent_name: Optional[str] = None,
        specialization: Optional[str] = None,
    ) -> None:
        """Apply a parent's inheritance package to this (child) memory system.

        `territory_name`, `generation`, `agent_name`, and `specialization`
        are optional and only affect the personalized founding myth text;
        the core inheritance (episodes/semantic nodes/lineage) is applied
        from `package` alone.
        """
        self.genetic.inherited_episodes = [
            EpisodicNode.from_dict(d) for d in package.get("episodes", [])
        ]
        self.genetic.inherited_semantic_nodes = [
            SemanticNode.from_dict(d) for d in package.get("semantic_nodes", [])
        ]
        self.genetic.lineage_summary = package.get("lineage_summary", "")
        self.genetic.apply_childhood_amnesia()

        peer_semantic = SemanticMemory()
        for node in self.genetic.inherited_semantic_nodes:
            peer_semantic.add_concept(node)
        self.semantic.merge_from(peer_semantic, trust_weight=0.8)

        self.genetic.founding_myth = generate_agent_founding_myth(
            agent_id=self.agent_id,
            territory_name=territory_name,
            generation=generation,
            agent_name=agent_name,
            specialization=specialization,
        )

    def set_genesis_founding_myth(
        self,
        territory_name: str,
        agent_name: Optional[str] = None,
        specialization: Optional[str] = None,
    ) -> None:
        """Convenience for generation-0 (founder) agents, which have no
        parent package to inherit but still need a founding myth.
        """
        self.genetic.founding_myth = generate_agent_founding_myth(
            agent_id=self.agent_id,
            territory_name=territory_name,
            generation=0,
            agent_name=agent_name,
            specialization=specialization,
        )

    # ------------------------------------------------------------------
    # World clock integration
    # ------------------------------------------------------------------

    def tick(self) -> None:
        self._tick_count += 1

        if self._prune_interval and self._tick_count % self._prune_interval == 0:
            removed = self.episodic.prune()
            if removed:
                _fire_and_forget_log(
                    WorldEvent(
                        event_type=WorldEventType.MEMORY_PRUNED,
                        agent_id=self.agent_id,
                        data={"removed": removed, "size_bytes": self.episodic.size_bytes()},
                    )
                )

        if self._sync_interval and self._tick_count % self._sync_interval == 0:
            self.semantic.save_to_supabase(agent_id=self.agent_id, territory_id=None)

    # ------------------------------------------------------------------
    # Persistence
    # ------------------------------------------------------------------

    def _episodic_path(self, agent_id: str) -> str:
        base_dir = os.environ.get("NPMAI_MEMORY_DIR", "/tmp/npmai_memory")
        return os.path.join(base_dir, f"{agent_id}_episodic.json")

    def full_save(self, agent_id: str, territory_id: Optional[str]) -> None:
        self.episodic.save(self._episodic_path(agent_id))
        self.semantic.save_to_supabase(agent_id, territory_id)

    def full_load(self, agent_id: str, territory_id: Optional[str]) -> None:
        path = self._episodic_path(agent_id)
        if os.path.exists(path):
            self.episodic.load(path)
        self.semantic.load_from_supabase(agent_id)
