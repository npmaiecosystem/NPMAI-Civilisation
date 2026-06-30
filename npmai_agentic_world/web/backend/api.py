"""
web/backend/api.py
──────────────────
FastAPI application for NPMAI Agentic World — the public civilisation website.

Endpoint groups
───────────────
PUBLIC (no auth)
  GET  /api/world/stats
  GET  /api/world/territories
  GET  /api/agents/leaderboard
  GET  /api/agents/{agent_id}
  GET  /api/lineage/{lineage_id}
  GET  /api/events/recent
  GET  /api/research/papers
  GET  /api/research/updates

AUTHENTICATED
  POST /api/auth/register
  POST /api/auth/login
  POST /api/agents/register
  GET  /api/my/agents
  GET  /api/my/agent/{agent_id}/lineage

ADMIN (Sonu only)
  POST /api/divine/send
  POST /api/research/update
  POST /api/world/config

WebSocket hub is in websocket.py — imported and mounted below.

Run with:
  uvicorn web.backend.api:app --host 0.0.0.0 --port 8000 --workers 4
"""

from __future__ import annotations

from pathlib import Path
import sys
root_dir = Path(__file__).resolve().parent.parent.parent
if str(root_dir) not in sys.path:
    sys.path.append(str(root_dir))
  
import logging
import os
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    HTTPException,
    Query,
    Request,
    status,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, EmailStr, Field, field_validator

# ── project imports ───────────────────────────────────────────────────────────
from data.supabase_client import SupabaseClient
from data.event_types import WorldEventType
from config.settings import load_settings
from world.world_controller import WorldController
from divine.oracle import Oracle

from web.backend.auth import (
    User,
    ensure_users_table,
    get_client_ip,
    get_current_user,
    get_optional_user,
    login,
    register,
    require_admin,
)

# ── WebSocket broadcaster (imported, router mounted at bottom) ────────────────
from web.backend.websocket import ws_router, broadcaster

logger = logging.getLogger(__name__)

# ═══════════════════════════════════════════════════════════════════════════════
# Application factory
# ═══════════════════════════════════════════════════════════════════════════════

app = FastAPI(
    title="NPMAI Agentic World — API",
    description=(
        "REST + WebSocket backend for the NPMAI Agentic World civilisation "
        "experiment. Built by Sonu Kumar, 15, NPMAI ECOSYSTEM."
    ),
    version="1.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
    openapi_url="/api/openapi.json",
)

# ── CORS ──────────────────────────────────────────────────────────────────────
_ALLOWED_ORIGINS: list[str] = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://localhost:5173,https://npmai.netlify.app",
).split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=_ALLOWED_ORIGINS,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Request-ID", "X-Process-Time"],
)

# ── GZip ─────────────────────────────────────────────────────────────────────
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ── Mount WebSocket router ────────────────────────────────────────────────────
app.include_router(ws_router)


# ═══════════════════════════════════════════════════════════════════════════════
# Middleware — request timing & request-id injection
# ═══════════════════════════════════════════════════════════════════════════════

@app.middleware("http")
async def add_process_time_header(request: Request, call_next):
    start = time.perf_counter()
    response = await call_next(request)
    elapsed = round((time.perf_counter() - start) * 1000, 2)
    response.headers["X-Process-Time"] = f"{elapsed}ms"
    return response


# ═══════════════════════════════════════════════════════════════════════════════
# Lifecycle events
# ═══════════════════════════════════════════════════════════════════════════════

@app.on_event("startup")
async def _startup():
    await ensure_users_table()
    await broadcaster.startup()
    logger.info("NPMAI API started — %s", datetime.now(timezone.utc).isoformat())


@app.on_event("shutdown")
async def _shutdown():
    await broadcaster.shutdown()
    logger.info("NPMAI API shutting down.")


# ═══════════════════════════════════════════════════════════════════════════════
# Pydantic request / response models
# ═══════════════════════════════════════════════════════════════════════════════

# ── Auth ──────────────────────────────────────────────────────────────────────

class RegisterRequest(BaseModel):
    username: str = Field(..., min_length=3, max_length=32)
    email: EmailStr
    password: str = Field(..., min_length=8, max_length=128)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(..., min_length=1)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: str
    username: str
    agent_slots: int
    subscription_tier: str
    is_admin: bool


# ── Agent registration ────────────────────────────────────────────────────────

