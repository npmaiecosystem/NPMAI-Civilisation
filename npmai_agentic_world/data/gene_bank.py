"""
data/gene_bank.py
==================
Cold storage for dead agents' genomes.

When an agent dies (any DeathMode), its genome — prompt_dna,
capability_chromosome, parameter_genes, plus final stats — is archived
here. Archives are retained for 30 days by default and then cleaned up,
*except* lineage roots (the first agent of a lineage), which are kept
forever so a lineage can always be traced back to its origin.

A "resurrection" doesn't restore the dead agent itself — it returns a
genome that world/lifecycle.py can hand to a brand-new AgentCell, seeding
a fresh agent with an extinct lineage's traits.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config.constants import DeathMode
from data.supabase_client import SupabaseClient, SupabaseConnectionError

logger = logging.getLogger("npmai_world.gene_bank")


class GeneBank:
    """Stateless-ish facade over the `genome_bank` table. No singleton
    needed since it holds no buffering state of its own (SupabaseClient
    already is one); a thin class purely for API clarity and discoverability.
    """

    TABLE = "genome_bank"

    @staticmethod
    async def archive_genome(
        agent_id: uuid.UUID,
        lineage_id: uuid.UUID,
        genome: dict[str, Any],
        death_mode: DeathMode | str,
        final_stats: dict[str, Any],
        parent_id: Optional[uuid.UUID] = None,
        generation: int = 0,
        is_lineage_root: bool = False,
    ) -> uuid.UUID:
        """Persist a dead agent's genome. Returns the new archive_id.

        `is_lineage_root` should be True iff this agent had no parent
        (generation == 0) — those rows are exempt from cleanup_old_archives.
        """
        death_mode_value = death_mode.value if isinstance(death_mode, DeathMode) else str(death_mode)
        archive_id = uuid.uuid4()
        row = {
            "archive_id": str(archive_id),
            "agent_id": str(agent_id),
            "lineage_id": str(lineage_id),
            "parent_id": str(parent_id) if parent_id else None,
            "generation": generation,
            "death_mode": death_mode_value,
            "archived_at": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
            "final_stats": final_stats,
            "genome": genome,
            "is_lineage_root": is_lineage_root or generation == 0,
        }
        client = await SupabaseClient.get_instance()
        await client.insert(GeneBank.TABLE, row)
        logger.info("Archived genome for agent %s (lineage %s, death=%s)", agent_id, lineage_id, death_mode_value)
        return archive_id

    @staticmethod
    async def retrieve_genome(agent_id: uuid.UUID) -> Optional[dict[str, Any]]:
        """Fetch the archived genome row for a specific dead agent, or None
        if it was never archived (still alive) or has been cleaned up."""
        client = await SupabaseClient.get_instance()
        rows = await client.query(GeneBank.TABLE, filters={"agent_id": str(agent_id)}, limit=1)
        return rows[0] if rows else None

    @staticmethod
    async def get_lineage(lineage_id: uuid.UUID) -> list[dict[str, Any]]:
        """Return the full archived family tree for a lineage, oldest
        generation first."""
        client = await SupabaseClient.get_instance()
        rows = await client.query(
            GeneBank.TABLE,
            filters={"lineage_id": str(lineage_id)},
            order_by="generation",
            descending=False,
            limit=10_000,
        )
        return rows

    @staticmethod
    async def get_strongest_genomes(n: int = 10, by: str = "credits_earned") -> list[dict[str, Any]]:
        """Return the top-N archived genomes ranked by a key inside
        final_stats (default: lifetime credits_earned).

        Supabase/PostgREST can't order by a JSONB field through the simple
        query builder portably across versions, so we pull a generous
        candidate window ordered by archived_at and sort client-side. This
        trades a bit of efficiency for correctness and portability.
        """
        client = await SupabaseClient.get_instance()
        candidates = await client.query(GeneBank.TABLE, order_by="archived_at", limit=2000)

        def _score(row: dict[str, Any]) -> float:
            stats = row.get("final_stats") or {}
            value = stats.get(by, 0)
            try:
                return float(value)
            except (TypeError, ValueError):
                return 0.0

        candidates.sort(key=_score, reverse=True)
        return candidates[:n]

    @staticmethod
    async def resurrect_lineage(lineage_id: uuid.UUID) -> Optional[dict[str, Any]]:
        """Return the genome of the strongest archived ancestor in a
        lineage (highest lifetime credits_earned), suitable for seeding a
        brand-new AgentCell. Returns None if the lineage has no archives."""
        lineage = await GeneBank.get_lineage(lineage_id)
        if not lineage:
            return None

        def _score(row: dict[str, Any]) -> float:
            stats = row.get("final_stats") or {}
            try:
                return float(stats.get("credits_earned", 0))
            except (TypeError, ValueError):
                return 0.0

        best = max(lineage, key=_score)
        logger.info("Resurrecting lineage %s from agent %s", lineage_id, best.get("agent_id"))
        return best.get("genome")

    @staticmethod
    async def cleanup_old_archives(retention_days: int = 30) -> int:
        """Delete archived genomes older than `retention_days`, except
        lineage roots, which are kept forever. Returns the number of rows
        deleted.

        Uses the Supabase RPC channel for the bulk DELETE since the simple
        query builder doesn't expose `delete().lt()` combined with a
        boolean exclusion cleanly across client versions; this keeps the
        operation atomic on the server side.
        """
        client = await SupabaseClient.get_instance()
        if not client.is_connected:
            raise SupabaseConnectionError("Cannot clean up gene bank: not connected to Supabase")

        cutoff = (datetime.now(timezone.utc) - timedelta(days=retention_days)).isoformat(timespec="milliseconds")
        sql = (
            "DELETE FROM genome_bank "
            f"WHERE archived_at < '{cutoff}' AND is_lineage_root = FALSE "
            "RETURNING archive_id;"
        )
        try:
            result = client._client.rpc("exec_sql", {"sql": sql}).execute()  # noqa: SLF001
            deleted = len(result.data or [])
            logger.info("Gene bank cleanup removed %d archives older than %d days", deleted, retention_days)
            return deleted
        except Exception as exc:  # noqa: BLE001
            logger.error("Gene bank cleanup failed: %s", exc)
            raise

    @staticmethod
    async def get_gene_bank_stats() -> dict[str, Any]:
        """Population-genetics-flavoured summary of everything archived so
        far: total archives, distinct lineages, death mode breakdown, and
        the deepest generation reached."""
        client = await SupabaseClient.get_instance()
        rows = await client.query(GeneBank.TABLE, limit=100_000)

        if not rows:
            return {
                "total_archives": 0,
                "distinct_lineages": 0,
                "death_mode_breakdown": {},
                "max_generation_reached": 0,
                "lineage_root_count": 0,
            }

        lineages = {row["lineage_id"] for row in rows}
        death_modes: dict[str, int] = {}
        for row in rows:
            death_modes[row["death_mode"]] = death_modes.get(row["death_mode"], 0) + 1
        max_generation = max((row.get("generation", 0) for row in rows), default=0)
        lineage_roots = sum(1 for row in rows if row.get("is_lineage_root"))

        return {
            "total_archives": len(rows),
            "distinct_lineages": len(lineages),
            "death_mode_breakdown": death_modes,
            "max_generation_reached": max_generation,
            "lineage_root_count": lineage_roots,
        }
