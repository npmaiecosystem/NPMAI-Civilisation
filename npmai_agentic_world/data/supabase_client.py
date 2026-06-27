"""
data/supabase_client.py
========================
Singleton wrapper around the Supabase Python client, with exponential
backoff retry, full table schema management, and thin async insert /
batch_insert / query helpers used by everything else in data/.

The `supabase` package is imported lazily (inside SupabaseClient.connect)
so that this module can always be imported — and unit-tested — even in
environments where the dependency isn't installed yet or where no network
access to Supabase exists.
"""

from __future__ import annotations

import asyncio
import logging
import random
from typing import Any, Optional

from config.settings import get_settings

logger = logging.getLogger("npmai_world.supabase_client")


class SupabaseConnectionError(RuntimeError):
    """Raised when the Supabase client cannot establish or use a connection
    after exhausting all retries."""


# ---------------------------------------------------------------------------
# Full SQL schema for every table the simulation needs.
# Executed via create_all_tables(); idempotent (CREATE TABLE IF NOT EXISTS).
# world_events is range-partitioned by experiment_day so the "never delete,
# log from tick 1 to forever" requirement stays queryable at scale.
# ---------------------------------------------------------------------------

SCHEMA_STATEMENTS: list[str] = [
    # world_events: partitioned master event log
    """
    CREATE TABLE IF NOT EXISTS world_events (
        event_id        UUID PRIMARY KEY,
        timestamp       TIMESTAMPTZ NOT NULL,
        event_type      TEXT NOT NULL,
        event_category  TEXT NOT NULL,
        agent_id        UUID,
        territory_id    UUID,
        data            JSONB NOT NULL DEFAULT '{}'::jsonb,
        generation      INTEGER NOT NULL DEFAULT 0,
        tick            BIGINT NOT NULL DEFAULT 0,
        experiment_day  INTEGER NOT NULL DEFAULT 0
    ) PARTITION BY RANGE (experiment_day);
    """,
    # A generous default partition; downstream tooling can add per-day
    # partitions ahead of time (e.g. via a scheduled job) for performance.
    """
    CREATE TABLE IF NOT EXISTS world_events_default
        PARTITION OF world_events DEFAULT;
    """,
    "CREATE INDEX IF NOT EXISTS idx_world_events_agent ON world_events (agent_id, timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_world_events_territory ON world_events (territory_id, timestamp DESC);",
    "CREATE INDEX IF NOT EXISTS idx_world_events_type ON world_events (event_type);",
    "CREATE INDEX IF NOT EXISTS idx_world_events_category ON world_events (event_category);",
    "CREATE INDEX IF NOT EXISTS idx_world_events_tick ON world_events (tick);",

    # agent_states: latest + historical snapshots of each agent
    """
    CREATE TABLE IF NOT EXISTS agent_states (
        snapshot_id     UUID PRIMARY KEY,
        agent_id        UUID NOT NULL,
        territory_id    UUID,
        captured_at     TIMESTAMPTZ NOT NULL,
        tick            BIGINT NOT NULL,
        generation      INTEGER NOT NULL DEFAULT 0,
        lineage_id      UUID,
        status          TEXT NOT NULL,
        credits         DOUBLE PRECISION NOT NULL,
        age             BIGINT NOT NULL,
        health          DOUBLE PRECISION NOT NULL,
        reputation      DOUBLE PRECISION NOT NULL DEFAULT 0,
        divine_favor    DOUBLE PRECISION NOT NULL DEFAULT 0,
        state           JSONB NOT NULL DEFAULT '{}'::jsonb
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_agent_states_agent ON agent_states (agent_id, captured_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_agent_states_lineage ON agent_states (lineage_id);",

    # territory_states
    """
    CREATE TABLE IF NOT EXISTS territory_states (
        snapshot_id     UUID PRIMARY KEY,
        territory_id    UUID NOT NULL,
        captured_at     TIMESTAMPTZ NOT NULL,
        tick            BIGINT NOT NULL,
        name            TEXT NOT NULL,
        host            TEXT,
        population_count INTEGER NOT NULL DEFAULT 0,
        border_policy   TEXT NOT NULL,
        resources       JSONB NOT NULL DEFAULT '{}'::jsonb,
        state           JSONB NOT NULL DEFAULT '{}'::jsonb
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_territory_states_territory ON territory_states (territory_id, captured_at DESC);",

    # genome_bank: archived genomes of dead agents
    """
    CREATE TABLE IF NOT EXISTS genome_bank (
        archive_id      UUID PRIMARY KEY,
        agent_id        UUID NOT NULL,
        lineage_id      UUID NOT NULL,
        parent_id       UUID,
        generation      INTEGER NOT NULL DEFAULT 0,
        death_mode      TEXT NOT NULL,
        archived_at     TIMESTAMPTZ NOT NULL,
        final_stats     JSONB NOT NULL DEFAULT '{}'::jsonb,
        genome          JSONB NOT NULL,
        is_lineage_root BOOLEAN NOT NULL DEFAULT FALSE
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_genome_bank_lineage ON genome_bank (lineage_id);",
    "CREATE INDEX IF NOT EXISTS idx_genome_bank_archived_at ON genome_bank (archived_at);",

    # semantic_graphs: shared-per-territory conceptual hypergraph fragments
    """
    CREATE TABLE IF NOT EXISTS semantic_graphs (
        node_id         UUID PRIMARY KEY,
        territory_id    UUID NOT NULL,
        contributed_by  UUID,
        concept         TEXT NOT NULL,
        relations       JSONB NOT NULL DEFAULT '[]'::jsonb,
        confidence      DOUBLE PRECISION NOT NULL DEFAULT 0.5,
        created_at      TIMESTAMPTZ NOT NULL,
        updated_at      TIMESTAMPTZ NOT NULL
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_semantic_graphs_territory ON semantic_graphs (territory_id);",
    "CREATE INDEX IF NOT EXISTS idx_semantic_graphs_concept ON semantic_graphs (concept);",

    # lineage_tree
    """
    CREATE TABLE IF NOT EXISTS lineage_tree (
        agent_id        UUID PRIMARY KEY,
        lineage_id      UUID NOT NULL,
        parent_id       UUID,
        generation      INTEGER NOT NULL DEFAULT 0,
        born_at         TIMESTAMPTZ NOT NULL,
        died_at         TIMESTAMPTZ,
        territory_id    UUID
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_lineage_tree_lineage ON lineage_tree (lineage_id);",
    "CREATE INDEX IF NOT EXISTS idx_lineage_tree_parent ON lineage_tree (parent_id);",

    # governance_records
    """
    CREATE TABLE IF NOT EXISTS governance_records (
        record_id       UUID PRIMARY KEY,
        territory_id    UUID NOT NULL,
        record_type     TEXT NOT NULL,
        proposed_by     UUID,
        created_at      TIMESTAMPTZ NOT NULL,
        resolved_at     TIMESTAMPTZ,
        outcome         TEXT,
        details         JSONB NOT NULL DEFAULT '{}'::jsonb
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_governance_records_territory ON governance_records (territory_id, created_at DESC);",

    # economic_ledger
    """
    CREATE TABLE IF NOT EXISTS economic_ledger (
        entry_id        UUID PRIMARY KEY,
        agent_id        UUID NOT NULL,
        territory_id    UUID,
        tick            BIGINT NOT NULL,
        timestamp       TIMESTAMPTZ NOT NULL,
        delta           DOUBLE PRECISION NOT NULL,
        balance_after   DOUBLE PRECISION NOT NULL,
        reason          TEXT NOT NULL,
        details         JSONB NOT NULL DEFAULT '{}'::jsonb
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_economic_ledger_agent ON economic_ledger (agent_id, timestamp DESC);",

    # divine_communications
    """
    CREATE TABLE IF NOT EXISTS divine_communications (
        message_id      UUID PRIMARY KEY,
        persona         TEXT NOT NULL,
        message_type    TEXT NOT NULL,
        target_agent_id UUID,
        target_territory_id UUID,
        sent_at         TIMESTAMPTZ NOT NULL,
        content         TEXT NOT NULL,
        agent_response  JSONB,
        researcher_note TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_divine_comm_target_agent ON divine_communications (target_agent_id);",

    # bad_activity_log
    """
    CREATE TABLE IF NOT EXISTS bad_activity_log (
        incident_id     UUID PRIMARY KEY,
        agent_id        UUID,
        territory_id    UUID,
        detected_at     TIMESTAMPTZ NOT NULL,
        activity_type   TEXT NOT NULL,
        severity        TEXT NOT NULL DEFAULT 'low',
        description     TEXT NOT NULL,
        evidence        JSONB NOT NULL DEFAULT '{}'::jsonb,
        action_taken    TEXT
    );
    """,
    "CREATE INDEX IF NOT EXISTS idx_bad_activity_agent ON bad_activity_log (agent_id, detected_at DESC);",
    "CREATE INDEX IF NOT EXISTS idx_bad_activity_severity ON bad_activity_log (severity);",
]


class SupabaseClient:
    """Process-wide singleton wrapper around the Supabase client.

    Call `SupabaseClient.get_instance()` everywhere instead of constructing
    this class directly, so the whole process shares one connection pool.
    """

    _instance: Optional["SupabaseClient"] = None
    _instance_lock = asyncio.Lock()

    def __init__(self) -> None:
        if SupabaseClient._instance is not None:
            raise RuntimeError("Use SupabaseClient.get_instance() instead of direct construction")
        self._client: Any = None
        self._connected: bool = False
        self._url: str = ""
        self._key: str = ""

    @classmethod
    async def get_instance(cls) -> "SupabaseClient":
        async with cls._instance_lock:
            if cls._instance is None:
                cls._instance = cls.__new__(cls)
                cls._instance._client = None
                cls._instance._connected = False
                cls._instance._url = ""
                cls._instance._key = ""
                await cls._instance.connect()
            return cls._instance

    async def connect(self, max_retries: int = 6, base_delay: float = 0.5) -> None:
        """Establish (or re-establish) the Supabase connection with
        exponential backoff + jitter. Raises SupabaseConnectionError if all
        retries are exhausted."""
        settings = get_settings()
        self._url = settings.supabase_url
        self._key = settings.supabase_key

        if not self._url or not self._key:
            logger.warning(
                "SUPABASE_URL / SUPABASE_KEY not set; SupabaseClient running in "
                "disconnected mode (EventLogger will buffer locally instead)."
            )
            self._connected = False
            return

        last_exc: Optional[Exception] = None
        for attempt in range(1, max_retries + 1):
            try:
                from supabase import Client, create_client  # lazy import

                self._client = create_client(self._url, self._key)
                # Cheap connectivity probe.
                self._client.table("world_events").select("event_id").limit(1).execute()
                self._connected = True
                logger.info("Connected to Supabase on attempt %d", attempt)
                return
            except Exception as exc:  # noqa: BLE001 - any client/network error
                last_exc = exc
                delay = base_delay * (2 ** (attempt - 1)) + random.uniform(0, 0.25)
                logger.warning(
                    "Supabase connection attempt %d/%d failed (%s); retrying in %.2fs",
                    attempt, max_retries, exc, delay,
                )
                await asyncio.sleep(delay)

        self._connected = False
        raise SupabaseConnectionError(
            f"Could not connect to Supabase after {max_retries} attempts: {last_exc}"
        )

    @property
    def is_connected(self) -> bool:
        return self._connected and self._client is not None

    async def health_check(self) -> bool:
        """Cheap round-trip to verify the connection is alive; attempts one
        reconnect if it isn't."""
        if not self.is_connected:
            try:
                await self.connect(max_retries=1)
            except SupabaseConnectionError:
                return False
        try:
            self._client.table("world_events").select("event_id").limit(1).execute()
            return True
        except Exception as exc:  # noqa: BLE001
            logger.error("Supabase health check failed: %s", exc)
            self._connected = False
            return False

    def create_all_tables(self) -> None:
        """Execute the full schema. Requires an RPC function on the Supabase
        side (`exec_sql`) since the REST API does not allow raw DDL; see
        the migration note below. This is the conventional pattern for
        bootstrapping schema from a Python client against Supabase/Postgres.
        """
        if not self.is_connected:
            raise SupabaseConnectionError("Cannot create tables: not connected to Supabase")
        for statement in SCHEMA_STATEMENTS:
            try:
                self._client.rpc("exec_sql", {"sql": statement}).execute()
            except Exception as exc:  # noqa: BLE001
                logger.error("Failed executing schema statement: %s\nSQL:\n%s", exc, statement)
                raise
        logger.info("All %d schema statements applied.", len(SCHEMA_STATEMENTS))

    async def insert(self, table: str, row: dict[str, Any], retries: int = 3) -> dict[str, Any]:
        """Insert a single row with retry on transient failure."""
        return (await self.batch_insert(table, [row], retries=retries))[0]

    async def batch_insert(
        self, table: str, rows: list[dict[str, Any]], retries: int = 3
    ) -> list[dict[str, Any]]:
        if not rows:
            return []
        if not self.is_connected:
            raise SupabaseConnectionError(f"Cannot insert into {table}: not connected to Supabase")

        last_exc: Optional[Exception] = None
        for attempt in range(1, retries + 1):
            try:
                result = self._client.table(table).insert(rows).execute()
                return result.data or []
            except Exception as exc:  # noqa: BLE001
                last_exc = exc
                delay = 0.3 * (2 ** (attempt - 1))
                logger.warning(
                    "batch_insert into %s failed (attempt %d/%d): %s", table, attempt, retries, exc
                )
                await asyncio.sleep(delay)
        raise SupabaseConnectionError(f"batch_insert into {table} failed after {retries} attempts: {last_exc}")

    async def query(
        self,
        table: str,
        filters: Optional[dict[str, Any]] = None,
        order_by: Optional[str] = None,
        descending: bool = True,
        limit: Optional[int] = None,
    ) -> list[dict[str, Any]]:
        """Generic SELECT helper with equality filters, ordering, and limit."""
        if not self.is_connected:
            raise SupabaseConnectionError(f"Cannot query {table}: not connected to Supabase")

        q = self._client.table(table).select("*")
        for key, value in (filters or {}).items():
            q = q.eq(key, value)
        if order_by:
            q = q.order(order_by, desc=descending)
        if limit is not None:
            q = q.limit(limit)

        try:
            result = q.execute()
            return result.data or []
        except Exception as exc:  # noqa: BLE001
            raise SupabaseConnectionError(f"query on {table} failed: {exc}") from exc