class AgentRegisterRequest(BaseModel):
    name: str = Field(..., min_length=2, max_length=64)
    specialization: str = Field(
        default="generalist",
        description="Agent's primary specialization: explorer, builder, trader, diplomat, scientist, generalist",
    )
    territory_id: Optional[str] = Field(
        None,
        description="Target starting territory. Omit for auto-assignment.",
    )
    custom_prompt_fragment: Optional[str] = Field(
        None,
        max_length=500,
        description="Up to 500-char personality fragment injected into agent's prompt_dna exons.",
    )

    @field_validator("specialization")
    @classmethod
    def validate_spec(cls, v: str) -> str:
        valid = {"explorer", "builder", "trader", "diplomat", "scientist", "generalist"}
        if v.lower() not in valid:
            raise ValueError(f"specialization must be one of: {', '.join(sorted(valid))}")
        return v.lower()


# ── Divine ────────────────────────────────────────────────────────────────────

class DivineMessageRequest(BaseModel):
    agent_id: str
    message: str = Field(..., min_length=1, max_length=2000)
    persona: str = Field(
        default="THE_ARCHITECT",
        description="One of: THE_ARCHITECT, THE_GARDENER, THE_JUDGE, THE_TRICKSTER, THE_SILENT_ONE",
    )
    message_type: str = Field(
        default="REVELATION",
        description="One of: REVELATION, COMMANDMENT, PROPHECY, BLESSING, TRIAL",
    )

    @field_validator("persona")
    @classmethod
    def validate_persona(cls, v: str) -> str:
        valid = {"THE_ARCHITECT", "THE_GARDENER", "THE_JUDGE", "THE_TRICKSTER", "THE_SILENT_ONE"}
        if v.upper() not in valid:
            raise ValueError(f"persona must be one of: {', '.join(valid)}")
        return v.upper()

    @field_validator("message_type")
    @classmethod
    def validate_msg_type(cls, v: str) -> str:
        valid = {"REVELATION", "COMMANDMENT", "PROPHECY", "BLESSING", "TRIAL"}
        if v.upper() not in valid:
            raise ValueError(f"message_type must be one of: {', '.join(valid)}")
        return v.upper()


# ── Research ──────────────────────────────────────────────────────────────────

class ResearchUpdateRequest(BaseModel):
    title: str = Field(..., min_length=3, max_length=200)
    content: str = Field(..., min_length=10)
    tags: List[str] = Field(default_factory=list)
    experiment_day: Optional[int] = Field(None, ge=0)


# ── World config ──────────────────────────────────────────────────────────────

class WorldConfigRequest(BaseModel):
    key: str
    value: Any


# ═══════════════════════════════════════════════════════════════════════════════
# Helper: Supabase query wrappers
# ═══════════════════════════════════════════════════════════════════════════════

async def _db() -> SupabaseClient:
    return SupabaseClient.get_instance()


async def _query(table: str, filters: Optional[dict] = None) -> list:
    client = await _db()
    try:
        return await client.select(table, filters=filters or {})
    except Exception as exc:
        logger.exception("DB query error on %s: %s", table, exc)
        raise HTTPException(500, f"Database error querying {table}.")


async def _insert(table: str, data: dict) -> dict:
    client = await _db()
    try:
        return await client.insert(table, data)
    except Exception as exc:
        logger.exception("DB insert error on %s: %s", table, exc)
        raise HTTPException(500, f"Database error inserting into {table}.")


# ═══════════════════════════════════════════════════════════════════════════════
# ──  PUBLIC ENDPOINTS  ──────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── /api/world/stats ─────────────────────────────────────────────────────────

@app.get(
    "/api/world/stats",
    summary="World-level civilisation statistics",
    tags=["World"],
)
async def world_stats():
    """
    Returns high-level snapshot of the running civilisation:
    - alive_count, dead_count, total_generations
    - world_age (seconds since epoch tick 0), tick_count
    - territory_count
    - gini_coefficient (wealth inequality measure 0–1)
    - total_events_logged
    """
    client = await _db()

    async def _count(table, filters=None):
        try:
            rows = await client.select(table, filters=filters or {})
            return len(rows)
        except Exception:
            return 0

    # Alive / dead agents
    alive = await _count("agent_snapshots", {"status": "ACTIVE"})
    alive += await _count("agent_snapshots", {"status": "ELDER"})
    dead = await _count("agent_snapshots", {"status": "DEAD"})
    territories = await _count("territories")
    total_events = await _count("world_events")

    # Generations — max generation number seen
    try:
        agent_rows = await client.select("agent_snapshots", filters={})
        total_gens = max((r.get("generation", 0) for r in agent_rows), default=0)
        credits_list = [r.get("credits", 0) for r in agent_rows if r.get("status") == "ACTIVE"]
        gini = _compute_gini(credits_list)
    except Exception:
        total_gens = 0
        gini = 0.0

    # World clock
    try:
        wc_rows = await client.select("world_clock", filters={})
        tick_count = wc_rows[0].get("tick_count", 0) if wc_rows else 0
        world_age = wc_rows[0].get("world_age_seconds", 0) if wc_rows else 0
    except Exception:
        tick_count = 0
        world_age = 0

    return {
        "alive_count": alive,
        "dead_count": dead,
        "total_generations": total_gens,
        "world_age": world_age,
        "tick_count": tick_count,
        "territory_count": territories,
        "gini_coefficient": round(gini, 4),
        "total_events_logged": total_events,
    }


