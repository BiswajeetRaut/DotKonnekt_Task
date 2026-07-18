"""Redis Pub/Sub-backed event bus.

Replaces the v1.0 in-process pub/sub (a plain Python list of callbacks) with
a real broker, per docs/V2_DESIGN.md Phase C. The router still only knows
about `publish_expense_created`/`subscribe` — this swap is entirely internal.

Correctness caveat, stated explicitly rather than silently glossed over:
Pub/Sub is fan-out — every subscriber process receives every message. That's
exactly right for the single backend instance this app runs today. If the
analytics service is ever scaled to *multiple* consumer instances, this
would need to move to Redis Streams with a consumer group, so exactly one
consumer processes each event — otherwise N instances would each run
detection on the same expense and could each write a duplicate Alert.
Not implemented here (that's real added complexity — consumer groups, XACK,
pending-entry handling — disproportionate to a single-instance deployment);
flagged as a follow-up in docs/V2_DESIGN.md rather than built speculatively.
"""

import asyncio
import logging
from typing import Awaitable, Callable

import redis.asyncio as redis

from app.config import settings

logger = logging.getLogger(__name__)

CHANNEL = "expense_created"

Handler = Callable[[int], Awaitable[None]]

_subscribers: list[Handler] = []
_redis_client: redis.Redis | None = None
_listener_task: asyncio.Task | None = None


def _get_client() -> redis.Redis:
    global _redis_client
    if _redis_client is None:
        _redis_client = redis.from_url(settings.redis_url, decode_responses=True)
    return _redis_client


def subscribe(handler: Handler) -> None:
    _subscribers.append(handler)


async def publish_expense_created(expense_id: int) -> None:
    await _get_client().publish(CHANNEL, str(expense_id))


async def _listen() -> None:
    pubsub = _get_client().pubsub()
    await pubsub.subscribe(CHANNEL)
    logger.info("events: subscribed to Redis channel '%s'", CHANNEL)
    async for message in pubsub.listen():
        if message["type"] != "message":
            continue
        try:
            expense_id = int(message["data"])
        except (TypeError, ValueError):
            logger.warning("events: dropping malformed message %r", message.get("data"))
            continue
        for handler in _subscribers:
            try:
                await handler(expense_id)
            except Exception:
                logger.exception("Subscriber failed handling expense_created for id=%s", expense_id)


def start_listener() -> None:
    """Call once at app startup — runs the subscribe loop for the app's lifetime."""
    global _listener_task
    _listener_task = asyncio.create_task(_listen())


async def stop_listener() -> None:
    if _listener_task is not None:
        _listener_task.cancel()
        try:
            await _listener_task
        except asyncio.CancelledError:
            pass
    if _redis_client is not None:
        await _redis_client.aclose()
