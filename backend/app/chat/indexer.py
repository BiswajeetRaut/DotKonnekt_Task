"""Embeds new expenses/alerts into `knowledge_chunks` so the chatbot can find
them via semantic search. Subscribes to `expense_created`/`alert_created` —
same "subscribe, don't couple" pattern as the anomaly detectors; this module
has no idea the CRUD routers or analytics services exist.
"""

import logging

from sqlalchemy.dialects.postgresql import insert as pg_insert

from app.chat.embeddings import embed
from app.database import SessionLocal
from app.models import Alert, Expense, KnowledgeChunk

logger = logging.getLogger(__name__)


def _upsert_chunk(db, *, user_id: int, source_type: str, source_id: int, content: str, embedding: list[float]) -> None:
    stmt = pg_insert(KnowledgeChunk).values(
        user_id=user_id,
        source_type=source_type,
        source_id=source_id,
        content=content,
        embedding=embedding,
    )
    stmt = stmt.on_conflict_do_update(
        constraint="uq_knowledge_chunks_source",
        set_={"content": stmt.excluded.content, "embedding": stmt.excluded.embedding},
    )
    db.execute(stmt)
    db.commit()


async def handle_expense_created_index(expense_id: int) -> None:
    db = SessionLocal()
    try:
        expense = db.get(Expense, expense_id)
        if expense is None:
            return
        content = (
            f"Expense of ${expense.amount} in category '{expense.category.name}' "
            f"on {expense.occurred_at.date()}. Description: {expense.description or '(none)'}."
        )
        vector = await embed(content)
        if vector is None:
            return
        _upsert_chunk(
            db,
            user_id=expense.user_id,
            source_type="expense",
            source_id=expense.id,
            content=content,
            embedding=vector,
        )
    finally:
        db.close()


async def handle_alert_created_index(alert_id: int) -> None:
    db = SessionLocal()
    try:
        alert = db.get(Alert, alert_id)
        if alert is None:
            return
        expense = alert.expense
        content = (
            f"Alert ({alert.severity}, detected by {alert.source}) on "
            f"{expense.occurred_at.date()}: {alert.reason}. Related expense: "
            f"${expense.amount} in category '{expense.category.name}', "
            f"description: {expense.description or '(none)'}."
        )
        vector = await embed(content)
        if vector is None:
            return
        _upsert_chunk(
            db,
            user_id=expense.user_id,
            source_type="alert",
            source_id=alert.id,
            content=content,
            embedding=vector,
        )
    finally:
        db.close()