def _compute_gini(values: list[float]) -> float:
    """Compute Gini coefficient from a list of non-negative values."""
    if not values or len(values) < 2:
        return 0.0
    arr = sorted(v for v in values if v >= 0)
    n = len(arr)
    if n == 0 or sum(arr) == 0:
        return 0.0
    cumsum = 0.0
    gini_sum = 0.0
    for i, v in enumerate(arr):
        cumsum += v
        gini_sum += (2 * (i + 1) - n - 1) * v
    return gini_sum / (n * cumsum)


# ── /api/world/territories ───────────────────────────────────────────────────

@app.get(
    "/api/world/territories",
    summary="All territories with population and laws",
    tags=["World"],
)
async def list_territories():
    """
    Returns all territories with population counts, resource health, and
    current laws.
    """
    rows = await _query("territories")
    result = []
    for t in rows:
        pop_rows = await _query("agent_snapshots", {"territory_id": t["territory_id"]})
        alive_pop = [r for r in pop_rows if r.get("status") in ("ACTIVE", "ELDER")]
        result.append(
            {
                "territory_id": t["territory_id"],
                "name": t.get("name", "Unknown"),
                "host": t.get("host", ""),
                "border_policy": t.get("border_policy", "OPEN"),
                "resources": t.get("resources", {}),
                "population_count": len(alive_pop),
                "laws": t.get("laws", []),
                "credit_pool": t.get("credit_pool", 0),
            }
        )
    return {"territories": result, "total": len(result)}


# ── /api/agents/leaderboard ──────────────────────────────────────────────────

@app.get(
    "/api/agents/leaderboard",
    summary="Top 20 agents leaderboard",
    tags=["Agents"],
)
async def agent_leaderboard(
    by: str = Query(
        default="credits",
        description="Sort field: credits | age | children | territories_visited",
    )
):
    """
    Returns top 20 agents sorted by the chosen metric.
    """
    valid_sort = {"credits", "age", "children", "territories_visited"}
    if by not in valid_sort:
        raise HTTPException(
            400,
            f"Invalid 'by' parameter. Choose from: {', '.join(sorted(valid_sort))}",
        )

    rows = await _query("agent_snapshots")
    alive = [r for r in rows if r.get("status") in ("ACTIVE", "ELDER", "MIGRATING")]

    # Sort
    def sort_key(r):
        if by == "credits":
            return r.get("credits", 0)
        elif by == "age":
            return r.get("age", 0)
        elif by == "children":
            return r.get("children_count", 0)
        elif by == "territories_visited":
            return len(r.get("territories_visited", []))
        return 0

    ranked = sorted(alive, key=sort_key, reverse=True)[:20]

    return {
        "sort_by": by,
        "agents": [
            {
                "rank": idx + 1,
                "agent_id": r["agent_id"],
                "name": r.get("name", "Unknown"),
                "generation": r.get("generation", 1),
                "territory_id": r.get("territory_id"),
                "credits": round(r.get("credits", 0), 2),
                "age": r.get("age", 0),
                "children_count": r.get("children_count", 0),
                "reputation": round(r.get("reputation", 0), 2),
                "status": r.get("status", "ACTIVE"),
            }
            for idx, r in enumerate(ranked)
        ],
    }


# ── /api/agents/{agent_id} ───────────────────────────────────────────────────

