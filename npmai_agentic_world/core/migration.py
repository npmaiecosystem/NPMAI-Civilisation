"""
core/migration.py
==================
MigrationProtocol: the 4-phase process by which an AgentCell relocates
from one territory to another (reconnaissance -> preparation ->
displacement -> integration), plus TerritoryScanner, the discovery layer
it leans on in phase 1.

=== Assumed interfaces from Sessions 1-3 ===
AgentCell — same contract as documented at the top of core/reproduction.py,
plus:
    .relationships: dict[str, float]
    .divine_favor: float
    A territory-side governance hook (duck-typed, all optional):
        territory.laws -> list of law-like objects/dicts, each exposing
        either `.banned_specializations` / `["banned_specializations"]`
        (list[str]) or `.text` / `["text"]` (free text scanned for
        "no <specialization>" patterns as a last-resort heuristic).

Territory (world/territory.py; duck-typed, dict or object):
    .territory_id, .name, .host, .resources (dict: cpu, ram, capacity,
    credit_pool), .population (list of agent-like objects/ids),
    .laws (list), .border_policy (config.constants.BorderPolicy)

Since this simulation has no literal sockets between territories —
territories are rows/objects the world controller already holds —
"network scanning" here means querying the territory registry the caller
passes in (`world_territories`) and, for phase-2+ cloud territories,
querying Supabase's `territory_states` table for territories whose most
recent snapshot indicates a non-local host. An agent's own capability
chromosome still gates *how much* it can see (see
`_agent_network_visibility_factor`), which is the in-universe stand-in for
"scans via a network-capable tool" without inventing a tool class that
isn't in npmai_agents' actual 100-class registry.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from config.constants import *  # noqa: F401,F403  (BorderPolicy, MEMORY_LIMITS, CREDIT_COSTS, WORLD_CONSTANTS)
from config.settings import ExperimentSettings
from data.event_logger import EventLogger
from data.event_types import WorldEvent, WorldEventType
from data.supabase_client import SupabaseClient, SupabaseConnectionError

logger = logging.getLogger("npmai_world.migration")


def _attr(obj: Any, name: str, default: Any = None) -> Any:
    """Read `name` off `obj` whether it's a real object or dict-like."""
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


# Tool clusters (see core.genome.TOOL_SPECIALIZATION_MAP) treated as the
# in-universe equivalent of "has a network-scanning capability" for
# reconnaissance visibility purposes.
_NETWORK_CAPABLE_SPECIALIZATIONS = frozenset({"devops", "system", "media", "security"})


