"""One-time backfill: indexes expenses/alerts that existed before the
chatbot feature was added (and so never went through the normal
expense_created/alert_created event path). Safe to re-run — upserts on
(source_type, source_id), so already-indexed rows just get re-embedded.

Run with: python -m app.chat.backfill
"""
import asyncio

from app.chat.indexer import handle_alert_created_index, handle_expense_created_index
from app.database import SessionLocal
from app.models import Alert, Expense


async def run() -> None:
    db = SessionLocal()
    try:
        expense_ids = [e.id for e in db.query(Expense.id).all()]
        alert_ids = [a.id for a in db.query(Alert.id).all()]
    finally:
        db.close()

    print(f"Backfilling {len(expense_ids)} expenses and {len(alert_ids)} alerts...")
    for expense_id in expense_ids:
        await handle_expense_created_index(expense_id)
    for alert_id in alert_ids:
        await handle_alert_created_index(alert_id)
    print("Done.")


if __name__ == "__main__":
    asyncio.run(run())