@app.get(
    "/api/agents/{agent_id}",
    summary="Full agent profile",
    tags=["Agents"],
)
async def get_agent(agent_id: str):
    """
    Full agent profile including identity, vitals, genome summary,
    recent memories, task history, relationships, and divine interactions.
    """
    # Main snapshot
    rows = await _query("agent_snapshots", {"agent_id": agent_id})
    if not rows:
        raise HTTPException(404, f"Agent '{agent_id}' not found.")
    agent = rows[0]

    # Recent memories (last 10 episodic)
    mem_rows = await _query("episodic_memories", {"agent_id": agent_id})
    recent_memories = sorted(
        mem_rows, key=lambda r: r.get("timestamp", 0), reverse=True
    )[:10]

    # Task history (last 20 events)
    event_rows = await _query("world_events", {"agent_id": agent_id})
    task_events = [
        e for e in event_rows
        if e.get("event_type", "").startswith("COGNITION")
    ]
    task_history = sorted(
        task_events, key=lambda r: r.get("timestamp", 0), reverse=True
    )[:20]

    # Relationships
    rel_rows = await _query("agent_relationships", {"agent_id": agent_id})

    # Divine interactions
    divine_rows = [
        e for e in event_rows
        if e.get("event_type", "") in (
            "DIVINE_RECEIVED", "DIVINE_INTERPRETED", "DIVINE_IGNORED"
        )
    ]

    # Genome summary (only expose safe fields, not raw dna)
    genome = agent.get("genome", {})
    genome_summary = {
        "mutation_rate": genome.get("mutation_rate", 0.01),
        "parameter_genes": genome.get("parameter_genes", {}),
        "capability_count": bin(
            int(genome.get("capability_chromosome", "0" * 100), 2)
        ).count("1"),
        "active_exon_count": len(genome.get("prompt_dna", {}).get("exons", [])),
    }

    return {
        "identity": {
            "agent_id": agent["agent_id"],
            "name": agent.get("name"),
            "generation": agent.get("generation", 1),
            "parent_id": agent.get("parent_id"),
            "lineage_id": agent.get("lineage_id"),
            "born_at": agent.get("born_at"),
        },
        "vitals": {
            "credits": round(agent.get("credits", 0), 4),
            "age": agent.get("age", 0),
            "health": round(agent.get("health", 1.0), 4),
            "status": agent.get("status", "ACTIVE"),
            "max_age": agent.get("max_age", 1000),
        },
        "social": {
            "territory_id": agent.get("territory_id"),
            "reputation": round(agent.get("reputation", 0), 4),
            "divine_favor": round(agent.get("divine_favor", 0), 4),
        },
        "genome_summary": genome_summary,
        "recent_memories": [
            {
                "timestamp": m.get("timestamp"),
                "description": m.get("description", ""),
                "emotional_valence": round(m.get("emotional_valence", 0), 3),
            }
            for m in recent_memories
        ],
        "task_history": [
            {
                "timestamp": e.get("timestamp"),
                "event_type": e.get("event_type"),
                "summary": e.get("data", {}).get("summary", ""),
            }
            for e in task_history
        ],
        "relationships": [
            {
                "other_agent_id": r.get("other_agent_id"),
                "trust_score": round(r.get("trust_score", 0), 3),
                "interaction_count": r.get("interaction_count", 0),
            }
            for r in rel_rows[:20]
        ],
        "divine_history": [
            {
                "timestamp": d.get("timestamp"),
                "event_type": d.get("event_type"),
                "persona": d.get("data", {}).get("persona", ""),
                "message_type": d.get("data", {}).get("message_type", ""),
            }
            for d in divine_rows[:10]
        ],
    }


# ── /api/lineage/{lineage_id} ────────────────────────────────────────────────

@app.get(
    "/api/lineage/{lineage_id}",
    summary="Full family tree from genesis to current",
    tags=["Agents"],
)
async def get_lineage(lineage_id: str):
    """
    Full family tree for a lineage — from founding agent through all
    descendant generations.
    """
    rows = await _query("agent_snapshots", {"lineage_id": lineage_id})
    if not rows:
        raise HTTPException(404, f"Lineage '{lineage_id}' not found.")

    # Build adjacency: parent_id → [children]
    by_id: dict[str, dict] = {r["agent_id"]: r for r in rows}

    def _node(agent_id: str) -> dict:
        r = by_id.get(agent_id, {})
        children = [
            a["agent_id"] for a in rows if a.get("parent_id") == agent_id
        ]
        return {
            "agent_id": agent_id,
            "name": r.get("name", "Unknown"),
            "generation": r.get("generation", 1),
            "status": r.get("status", "DEAD"),
            "born_at": r.get("born_at"),
            "credits_at_death": r.get("credits_at_death"),
            "children": [_node(cid) for cid in children],
        }

    # Find genesis agents (no parent in this lineage)
    roots = [
        r["agent_id"]
        for r in rows
        if r.get("parent_id") not in by_id
    ]

    tree = [_node(root) for root in roots]

    return {
        "lineage_id": lineage_id,
        "total_agents": len(rows),
        "alive_count": sum(1 for r in rows if r.get("status") in ("ACTIVE", "ELDER")),
        "max_generation": max((r.get("generation", 1) for r in rows), default=1),
        "tree": tree,
    }


