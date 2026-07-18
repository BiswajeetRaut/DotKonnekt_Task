import asyncio
import json
import logging
from collections import defaultdict

import redis.asyncio as redis
from fastapi import WebSocket

from app.config import settings

logger = logging.getLogger(__name__)

BROADCAST_CHANNEL = "alerts_broadcast"


class ConnectionManager:
    """Tracks connected dashboard clients per user, on *this* process, and
    broadcasts alert payloads only to that user's own sockets.

    `broadcast()` publishes to Redis rather than writing to local sockets
    directly — every backend instance's `_listen` loop receives the message
    and each forwards it only to whichever of its own locally-connected
    sockets match the target user_id. That's what makes alert delivery work
    when the instance that raised the alert (handling the POST /expenses
    request) isn't the same instance holding the user's WebSocket — a real
    scenario once there's more than one backend process (docs/V2_DESIGN.md
    Phase C). Broadcast fan-out is safe here (unlike the expense_created
    event bus in app/events.py) because forwarding to a local socket has no
    side effect to duplicate — an instance with no matching connection just
    no-ops.
    """

    def __init__(self) -> None:
        self._connections: dict[int, list[WebSocket]] = defaultdict(list)
        self._redis_client: redis.Redis | None = None
        self._listener_task: asyncio.Task | None = None

    def _get_client(self) -> redis.Redis:
        if self._redis_client is None:
            self._redis_client = redis.from_url(settings.redis_url, decode_responses=True)
        return self._redis_client

    async def connect(self, websocket: WebSocket, user_id: int) -> None:
        await websocket.accept()
        self._connections[user_id].append(websocket)

    def disconnect(self, websocket: WebSocket, user_id: int) -> None:
        if websocket in self._connections.get(user_id, []):
            self._connections[user_id].remove(websocket)

    async def broadcast(self, user_id: int | None, payload: dict) -> None:
        if user_id is None:
            return
        envelope = json.dumps({"user_id": user_id, "payload": payload}, default=str)
        await self._get_client().publish(BROADCAST_CHANNEL, envelope)

    async def _deliver_locally(self, user_id: int, message: str) -> None:
        stale: list[WebSocket] = []
        for connection in self._connections.get(user_id, []):
            try:
                await connection.send_text(message)
            except Exception:
                stale.append(connection)
        for connection in stale:
            self.disconnect(connection, user_id)

    async def _listen(self) -> None:
        pubsub = self._get_client().pubsub()
        await pubsub.subscribe(BROADCAST_CHANNEL)
        logger.info("ws_manager: subscribed to Redis channel '%s'", BROADCAST_CHANNEL)
        async for message in pubsub.listen():
            if message["type"] != "message":
                continue
            try:
                envelope = json.loads(message["data"])
                user_id = envelope["user_id"]
                payload = envelope["payload"]
            except (TypeError, ValueError, KeyError):
                logger.warning("ws_manager: dropping malformed broadcast %r", message.get("data"))
                continue
            await self._deliver_locally(user_id, json.dumps(payload, default=str))

    def start_listener(self) -> None:
        self._listener_task = asyncio.create_task(self._listen())

    async def stop_listener(self) -> None:
        if self._listener_task is not None:
            self._listener_task.cancel()
            try:
                await self._listener_task
            except asyncio.CancelledError:
                pass
        if self._redis_client is not None:
            await self._redis_client.aclose()


manager = ConnectionManager()