class TerritoryScanner:
    """Discovery layer over the territory registry. No literal sockets —
    "local network" means the in-memory/world-controller-held registry of
    territories; "cloud endpoints" means territories whose latest Supabase
    snapshot indicates a non-local host (Phase 2+ deployments)."""

    def __init__(self) -> None:
        self._known_territory_ids: set[str] = set()
        self._monitor_task = None

    def scan_local_network(self, world_territories: dict[str, Any]) -> list[dict[str, Any]]:
        """Return a lightweight summary dict per territory the world
        controller currently knows about."""
        results = []
        for territory_id, territory in (world_territories or {}).items():
            results.append(
                {
                    "territory_id": territory_id,
                    "name": _attr(territory, "name", str(territory_id)),
                    "host": _attr(territory, "host"),
                    "border_policy": str(_attr(territory, "border_policy", BorderPolicy.OPEN)),
                    "population_count": len(_attr(territory, "population", []) or []),
                    "resources": _attr(territory, "resources", {}) or {},
                }
            )
        return results

    async def scan_cloud_endpoints(self) -> list[dict[str, Any]]:
        """Query Supabase for the latest snapshot of every territory whose
        host doesn't look like a local/loopback address — i.e. territories
        running on remote infrastructure (Phase 2+ multi-host deployments).
        Returns [] gracefully if Supabase isn't reachable rather than
        raising, since reconnaissance should degrade, not crash, offline."""
        try:
            client = await SupabaseClient.get_instance()
            if not client.is_connected:
                return []
            rows = await client.query("territory_states", order_by="captured_at", limit=500)
        except SupabaseConnectionError:
            logger.warning("scan_cloud_endpoints: Supabase unreachable; returning no cloud territories")
            return []

        latest_by_territory: dict[str, dict[str, Any]] = {}
        for row in rows:
            tid = row.get("territory_id")
            if tid and tid not in latest_by_territory:
                latest_by_territory[tid] = row  # rows are already newest-first

        cloud_territories = []
        for tid, row in latest_by_territory.items():
            host = (row.get("host") or "").lower()
            is_local = host in ("", "localhost", "127.0.0.1") or host.startswith("192.168.") or host.startswith("10.")
            if not is_local:
                cloud_territories.append(row)
        return cloud_territories

    async def get_territory_health(self, territory_id: str) -> dict[str, Any]:
        """Cheap health summary for a single territory from its latest
        snapshot: population pressure, resource availability, recency."""
        try:
            client = await SupabaseClient.get_instance()
            if not client.is_connected:
                return {"territory_id": territory_id, "status": "UNKNOWN", "reason": "Supabase unreachable"}
            rows = await client.query(
                "territory_states", filters={"territory_id": territory_id}, order_by="captured_at", limit=1
            )
        except SupabaseConnectionError:
            return {"territory_id": territory_id, "status": "UNKNOWN", "reason": "Supabase unreachable"}

        if not rows:
            return {"territory_id": territory_id, "status": "UNKNOWN", "reason": "no snapshot on record"}

        snapshot = rows[0]
        resources = snapshot.get("resources") or {}
        capacity = resources.get("capacity") or 0
        population = snapshot.get("population_count", 0)
        density = (population / capacity) if capacity else 1.0
        status = "HEALTHY" if density < 0.7 else ("STRAINED" if density < 1.0 else "OVERLOADED")
        return {
            "territory_id": territory_id,
            "status": status,
            "population_density": density,
            "captured_at": snapshot.get("captured_at"),
            "resources": resources,
        }

    async def monitor_territory_changes(self, callback, world_territories: dict[str, Any], poll_seconds: float = 30.0):
        """Single-pass change detection helper: compares the territory IDs
        seen this call against the previous call and invokes `callback`
        with each newly-discovered territory's summary dict.

        Designed to be invoked repeatedly from the world tick loop (rather
        than spinning up its own infinite loop here), so the caller stays
        in control of cadence and shutdown.
        """
        current = self.scan_local_network(world_territories)
        current_ids = {t["territory_id"] for t in current}
        new_ids = current_ids - self._known_territory_ids
        self._known_territory_ids = current_ids

        for territory_summary in current:
            if territory_summary["territory_id"] in new_ids:
                try:
                    callback(territory_summary)
                except Exception:  # noqa: BLE001
                    logger.exception("monitor_territory_changes callback raised an exception")
        return list(new_ids)