# ── /api/events/recent ───────────────────────────────────────────────────────

@app.get(
    "/api/events/recent",
    summary="Recent world events feed",
    tags=["Events"],
)
async def recent_events(
    limit: int = Query(default=50, ge=1, le=500),
    category: Optional[str] = Query(
        default=None,
        description="Filter by category prefix e.g. COGNITION, ECONOMY, GOVERNANCE, DIVINE, BAD_ACTIVITY",
    ),
    territory_id: Optional[str] = Query(default=None),
    agent_id: Optional[str] = Query(default=None),
):
    """
    Returns recent world events, most recent first.
    Supports filtering by category prefix, territory, or agent.
    """
    filters: dict = {}
    if territory_id:
        filters["territory_id"] = territory_id
    if agent_id:
        filters["agent_id"] = agent_id

    rows = await _query("world_events", filters or None)

    # Category filter (prefix match)
    if category:
        cat_upper = category.upper()
        rows = [r for r in rows if r.get("event_type", "").startswith(cat_upper)]

    # Sort by timestamp descending, limit
    rows = sorted(rows, key=lambda r: r.get("timestamp", 0), reverse=True)[:limit]

    return {
        "events": [
            {
                "event_id": e.get("event_id"),
                "timestamp": e.get("timestamp"),
                "event_type": e.get("event_type"),
                "agent_id": e.get("agent_id"),
                "territory_id": e.get("territory_id"),
                "generation": e.get("generation"),
                "tick": e.get("tick"),
                "summary": e.get("data", {}).get("summary", ""),
            }
            for e in rows
        ],
        "returned": len(rows),
    }


# ── /api/research/papers ─────────────────────────────────────────────────────

@app.get(
    "/api/research/papers",
    summary="List research papers / publications",
    tags=["Research"],
)
async def list_research_papers():
    """
    Returns research papers linked to the NPMAI Agentic World experiment,
    stored in the `research_content` Supabase table.
    """
    rows = await _query("research_content", {"type": "paper"})
    papers = sorted(rows, key=lambda r: r.get("published_at", ""), reverse=True)
    return {
        "papers": [
            {
                "id": p.get("id"),
                "title": p.get("title"),
                "abstract": p.get("abstract", ""),
                "tags": p.get("tags", []),
                "published_at": p.get("published_at"),
                "url": p.get("url", ""),
                "experiment_day": p.get("experiment_day"),
            }
            for p in papers
        ]
    }


# ── /api/research/updates ────────────────────────────────────────────────────

@app.get(
    "/api/research/updates",
    summary="Experiment updates from Sonu",
    tags=["Research"],
)
async def list_research_updates():
    """
    Returns the experiment update feed posted by Sonu Kumar.
    Shown on the website's Research Updates section.
    """
    rows = await _query("research_content", {"type": "update"})
    updates = sorted(rows, key=lambda r: r.get("published_at", ""), reverse=True)
    return {
        "updates": [
            {
                "id": u.get("id"),
                "title": u.get("title"),
                "content": u.get("content", ""),
                "tags": u.get("tags", []),
                "published_at": u.get("published_at"),
                "experiment_day": u.get("experiment_day"),
            }
            for u in updates
        ]
    }


# ═══════════════════════════════════════════════════════════════════════════════
# ── AUTHENTICATED ENDPOINTS ─────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── POST /api/auth/register ──────────────────────────────────────────────────

@app.post(
    "/api/auth/register",
    response_model=TokenResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user account",
    tags=["Auth"],
)
async def auth_register(body: RegisterRequest, request: Request):
    """
    Register a new researcher account.
    Returns a signed JWT on success.
    Rate-limited to 3 registrations per IP per hour.
    """
    ip = get_client_ip(request)
    token = await register(
        username=body.username,
        email=body.email,
        password=body.password,
        ip_address=ip,
    )
    # Decode to fill response fields
    from web.backend.auth import _decode_jwt, get_user_by_id
    payload = _decode_jwt(token)
    user = await get_user_by_id(payload["sub"])
    return TokenResponse(
        access_token=token,
        user_id=user.user_id,
        username=user.username,
        agent_slots=user.agent_slots,
        subscription_tier=user.subscription_tier,
        is_admin=user.is_admin,
    )


