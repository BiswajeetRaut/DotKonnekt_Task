"""Backfills realistic historical expenses so the anomaly rule and dashboard chart
have real signal on first run. Also inserts a couple of deliberate outliers so a
freshly-seeded dashboard already shows alerts.

Run with: python -m app.seed
"""
import random
from datetime import datetime, timedelta, timezone

from app.analytics.anomaly_service import evaluate_expense
from app.database import Base, SessionLocal, engine
from app.models import Expense

random.seed(7)

CATEGORIES = {
    "food": (15, 45),
    "transport": (10, 35),
    "software": (20, 120),
    "entertainment": (10, 60),
}

OUTLIERS = [
    ("food", 480),
    ("software", 1500),
]


def run() -> None:
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(Expense).count() > 0:
            print("Expenses already exist — skipping seed.")
            return

        now = datetime.now(timezone.utc)
        rows = []
        for category, (lo, hi) in CATEGORIES.items():
            for i in range(20):
                amount = round(random.uniform(lo, hi), 2)
                occurred_at = now - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23))
                rows.append(Expense(amount=amount, category=category, description=f"{category} expense", occurred_at=occurred_at))

        db.add_all(rows)
        db.commit()

        outlier_ids = []
        for category, amount in OUTLIERS:
            outlier = Expense(
                amount=amount,
                category=category,
                description="unusually large charge",
                occurred_at=now - timedelta(hours=1),
            )
            db.add(outlier)
            db.commit()
            db.refresh(outlier)
            outlier_ids.append(outlier.id)

        print(f"Seeded {len(rows)} baseline expenses + {len(outlier_ids)} outliers.")

        # Run detection synchronously here (seed script isn't going through the
        # HTTP/event pipeline) so the dashboard has alerts to show on first load.
        for expense_id in outlier_ids:
            alert = evaluate_expense(db, expense_id)
            if alert:
                print(f"  -> alert created for expense {expense_id}: {alert.reason}")
    finally:
        db.close()


if __name__ == "__main__":
    run()
