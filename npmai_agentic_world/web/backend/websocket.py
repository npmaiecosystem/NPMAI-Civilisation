"""
web/backend/websocket.py
────────────────────────
WebSocket server for NPMAI Agentic World real-time streaming.

Channels
────────
/ws/world               — all world events, with optional category filter
/ws/agent/{agent_id}    — events for a specific agent
/ws/territory/{territory_id} — events for a specific territory

ConnectionManager
─────────────────
- Handles 1000+ concurrent connections via asyncio sets
- Per-channel subscription routing (no broadcast-to-all overhead)
- Heartbeat every 30 s (ping/pong)
- Reconnection guidance in error payloads

WorldEventBroadcaster
─────────────────────
- Subscribes to EventLogger's event stream
- Routes each WorldEvent to the right channel sets
- Formats events for frontend consumption (camelCase JSON)
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
import uuid
from collections import defaultdict
from dataclasses import asdict, dataclass
from typing import Any, Dict, Optional, Set

from fastapi import APIRouter, WebSocket, WebSocketDisconnect, Query

logger = logging.getLogger(__name__)

ws_router = APIRouter()

# ═══════════════════════════════════════════════════════════════════════════════
# Connection model
# ═══════════════════════════════════════════════════════════════════════════════

@dataclass
class WSConnection:
    """Metadata for one WebSocket connection."""
    conn_id: str
    websocket: WebSocket
    channel: str          # "world" | "agent:<id>" | "territory:<id>"
    category_filter: Optional[str]  # e.g. "ECONOMY", "DIVINE" — prefix match
    connected_at: float
    last_ping: float
    authenticated: bool = False


# ═══════════════════════════════════════════════════════════════════════════════
# Connection Manager
# ═══════════════════════════════════════════════════════════════════════════════

class ConnectionManager:
    """
    Manages all active WebSocket connections.

    Channels are free-form strings:
      "world"                — all-world subscribers
      "agent:<agent_id>"     — per-agent subscribers
      "territory:<terr_id>"  — per-territory subscribers
      "research"             — research update feed
    """

    HEARTBEAT_INTERVAL: int = 30   # seconds
    STALE_TIMEOUT: int = 90        # seconds — drop silent connections

    def __init__(self) -> None:
        # channel → set of WSConnection
        self._channels: dict[str, set[WSConnection]] = defaultdict(set)
        # conn_id → WSConnection (global index)
        self._all: dict[str, WSConnection] = {}
        self._lock = asyncio.Lock()
        self._heartbeat_task: Optional[asyncio.Task] = None

    # ── lifecycle ──────────────────────────────────────────────────────────

    async def startup(self) -> None:
        self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())
        logger.info("ConnectionManager started.")

    async def shutdown(self) -> None:
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
        # Close all
        async with self._lock:
            for conn in list(self._all.values()):
                try:
                    await conn.websocket.close(code=1001, reason="Server shutting down")
                except Exception:
                    pass
            self._all.clear()
            self._channels.clear()
        logger.info("ConnectionManager shut down.")

    # ── connect / disconnect ──────────────────────────────────────────────

    async def connect(
        self,
        websocket: WebSocket,
        channel: str,
        category_filter: Optional[str] = None,
    ) -> WSConnection:
        await websocket.accept()
        now = time.monotonic()
        conn = WSConnection(
            conn_id=str(uuid.uuid4()),
            websocket=websocket,
            channel=channel,
            category_filter=category_filter.upper() if category_filter else None,
            connected_at=now,
            last_ping=now,
        )
        async with self._lock:
            self._channels[channel].add(conn)
            self._all[conn.conn_id] = conn

        logger.debug(
            "WS connected: %s on channel '%s' (total=%d)",
            conn.conn_id[:8],
            channel,
            len(self._all),
        )

        # Send welcome frame
        await self._send(
            conn,
            {
                "type": "CONNECTED",
                "conn_id": conn.conn_id,
                "channel": channel,
                "category_filter": category_filter,
                "server_time": time.time(),
            },
        )
        return conn

    async def disconnect(self, conn: WSConnection) -> None:
        async with self._lock:
            self._channels[conn.channel].discard(conn)
            if not self._channels[conn.channel]:
                del self._channels[conn.channel]
            self._all.pop(conn.conn_id, None)

        logger.debug(
            "WS disconnected: %s (total=%d)", conn.conn_id[:8], len(self._all)
        )

    # ── broadcast ─────────────────────────────────────────────────────────

    async def broadcast_to_channel(self, channel: str, event: dict) -> None:
        """
        Send a JSON event to all connections subscribed to `channel`,
        applying each connection's category_filter if set.
        """
        async with self._lock:
            conns = set(self._channels.get(channel, set()))

        dead: list[WSConnection] = []
        for conn in conns:
            if not self._passes_filter(conn, event):
                continue
            ok = await self._send(conn, event)
            if not ok:
                dead.append(conn)

        for conn in dead:
            await self.disconnect(conn)

    async def broadcast_world_event(self, event: dict) -> None:
        """
        Broadcast a world event to:
          1. All /ws/world connections (filtered)
          2. Any /ws/agent/<id> if event.agent_id matches
          3. Any /ws/territory/<id> if event.territory_id matches
        """
        # World channel
        await self.broadcast_to_channel("world", event)

        # Agent-specific channel
        agent_id = event.get("agent_id") or event.get("agentId")
        if agent_id:
            await self.broadcast_to_channel(f"agent:{agent_id}", event)

        # Territory-specific channel
        territory_id = event.get("territory_id") or event.get("territoryId")
        if territory_id:
            await self.broadcast_to_channel(f"territory:{territory_id}", event)

    # ── internal helpers ───────────────────────────────────────────────────

    @staticmethod
    def _passes_filter(conn: WSConnection, event: dict) -> bool:
        if conn.category_filter is None:
            return True
        event_type = event.get("event_type") or event.get("type") or ""
        return event_type.upper().startswith(conn.category_filter)

    async def _send(self, conn: WSConnection, data: dict) -> bool:
        """Returns False if send fails (connection dead)."""
        try:
            await conn.websocket.send_text(json.dumps(data, default=str))
            return True
        except Exception as exc:
            logger.debug("WS send failed for %s: %s", conn.conn_id[:8], exc)
            return False

    async def _heartbeat_loop(self) -> None:
        """Sends a ping every HEARTBEAT_INTERVAL seconds; culls stale connections."""
        while True:
            await asyncio.sleep(self.HEARTBEAT_INTERVAL)
            now = time.monotonic()
            async with self._lock:
                all_conns = list(self._all.values())

            dead: list[WSConnection] = []
            for conn in all_conns:
                # Cull stale (no ping response)
                if now - conn.last_ping > self.STALE_TIMEOUT:
                    dead.append(conn)
                    continue
                ok = await self._send(
                    conn, {"type": "PING", "server_time": time.time()}
                )
                if not ok:
                    dead.append(conn)

            for conn in dead:
                await self.disconnect(conn)

            if dead:
                logger.debug("Heartbeat culled %d stale connections.", len(dead))

    async def handle_pong(self, conn: WSConnection) -> None:
        """Update last_ping timestamp on receiving PONG from client."""
        conn.last_ping = time.monotonic()

    @property
    def connection_count(self) -> int:
        return len(self._all)

    @property
    def channel_stats(self) -> dict[str, int]:
        return {ch: len(conns) for ch, conns in self._channels.items() if conns}


# ═══════════════════════════════════════════════════════════════════════════════
# World Event Broadcaster
# ═══════════════════════════════════════════════════════════════════════════════

class WorldEventBroadcaster:
    """
    Subscribes to the EventLogger's async event stream and broadcasts
    each WorldEvent to the appropriate WebSocket channels.

    Also exposes `broadcast_to_channel` for direct server-side use
    (e.g. research update notifications from api.py).
    """

    def __init__(self, manager: ConnectionManager) -> None:
        self._manager = manager
        self._task: Optional[asyncio.Task] = None
        self._event_queue: asyncio.Queue[dict] = asyncio.Queue(maxsize=10_000)

    async def startup(self) -> None:
        """Start the background broadcast loop and hook into EventLogger."""
        self._task = asyncio.create_task(self._broadcast_loop())
        # Hook into EventLogger if available
        try:
            from data.event_logger import EventLogger
            el = EventLogger.get_instance()
            el.register_broadcast_callback(self._enqueue_event)
            logger.info("WorldEventBroadcaster hooked into EventLogger.")
        except Exception as exc:
            logger.warning(
                "Could not hook into EventLogger: %s. "
                "Events must be enqueued manually via push_event().",
                exc,
            )
        await self._manager.startup()

    async def shutdown(self) -> None:
        if self._task:
            self._task.cancel()
        await self._manager.shutdown()

    def _enqueue_event(self, event_data: dict) -> None:
        """
        Synchronous callback called by EventLogger on every logged event.
        Puts the event into the asyncio queue.
        """
        try:
            self._event_queue.put_nowait(event_data)
        except asyncio.QueueFull:
            logger.warning("WebSocket event queue full — dropping event.")

    async def push_event(self, event_data: dict) -> None:
        """
        Async version — can be called from async code paths.
        """
        try:
            self._event_queue.put_nowait(event_data)
        except asyncio.QueueFull:
            logger.warning("WebSocket event queue full — dropping event.")

    async def broadcast_to_channel(self, channel: str, event: dict) -> None:
        """Direct broadcast to a named channel (bypasses queue)."""
        await self._manager.broadcast_to_channel(channel, event)

    async def _broadcast_loop(self) -> None:
        """Consume events from queue and broadcast to WebSocket clients."""
        while True:
            try:
                raw_event = await asyncio.wait_for(
                    self._event_queue.get(), timeout=5.0
                )
                formatted = _format_event_for_frontend(raw_event)
                await self._manager.broadcast_world_event(formatted)
                self._event_queue.task_done()
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception as exc:
                logger.exception("Broadcast loop error: %s", exc)

    @property
    def queue_size(self) -> int:
        return self._event_queue.qsize()


def _format_event_for_frontend(event: dict) -> dict:
    """
    Normalise a WorldEvent dict for frontend consumption.
    Converts snake_case keys to camelCase and flattens data payload.
    """
    data_payload = event.get("data", {}) or {}
    return {
        "type": "WORLD_EVENT",
        "eventId": event.get("event_id", ""),
        "eventType": event.get("event_type", ""),
        "timestamp": event.get("timestamp", time.time() * 1000),
        "agentId": event.get("agent_id"),
        "territoryId": event.get("territory_id"),
        "generation": event.get("generation"),
        "tick": event.get("tick"),
        "experimentDay": event.get("experiment_day"),
        "summary": data_payload.get("summary", ""),
        "data": data_payload,
        # Keep originals too for filter matching
        "agent_id": event.get("agent_id"),
        "territory_id": event.get("territory_id"),
        "event_type": event.get("event_type", ""),
    }


# ═══════════════════════════════════════════════════════════════════════════════
# Module-level singletons (shared with api.py)
# ═══════════════════════════════════════════════════════════════════════════════

_manager = ConnectionManager()
broadcaster = WorldEventBroadcaster(_manager)


# ═══════════════════════════════════════════════════════════════════════════════
# WebSocket route handlers
# ═══════════════════════════════════════════════════════════════════════════════

@ws_router.websocket("/ws/world")
async def ws_world(
    websocket: WebSocket,
    category: Optional[str] = Query(
        default=None,
        description=(
            "Optional event-type prefix filter. "
            "Examples: ECONOMY, DIVINE, GOVERNANCE, COGNITION, BAD_ACTIVITY"
        ),
    ),
):
    """
    /ws/world — streams all world events to connected browsers.

    Client messages recognised:
      {"type": "PONG"}                    — heartbeat response
      {"type": "SET_FILTER", "category": "ECONOMY"} — change filter live
      {"type": "PING"}                    — client-initiated ping (server responds PONG)

    Server messages sent:
      {"type": "CONNECTED", ...}          — on connect
      {"type": "PING", "server_time": N}  — heartbeat
      {"type": "PONG"}                    — response to client ping
      {"type": "WORLD_EVENT", ...}        — world event
    """
    conn = await _manager.connect(websocket, channel="world", category_filter=category)
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "").upper()

            if msg_type == "PONG":
                await _manager.handle_pong(conn)

            elif msg_type == "PING":
                await websocket.send_text(
                    json.dumps({"type": "PONG", "server_time": time.time()})
                )

            elif msg_type == "SET_FILTER":
                new_cat = msg.get("category")
                conn.category_filter = new_cat.upper() if new_cat else None
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "FILTER_UPDATED",
                            "category_filter": conn.category_filter,
                        }
                    )
                )

            elif msg_type == "GET_STATS":
                await websocket.send_text(
                    json.dumps(
                        {
                            "type": "STATS",
                            "connection_count": _manager.connection_count,
                            "channel_stats": _manager.channel_stats,
                            "queue_size": broadcaster.queue_size,
                        }
                    )
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WS world error for %s: %s", conn.conn_id[:8], exc)
    finally:
        await _manager.disconnect(conn)


@ws_router.websocket("/ws/agent/{agent_id}")
async def ws_agent(
    websocket: WebSocket,
    agent_id: str,
    category: Optional[str] = Query(default=None),
):
    """
    /ws/agent/{agent_id} — streams events for one specific agent.

    Useful for the "Watch My Agent" feature on the website.
    Client receives every event where event.agent_id == agent_id.
    """
    channel = f"agent:{agent_id}"
    conn = await _manager.connect(
        websocket, channel=channel, category_filter=category
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "").upper()
            if msg_type == "PONG":
                await _manager.handle_pong(conn)
            elif msg_type == "PING":
                await websocket.send_text(
                    json.dumps({"type": "PONG", "server_time": time.time()})
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug("WS agent error for %s/%s: %s", agent_id[:8], conn.conn_id[:8], exc)
    finally:
        await _manager.disconnect(conn)


@ws_router.websocket("/ws/territory/{territory_id}")
async def ws_territory(
    websocket: WebSocket,
    territory_id: str,
    category: Optional[str] = Query(default=None),
):
    """
    /ws/territory/{territory_id} — streams events for one territory.

    Frontend uses this to drive live territory map overlays.
    """
    channel = f"territory:{territory_id}"
    conn = await _manager.connect(
        websocket, channel=channel, category_filter=category
    )
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue

            msg_type = msg.get("type", "").upper()
            if msg_type == "PONG":
                await _manager.handle_pong(conn)
            elif msg_type == "PING":
                await websocket.send_text(
                    json.dumps({"type": "PONG", "server_time": time.time()})
                )

    except WebSocketDisconnect:
        pass
    except Exception as exc:
        logger.debug(
            "WS territory error for %s/%s: %s",
            territory_id[:8],
            conn.conn_id[:8],
            exc,
        )
    finally:
        await _manager.disconnect(conn)


@ws_router.websocket("/ws/research")
async def ws_research(websocket: WebSocket):
    """
    /ws/research — streams research update notifications.
    Lightweight channel for the website's research feed section.
    """
    conn = await _manager.connect(websocket, channel="research")
    try:
        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if msg.get("type", "").upper() == "PONG":
                await _manager.handle_pong(conn)
    except WebSocketDisconnect:
        pass
    finally:
        await _manager.disconnect(conn)