# ── POST /api/auth/login ─────────────────────────────────────────────────────

@app.post(
    "/api/auth/login",
    response_model=TokenResponse,
    summary="Login and receive JWT",
    tags=["Auth"],
)
async def auth_login(body: LoginRequest, request: Request):
    """
    Authenticate with email + password.
    Returns a signed JWT valid for 24 hours.
    Rate-limited to 10 attempts per IP per 10 minutes.
    """
    ip = get_client_ip(request)
    token = await login(email=body.email, password=body.password, ip_address=ip)

    from web.backend.auth import _decode_jwt, get_user_by_id
    payload = _decode_jwt(token)
    user = await get_user_by_id(payload["sub"])
    return TokenResponse(
        access_token=token,
        user_id=user.user_id,
        username=user.username,
        agent_slots=user.agent_slots,
        subscription_tier=user.subscription_tier,
        is_admin=user.is_admin,
    )


# ── POST /api/agents/register ────────────────────────────────────────────────

@app.post(
    "/api/agents/register",
    status_code=status.HTTP_201_CREATED,
    summary="Spawn a new agent in the world",
    tags=["My Agents"],
)
async def register_agent(
    body: AgentRegisterRequest,
    current_user: User = Depends(get_current_user),
):
    """
    Spawn a new agent for the authenticated user.

    - Checks the user hasn't exceeded their `agent_slots` limit.
    - Creates an initial AgentCell configuration record in Supabase.
    - Returns agent_id, genome summary, and starting territory.

    Note: actual agent spawn is picked up by WorldController on next tick.
    """
    import uuid as _uuid

    # ── check slot limit ──────────────────────────────────────────────────────
    existing = await _query("user_agents", {"user_id": current_user.user_id})
    if len(existing) >= current_user.agent_slots:
        raise HTTPException(
            403,
            f"Agent slot limit reached ({current_user.agent_slots}). "
            "Upgrade your subscription for more slots.",
        )

    # ── resolve territory ─────────────────────────────────────────────────────
    territory_id = body.territory_id
    if not territory_id:
        ter_rows = await _query("territories", {"border_policy": "OPEN"})
        if not ter_rows:
            ter_rows = await _query("territories")
        if not ter_rows:
            raise HTTPException(503, "No territories available. World may not be running.")
        # Pick territory with lowest population
        territory_id = min(
            ter_rows,
            key=lambda t: t.get("population_count", 0),
        )["territory_id"]

    agent_id = str(_uuid.uuid4())
    lineage_id = str(_uuid.uuid4())

    # ── build initial genome summary ──────────────────────────────────────────
    import random
    capability_chromosome = "".join(
        "1" if random.random() < 0.3 else "0" for _ in range(100)
    )
    # Ensure minimum 10 tools
    bits = list(capability_chromosome)
    ones = bits.count("1")
    if ones < 10:
        zeros = [i for i, b in enumerate(bits) if b == "0"]
        for i in random.sample(zeros, 10 - ones):
            bits[i] = "1"
    capability_chromosome = "".join(bits)

    genome_summary = {
        "mutation_rate": 0.01,
        "capability_count": capability_chromosome.count("1"),
        "parameter_genes": {
            "temperature": 0.7,
            "retry_limit": 3,
            "risk_tolerance": 0.5,
            "cooperation_bias": 0.6,
        },
        "custom_prompt_fragment": body.custom_prompt_fragment or "",
        "specialization": body.specialization,
    }

    # ── write spawn record ────────────────────────────────────────────────────
    now = datetime.now(timezone.utc).isoformat()
    spawn_record = {
        "agent_id": agent_id,
        "user_id": current_user.user_id,
        "name": body.name,
        "specialization": body.specialization,
        "territory_id": territory_id,
        "lineage_id": lineage_id,
        "generation": 1,
        "genome": genome_summary,
        "capability_chromosome": capability_chromosome,
        "status": "PENDING_SPAWN",
        "credits": 10.0,
        "age": 0,
        "health": 1.0,
        "born_at": now,
        "custom_prompt_fragment": body.custom_prompt_fragment or "",
    }
    await _insert("user_agents", spawn_record)
    await _insert(
        "agent_spawn_queue",
        {"agent_id": agent_id, "created_at": now, "processed": False},
    )

    logger.info(
        "Agent spawn queued: %s by user %s", agent_id, current_user.user_id
    )

    return {
        "agent_id": agent_id,
        "name": body.name,
        "lineage_id": lineage_id,
        "starting_territory": territory_id,
        "genome_summary": genome_summary,
        "status": "PENDING_SPAWN",
        "message": "Agent queued for spawn. It will appear in the world on the next tick.",
    }