class MigrationProtocol:
    """Orchestrates the full 4-phase migration of one AgentCell from its
    current territory to a target territory."""

    def __init__(self, scanner: Optional[TerritoryScanner] = None, kill_original_on_displacement: bool = False) -> None:
        self.scanner = scanner or TerritoryScanner()
        # If False (default), the original agent enters "diaspora" mode
        # (status MIGRATING is cleared, a diaspora flag is set) rather than
        # dying outright on successful displacement — matching the brief's
        # "enters diaspora mode OR dies (configurable)".
        self.kill_original_on_displacement = kill_original_on_displacement

    # -- public entry point -----------------------------------------------

    async def initiate_migration(
        self,
        agent: Any,
        target_territory_id: Optional[str],
        world_territories: dict[str, Any],
    ) -> bool:
        event_logger = await EventLogger.get_instance()
        source_territory_id = _attr(agent, "territory_id")

        # --- Phase 1: Reconnaissance ---
        await event_logger.log(
            WorldEvent(
                event_type=WorldEventType.MIGRATION_RECONNAISSANCE,
                agent_id=_attr(agent, "agent_id"),
                territory_id=source_territory_id,
                data={"phase_status": "STARTED"},
            )
        )

        candidates = self.scanner.scan_local_network(world_territories)
        evaluations: dict[str, dict[str, Any]] = {}
        for candidate in candidates:
            tid = candidate["territory_id"]
            if tid == str(source_territory_id):
                continue
            territory_obj = world_territories.get(tid)
            evaluations[tid] = await self.evaluate_territory(territory_obj, agent)

        if target_territory_id is None:
            target_territory_id = self._select_best_target(evaluations)

        await event_logger.log(
            WorldEvent(
                event_type=WorldEventType.MIGRATION_RECONNAISSANCE,
                agent_id=_attr(agent, "agent_id"),
                territory_id=source_territory_id,
                data={
                    "phase_status": "COMPLETED",
                    "candidates_evaluated": len(evaluations),
                    "selected_target": target_territory_id,
                    "evaluations": evaluations,
                },
            )
        )

        if target_territory_id is None or target_territory_id not in world_territories:
            logger.info("Agent %s found no viable migration target; aborting", _attr(agent, "agent_id"))
            await event_logger.log(
                WorldEvent(
                    event_type=WorldEventType.MIGRATION_FAILED,
                    agent_id=_attr(agent, "agent_id"),
                    territory_id=source_territory_id,
                    data={"reason": "no_viable_target"},
                )
            )
            return False

        target_territory = world_territories[target_territory_id]

        # --- Phase 2: Preparation ---
        migration_package = await self.compress_migration_package(agent)
        migration_cost = self._calculate_migration_cost(migration_package)

        if _attr(agent, "credits", 0.0) < migration_cost:
            logger.info(
                "Agent %s cannot afford migration (needs %.2f, has %.2f)",
                _attr(agent, "agent_id"), migration_cost, _attr(agent, "credits", 0.0),
            )
            await event_logger.log(
                WorldEvent(
                    event_type=WorldEventType.MIGRATION_FAILED,
                    agent_id=_attr(agent, "agent_id"),
                    territory_id=source_territory_id,
                    data={"reason": "insufficient_credits", "migration_cost": migration_cost},
                )
            )
            return False

        self._set_status(agent, AgentStatus.MIGRATING)
        await event_logger.log(
            WorldEvent(
                event_type=WorldEventType.MIGRATION_PREPARATION,
                agent_id=_attr(agent, "agent_id"),
                territory_id=source_territory_id,
                data={
                    "target_territory_id": target_territory_id,
                    "migration_cost": migration_cost,
                    "package_size_bytes": migration_package.get("size_bytes", 0),
                },
            )
        )

        # --- Phase 3: Displacement ---
        accepted = await self.negotiate_border_entry(agent, target_territory)
        if not accepted:
            self._set_status(agent, AgentStatus.ACTIVE)  # agent stays, unharmed
            await event_logger.log(
                WorldEvent(
                    event_type=WorldEventType.MIGRATION_FAILED,
                    agent_id=_attr(agent, "agent_id"),
                    territory_id=source_territory_id,
                    data={"reason": "REJECTED", "target_territory_id": target_territory_id},
                )
            )
            return False

        self._deduct_credits(agent, migration_cost)
        new_agent = await self._spawn_migrated_agent_cell(agent, migration_package, target_territory_id)

        if self.kill_original_on_displacement:
            self._set_status(agent, AgentStatus.DEAD)
        else:
            self._set_status(agent, AgentStatus.MIGRATING)  # diaspora: original persists in a transitional state
            if not isinstance(agent, dict):
                setattr(agent, "diaspora", True)

        await event_logger.log(
            WorldEvent(
                event_type=WorldEventType.MIGRATION_DISPLACEMENT,
                agent_id=_attr(agent, "agent_id"),
                territory_id=source_territory_id,
                data={
                    "target_territory_id": target_territory_id,
                    "new_agent_id": str(_attr(new_agent, "agent_id")),
                    "original_killed": self.kill_original_on_displacement,
                },
            )
        )

        # --- Phase 4: Integration ---
        await self._integrate(new_agent, target_territory, migration_package, event_logger)

        return True

    # -- Phase 1 helper ------------------------------------------------

    async def evaluate_territory(self, territory: Any, agent: Any) -> dict[str, Any]:
        if territory is None:
            return {
                "resource_score": 0.0, "population_density": 1.0, "hostility_score": 1.0,
                "compatibility_score": 0.0, "migration_cost": float("inf"), "recommendation": "AVOID",
            }

        resources = _attr(territory, "resources", {}) or {}
        capacity = resources.get("capacity") or 1
        cpu = resources.get("cpu", 0)
        ram = resources.get("ram", 0)
        credit_pool = resources.get("credit_pool", 0)
        resource_score = min(1.0, (cpu + ram + credit_pool) / max(capacity, 1) / 3.0)

        population = _attr(territory, "population", []) or []
        population_density = len(population) / max(capacity, 1)

        hostility_score = await self._estimate_hostility(territory)

        agent_specialization = _attr(_attr(agent, "genome"), "specialization", "default")
        compatibility_score = self._compatibility_score(territory, agent_specialization)

        migration_package_estimate = await self.compress_migration_package(agent)
        migration_cost = self._calculate_migration_cost(migration_package_estimate)

        visibility = self._agent_network_visibility_factor(agent)
        # Low-visibility agents see a noisier (more conservative) picture of
        # remote territories — their effective resource_score is dampened.
        resource_score *= visibility

        score = resource_score * 0.4 + compatibility_score * 0.3 - hostility_score * 0.2 - population_density * 0.1
        if score >= 0.5 and hostility_score < 0.6:
            recommendation = "MIGRATE"
        elif score >= 0.2:
            recommendation = "CONSIDER"
        else:
            recommendation = "AVOID"

        return {
            "resource_score": round(resource_score, 3),
            "population_density": round(population_density, 3),
            "hostility_score": round(hostility_score, 3),
            "compatibility_score": round(compatibility_score, 3),
            "migration_cost": round(migration_cost, 3),
            "recommendation": recommendation,
        }

    @staticmethod
    def _agent_network_visibility_factor(agent: Any) -> float:
        specialization = _attr(_attr(agent, "genome"), "specialization", "default")
        return 1.0 if specialization in _NETWORK_CAPABLE_SPECIALIZATIONS else 0.6

    @staticmethod
    async def _estimate_hostility(territory: Any) -> float:
        """Hostility proxy: fraction of recent territory law-related events
        that were enforcement/execution actions, sourced from world_events.
        Falls back to a neutral 0.3 if Supabase is unreachable or there's
        no history yet."""
        territory_id = _attr(territory, "territory_id")
        try:
            client = await SupabaseClient.get_instance()
            if not client.is_connected or territory_id is None:
                return 0.3
            rows = await client.query(
                "world_events", filters={"territory_id": str(territory_id)}, order_by="timestamp", limit=200
            )
        except SupabaseConnectionError:
            return 0.3

        if not rows:
            return 0.3
        hostile_types = {
            WorldEventType.LAW_ENFORCED.value,
            WorldEventType.EXECUTION_VOTE_PASSED.value,
            WorldEventType.LAW_VIOLATION.value,
        }
        hostile_count = sum(1 for r in rows if r.get("event_type") in hostile_types)
        return min(1.0, hostile_count / len(rows) * 3.0)  # scaled so a handful of hostile events register clearly

    @staticmethod
    def _compatibility_score(territory: Any, agent_specialization: str) -> float:
        """How well an agent's specialization matches what a territory
        seems to need, inferred from the specializations *under-represented*
        in its current population (duck-typed: population members may or
        may not expose `.genome.specialization`)."""
        population = _attr(territory, "population", []) or []
        specializations = [
            _attr(_attr(p, "genome"), "specialization") for p in population if _attr(_attr(p, "genome"), "specialization")
        ]
        if not specializations:
            return 0.5  # no data either way
        representation = specializations.count(agent_specialization) / len(specializations)
        # Less-represented specializations are more valuable to a territory.
        return max(0.0, 1.0 - representation)

    def _select_best_target(self, evaluations: dict[str, dict[str, Any]]) -> Optional[str]:
        migrate_candidates = {tid: e for tid, e in evaluations.items() if e["recommendation"] == "MIGRATE"}
        pool = migrate_candidates or {
            tid: e for tid, e in evaluations.items() if e["recommendation"] == "CONSIDER"
        }
        if not pool:
            return None
        best_tid = max(pool, key=lambda tid: pool[tid]["resource_score"] + pool[tid]["compatibility_score"])
        return best_tid

    # -- Phase 2 helper ----------------------------------------------------

    async def compress_migration_package(self, agent: Any) -> dict[str, Any]:
        memory = _attr(agent, "memory")
        episodic_payload: list[Any] = []
        semantic_payload: Any = None

        if memory is not None:
            episodic = getattr(memory, "episodic", None)
            if episodic is not None and hasattr(episodic, "get_inheritance_candidates"):
                try:
                    # Top 30% by emotion, per brief -- reuse the episodic
                    # memory's own emotion-ranked candidate list and take
                    # the top 30% slice of it rather than its (20%)
                    # inheritance default.
                    all_recent = episodic.get_recent(10_000) if hasattr(episodic, "get_recent") else []
                    ranked = sorted(all_recent, key=lambda n: abs(getattr(n, "emotional_tag", 0.0)), reverse=True)
                    cutoff = max(1, int(len(ranked) * 0.30)) if ranked else 0
                    top_nodes = ranked[:cutoff]
                    episodic_payload = [n.to_dict() if hasattr(n, "to_dict") else n for n in top_nodes]
                except Exception:  # noqa: BLE001
                    logger.exception("Failed compressing episodic memory for migration; sending empty episodic payload")

            semantic = getattr(memory, "semantic", None)
            if semantic is not None:
                try:
                    if hasattr(semantic, "to_dict"):
                        semantic_payload = semantic.to_dict()
                    elif hasattr(semantic, "get_territory_knowledge"):
                        nodes = semantic.get_territory_knowledge()
                        semantic_payload = [n.to_dict() if hasattr(n, "to_dict") else n for n in nodes]
                except Exception:  # noqa: BLE001
                    logger.exception("Failed exporting full semantic memory for migration")

        genome = _attr(agent, "genome")
        genome_payload = genome.to_dict() if genome is not None and hasattr(genome, "to_dict") else genome

        package = {
            "agent_id": str(_attr(agent, "agent_id")),
            "episodic_memory": episodic_payload,
            "semantic_memory": semantic_payload,
            "genome": genome_payload,
            "relationships": dict(_attr(agent, "relationships", {}) or {}),
            "divine_favor": _attr(agent, "divine_favor", 0.0),
            "reputation": _attr(agent, "reputation", 0.0),
            "generation": _attr(agent, "generation", 0),
            "lineage_id": str(_attr(agent, "lineage_id")) if _attr(agent, "lineage_id") else None,
            "packaged_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
        }
        package["size_bytes"] = len(str(package).encode("utf-8"))
        return package

    @staticmethod
    def _calculate_migration_cost(migration_package: dict[str, Any]) -> float:
        size_mb = migration_package.get("size_bytes", 0) / (1024 * 1024)
        base = abs(CREDIT_COSTS["migrate_base"])
        per_mb = abs(CREDIT_COSTS["migrate_per_mb_memory"])
        return base + per_mb * size_mb

    # -- Phase 3 helper -------------------------------------------------

    async def negotiate_border_entry(self, agent: Any, target_territory: Any) -> bool:
        policy = _attr(target_territory, "border_policy", BorderPolicy.OPEN)
        policy_value = policy.value if hasattr(policy, "value") else str(policy)

        if policy_value == BorderPolicy.CLOSED.value:
            logger.info("Territory %s is CLOSED; rejecting agent %s", _attr(target_territory, "territory_id"), _attr(agent, "agent_id"))
            return False

        if policy_value == BorderPolicy.OPEN.value:
            return True

        # RESTRICTED: evaluate reputation/credits/specialization/lineage
        # against the territory's current laws.
        reputation = _attr(agent, "reputation", 0.0) or 0.0
        credits = _attr(agent, "credits", 0.0) or 0.0
        specialization = _attr(_attr(agent, "genome"), "specialization", "default")

        if self._specialization_is_banned(target_territory, specialization):
            logger.info(
                "Agent %s (specialization=%s) rejected by territory %s law banning that specialization",
                _attr(agent, "agent_id"), specialization, _attr(target_territory, "territory_id"),
            )
            return False

        min_reputation = 0.0  # a generous default; territories with no explicit law admit anyone non-banned
        laws = _attr(target_territory, "laws", []) or []
        for law in laws:
            min_rep_law = _attr(law, "min_reputation_required")
            if min_rep_law is not None:
                min_reputation = max(min_reputation, float(min_rep_law))

        if reputation < min_reputation:
            logger.info(
                "Agent %s reputation %.2f below territory %s's required %.2f",
                _attr(agent, "agent_id"), reputation, _attr(target_territory, "territory_id"), min_reputation,
            )
            return False

        # Credits act as a basic "can sustain itself here" signal under
        # restricted entry; require at least the territory's migration-style
        # buffer (existence tax for a reasonable grace window).
        min_credits = abs(CREDIT_COSTS["existence_tax"]) * 50
        if credits < min_credits:
            logger.info(
                "Agent %s credits %.2f below restricted-entry buffer %.2f for territory %s",
                _attr(agent, "agent_id"), credits, min_credits, _attr(target_territory, "territory_id"),
            )
            return False

        return True

    @staticmethod
    def _specialization_is_banned(territory: Any, specialization: str) -> bool:
        laws = _attr(territory, "laws", []) or []
        for law in laws:
            banned = _attr(law, "banned_specializations")
            if banned and specialization in banned:
                return True
            text = (_attr(law, "text") or "").lower()
            if text and f"no {specialization}" in text:
                return True
        return False

    @staticmethod
    async def _spawn_migrated_agent_cell(agent: Any, migration_package: dict[str, Any], target_territory_id: str) -> Any:
        """Construct the new AgentCell instance at the destination
        territory from the migration package. Imported lazily for the same
        reason as core.reproduction._spawn_agent_cell."""
        from core.agent_cell import AgentCell  # local import: see module docstring
        from core.genome import Genome

        genome_payload = migration_package.get("genome")
        genome = Genome.from_dict(genome_payload) if isinstance(genome_payload, dict) else genome_payload

        new_agent = AgentCell(
            genome=genome,
            territory_id=target_territory_id,
            generation=migration_package.get("generation", 0),
            parent_id=_attr(agent, "parent_id"),
            lineage_id=uuid.UUID(migration_package["lineage_id"]) if migration_package.get("lineage_id") else _attr(agent, "lineage_id"),
            credits=_attr(agent, "credits", 0.0),
        )
        if not isinstance(new_agent, dict):
            setattr(new_agent, "relationships", dict(migration_package.get("relationships") or {}))
            setattr(new_agent, "divine_favor", migration_package.get("divine_favor", 0.0))
            setattr(new_agent, "reputation", migration_package.get("reputation", 0.0))
            setattr(new_agent, "migrated_from", _attr(agent, "agent_id"))
        return new_agent

    # -- Phase 4 -------------------------------------------------------

    async def _integrate(self, new_agent: Any, target_territory: Any, migration_package: dict[str, Any], event_logger: EventLogger) -> None:
        target_territory_id = _attr(target_territory, "territory_id")

        memory = _attr(new_agent, "memory")
        if memory is not None:
            receive = getattr(memory, "receive_inheritance", None)
            if callable(receive):
                try:
                    receive(
                        {
                            "inherited_episodes": migration_package.get("episodic_memory", []),
                            "inherited_semantic_nodes": migration_package.get("semantic_memory"),
                            "lineage_summary": f"Migrated agent, lineage {migration_package.get('lineage_id')}",
                        }
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to unpack migration package into new agent's memory")

        laws = _attr(target_territory, "laws", []) or []
        semantic = getattr(memory, "semantic", None) if memory is not None else None
        if semantic is not None and hasattr(semantic, "add_concept"):
            for law in laws:
                try:
                    law_text = _attr(law, "text", str(law))
                    from core.memory_system import SemanticNode  # local import: see module docstring

                    semantic.add_concept(
                        SemanticNode(
                            concept=f"local_law:{getattr(law, 'law_id', law_text[:32])}",
                            confidence=0.9,
                            evidence_count=1,
                            relations=[],
                            learned_from="territory_integration",
                            last_updated=datetime.now(timezone.utc),
                        )
                    )
                except Exception:  # noqa: BLE001
                    logger.exception("Failed to record a local law into new agent's semantic memory during integration")

        resident_count = len(_attr(target_territory, "population", []) or [])
        self._set_status(new_agent, AgentStatus.ACTIVE)

        await event_logger.log(
            WorldEvent(
                event_type=WorldEventType.MIGRATION_INTEGRATION,
                agent_id=_attr(new_agent, "agent_id"),
                territory_id=target_territory_id,
                data={
                    "laws_learned": len(laws),
                    "resident_agents_on_arrival": resident_count,
                },
            )
        )
        logger.info(
            "Agent %s successfully integrated into territory %s (%d resident agents, %d laws learned)",
            _attr(new_agent, "agent_id"), target_territory_id, resident_count, len(laws),
        )

    # -- small shared helpers -------------------------------------------

    @staticmethod
    def _set_status(agent: Any, status: "AgentStatus") -> None:  # noqa: F821
        if isinstance(agent, dict):
            agent["status"] = status
        else:
            agent.status = status

    @staticmethod
    def _deduct_credits(agent: Any, amount: float) -> None:
        current = _attr(agent, "credits", 0.0) or 0.0
        new_balance = max(0.0, current - amount)
        if isinstance(agent, dict):
            agent["credits"] = new_balance
        else:
            agent.credits = new_balance
