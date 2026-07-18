"""Tiny in-process event bus.

The CRUD router publishes an `expense.created` event and knows nothing about who
listens. The analytics/anomaly service is the sole subscriber (wired in
app/main.py at startup). This keeps the router decoupled from anomaly logic —
swapping this for a real broker (Redis Streams/Kafka) later only means changing
`publish`/`subscribe`, not the router or the service.
"""

import logging
from typing import Awaitable, Callable

logger = logging.getLogger(__name__)

Handler = Callable[[int], Awaitable[None]]

_subscribers: list[Handler] = []


def subscribe(handler: Handler) -> None:
    _subscribers.append(handler)


async def publish_expense_created(expense_id: int) -> None:
    for handler in _subscribers:
        try:
            await handler(expense_id)
        except Exception:
            logger.exception("Subscriber failed handling expense_created for id=%s", expense_id)