# ── GET /api/my/agents ───────────────────────────────────────────────────────

@app.get(
    "/api/my/agents",
    summary="All agents belonging to authenticated user",
    tags=["My Agents"],
)
async def my_agents(current_user: User = Depends(get_current_user)):
    """
    Returns all agents the authenticated user has registered,
    with their current live status fetched from agent_snapshots.
    """
    user_agents = await _query("user_agents", {"user_id": current_user.user_id})
    result = []
    for ua in user_agents:
        agent_id = ua["agent_id"]
        # Get live snapshot
        snap_rows = await _query("agent_snapshots", {"agent_id": agent_id})
        snap = snap_rows[0] if snap_rows else {}
        result.append(
            {
                "agent_id": agent_id,
                "name": ua.get("name"),
                "specialization": ua.get("specialization", "generalist"),
                "lineage_id": ua.get("lineage_id"),
                "generation": snap.get("generation", ua.get("generation", 1)),
                "status": snap.get("status", ua.get("status", "PENDING_SPAWN")),
                "credits": round(snap.get("credits", ua.get("credits", 0)), 4),
                "age": snap.get("age", 0),
                "health": round(snap.get("health", 1.0), 4),
                "territory_id": snap.get("territory_id", ua.get("territory_id")),
                "reputation": round(snap.get("reputation", 0), 4),
                "children_count": snap.get("children_count", 0),
                "born_at": ua.get("born_at"),
            }
        )
    return {
        "agents": result,
        "slot_usage": f"{len(result)}/{current_user.agent_slots}",
    }


# ── GET /api/my/agent/{agent_id}/lineage ────────────────────────────────────

@app.get(
    "/api/my/agent/{agent_id}/lineage",
    summary="Family tree for a user-owned agent",
    tags=["My Agents"],
)
async def my_agent_lineage(
    agent_id: str,
    current_user: User = Depends(get_current_user),
):
    """
    Returns the full family tree for an agent owned by the authenticated user.
    Delegates to /api/lineage/{lineage_id} logic after ownership verification.
    """
    # Verify ownership
    owned = await _query(
        "user_agents",
        {"user_id": current_user.user_id, "agent_id": agent_id},
    )
    if not owned:
        # Check if agent descends from one of user's agents
        owned = await _query("user_agents", {"user_id": current_user.user_id})
        owned_lineages = {a.get("lineage_id") for a in owned}
        snap = await _query("agent_snapshots", {"agent_id": agent_id})
        if not snap or snap[0].get("lineage_id") not in owned_lineages:
            raise HTTPException(
                403, "You don't own this agent or any ancestor in its lineage."
            )
        lineage_id = snap[0]["lineage_id"]
    else:
        lineage_id = owned[0]["lineage_id"]

    return await get_lineage(lineage_id)


# ═══════════════════════════════════════════════════════════════════════════════
# ── ADMIN ENDPOINTS ─────────────────────────────────────────────────────────
# ═══════════════════════════════════════════════════════════════════════════════

# ── POST /api/divine/send ─────────────────────────────────────────────────────

@app.post(
    "/api/divine/send",
    summary="[ADMIN] Send divine message to agent",
    tags=["Admin — Divine Oracle"],
)
async def divine_send(
    body: DivineMessageRequest,
    admin: User = Depends(require_admin),
):
    """
    Sends a divine message from the Oracle to a specific agent.
    Agents never know this originates from a human.

    This endpoint is Sonu's direct line into the civilisation.
    """
    # Verify agent exists
    agent_rows = await _query("agent_snapshots", {"agent_id": body.agent_id})
    if not agent_rows:
        raise HTTPException(404, f"Agent '{body.agent_id}' not found.")

    agent_status = agent_rows[0].get("status")
    if agent_status == "DEAD":
        raise HTTPException(400, "Cannot send message to a dead agent.")

    # Queue divine message via Oracle
    try:
        oracle = Oracle.get_instance()
        msg_id = await oracle.send_message(
            agent_id=body.agent_id,
            raw_message=body.message,
            persona=body.persona,
            message_type=body.message_type,
            sender_user_id=admin.user_id,
        )
    except Exception as exc:
        logger.exception("Oracle send error: %s", exc)
        # Fallback: write directly to divine_message_queue table
        import uuid as _uuid
        msg_id = str(_uuid.uuid4())
        await _insert(
            "divine_message_queue",
            {
                "msg_id": msg_id,
                "agent_id": body.agent_id,
                "persona": body.persona,
                "message_type": body.message_type,
                "raw_message": body.message,
                "sender_user_id": admin.user_id,
                "queued_at": datetime.now(timezone.utc).isoformat(),
                "delivered": False,
            },
        )

    logger.info(
        "Divine message queued: %s → agent %s [%s/%s]",
        msg_id,
        body.agent_id,
        body.persona,
        body.message_type,
    )

    return {
        "msg_id": msg_id,
        "agent_id": body.agent_id,
        "persona": body.persona,
        "message_type": body.message_type,
        "status": "queued",
        "message": "Divine message queued. Agent will receive it on next tick.",
    }


