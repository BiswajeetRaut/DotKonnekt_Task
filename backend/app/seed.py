"""Backfills realistic historical expenses so the anomaly rule and dashboard chart
have real signal on first run. Also inserts a couple of deliberate outliers so a
freshly-seeded dashboard already shows alerts.

Seeds data onto the demo@example.com account created by the auth migration
(0002_users_and_expense_owner.py) — run `alembic upgrade head` first.

Run with: python -m app.seed
"""
import random
from datetime import datetime, timedelta, timezone

from app.analytics.anomaly_service import evaluate_expense
from app.database import SessionLocal
from app.models import Category, Expense, User

random.seed(7)

DEMO_EMAIL = "demo@example.com"

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
    db = SessionLocal()
    try:
        demo_user = db.query(User).filter(User.email == DEMO_EMAIL).first()
        if demo_user is None:
            print(f"No {DEMO_EMAIL} account found — run 'alembic upgrade head' first.")
            return

        if db.query(Expense).filter(Expense.user_id == demo_user.id).count() > 0:
            print("Demo account already has expenses — skipping seed.")
            return

        categories: dict[str, Category] = {}
        for name in CATEGORIES:
            category = db.query(Category).filter(Category.user_id == demo_user.id, Category.name == name).first()
            if category is None:
                category = Category(user_id=demo_user.id, name=name)
                db.add(category)
                db.commit()
                db.refresh(category)
            categories[name] = category

        now = datetime.now(timezone.utc)
        rows = []
        for name, (lo, hi) in CATEGORIES.items():
            for _ in range(20):
                amount = round(random.uniform(lo, hi), 2)
                occurred_at = now - timedelta(days=random.randint(0, 60), hours=random.randint(0, 23))
                rows.append(
                    Expense(
                        user_id=demo_user.id,
                        category_id=categories[name].id,
                        amount=amount,
                        description=f"{name} expense",
                        occurred_at=occurred_at,
                    )
                )

        db.add_all(rows)
        db.commit()

        outlier_ids = []
        for name, amount in OUTLIERS:
            outlier = Expense(
                user_id=demo_user.id,
                category_id=categories[name].id,
                amount=amount,
                description="unusually large charge",
                occurred_at=now - timedelta(hours=1),
            )
            db.add(outlier)
            db.commit()
            db.refresh(outlier)
            outlier_ids.append(outlier.id)

        print(f"Seeded {len(rows)} baseline expenses + {len(outlier_ids)} outliers for {DEMO_EMAIL}.")

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