# ── POST /api/research/update ─────────────────────────────────────────────────

@app.post(
    "/api/research/update",
    status_code=status.HTTP_201_CREATED,
    summary="[ADMIN] Post a research update",
    tags=["Admin — Research"],
)
async def post_research_update(
    body: ResearchUpdateRequest,
    admin: User = Depends(require_admin),
):
    """
    Posts a new research update to the public experiment feed.
    Only accessible to Sonu (admin).
    """
    import uuid as _uuid

    record_id = str(_uuid.uuid4())
    record = {
        "id": record_id,
        "type": "update",
        "title": body.title,
        "content": body.content,
        "tags": body.tags,
        "experiment_day": body.experiment_day,
        "published_at": datetime.now(timezone.utc).isoformat(),
        "author_user_id": admin.user_id,
        "author_username": admin.username,
    }
    await _insert("research_content", record)

    # Broadcast to WebSocket subscribers
    await broadcaster.broadcast_to_channel(
        channel="research",
        event={
            "type": "RESEARCH_UPDATE",
            "id": record_id,
            "title": body.title,
            "tags": body.tags,
        },
    )

    logger.info("Research update posted: %s by %s", record_id, admin.username)
    return {"id": record_id, "status": "published", "title": body.title}


# ── POST /api/world/config ────────────────────────────────────────────────────

@app.post(
    "/api/world/config",
    summary="[ADMIN] Hot-reload world configuration",
    tags=["Admin — World"],
)
async def update_world_config(
    body: WorldConfigRequest,
    admin: User = Depends(require_admin),
):
    """
    Hot-reload a world setting without restarting.
    Delegates to WorldController's config update mechanism.

    Valid keys mirror ExperimentSettings fields:
      tick_interval_seconds, max_agents_per_territory, base_existence_tax,
      reproduction_cost_per_child, migration_base_cost, etc.
    """
    try:
        settings = load_settings()
        if not hasattr(settings, body.key):
            raise HTTPException(
                400,
                f"Unknown config key '{body.key}'. "
                "See ExperimentSettings for valid keys.",
            )
        old_value = getattr(settings, body.key)
        setattr(settings, body.key, body.value)
        from config.settings import save_settings
        save_settings(settings)

        # Notify WorldController
        try:
            wc = WorldController.get_instance()
            await wc.reload_config()
        except Exception:
            pass  # WorldController may not be running in API-only mode

        # Persist in Supabase audit log
        await _insert(
            "config_change_log",
            {
                "key": body.key,
                "old_value": str(old_value),
                "new_value": str(body.value),
                "changed_by": admin.user_id,
                "changed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        logger.info(
            "World config updated: %s = %r (was %r) by %s",
            body.key,
            body.value,
            old_value,
            admin.username,
        )
        return {
            "key": body.key,
            "old_value": old_value,
            "new_value": body.value,
            "status": "applied",
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("World config update error: %s", exc)
        raise HTTPException(500, f"Failed to update config: {exc}")


# ═══════════════════════════════════════════════════════════════════════════════
# Health check
# ═══════════════════════════════════════════════════════════════════════════════

@app.get("/api/health", tags=["Meta"])
async def health():
    return {
        "status": "ok",
        "service": "NPMAI Agentic World API",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "version": "1.0.0",
    }


@app.get("/", tags=["Meta"])
async def root():
    return {
        "message": "NPMAI Agentic World API — Computational Civilisation Research",
        "docs": "/api/docs",
        "founder": "Sonu Kumar, 15, NPMAI ECOSYSTEM, Kota",
    }
