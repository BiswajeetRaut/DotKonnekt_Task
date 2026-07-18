"""Anomaly detection service.

This module is intentionally decoupled from FastAPI/HTTP entirely — its core
function (`detect_anomaly`) takes plain numbers and returns a plain result, so it
can be unit tested or lifted into a separate worker process without touching the
CRUD API. It is wired to the CRUD layer only via `app/events.py` (pub/sub) and
`app/ws_manager.py` (push), never imported by the routers directly.

Rule: rolling per-category z-score.
  - Look at the trailing N expenses in the same category (excluding the new one).
  - Require at least MIN_SAMPLES data points before judging — a cold-start
    category shouldn't trigger false alarms.
  - z = (amount - mean) / stddev.
  - z >= CRITICAL_Z  -> "critical"
  - z >= WARNING_Z   -> "warning"
  - otherwise no alert.
"""

import logging
import statistics
from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models import Alert, Expense
from app.ws_manager import manager as ws_manager

logger = logging.getLogger(__name__)

TRAILING_WINDOW = 20
MIN_SAMPLES = 5
WARNING_Z = 2.5
CRITICAL_Z = 4.0


@dataclass
class AnomalyResult:
    severity: str
    z_score: float
    reason: str


def detect_anomaly(amount: float, history: list[float]) -> AnomalyResult | None:
    """Pure rule evaluation — no DB, no I/O. Easy to unit test in isolation."""
    if len(history) < MIN_SAMPLES:
        return None

    mean = statistics.fmean(history)
    stddev = statistics.pstdev(history)

    if stddev == 0:
        if amount == mean:
            return None
        # All prior spend identical; any deviation is notable but we can't
        # compute a z-score, so flag as a moderate warning.
        return AnomalyResult(
            severity="warning",
            z_score=0.0,
            reason=f"Amount {amount:.2f} differs from the constant historical value {mean:.2f}",
        )

    z = (amount - mean) / stddev
    if z < WARNING_Z:
        return None

    severity = "critical" if z >= CRITICAL_Z else "warning"
    reason = (
        f"Amount {amount:.2f} is {z:.2f} standard deviations above the "
        f"category mean ({mean:.2f}, n={len(history)})"
    )
    return AnomalyResult(severity=severity, z_score=round(z, 2), reason=reason)


def evaluate_expense(db: Session, expense_id: int) -> Alert | None:
    """DB-aware wrapper: fetches history, runs the rule, persists an Alert."""
    expense = db.get(Expense, expense_id)
    if expense is None:
        return None

    history_rows = db.execute(
        select(Expense.amount)
        .where(Expense.category == expense.category, Expense.id != expense.id)
        .order_by(Expense.occurred_at.desc())
        .limit(TRAILING_WINDOW)
    ).scalars().all()
    history = [float(v) for v in history_rows]

    result = detect_anomaly(float(expense.amount), history)
    if result is None:
        return None

    alert = Alert(
        expense_id=expense.id,
        reason=result.reason,
        severity=result.severity,
        z_score=Decimal(str(result.z_score)),
    )
    db.add(alert)
    db.commit()
    db.refresh(alert)
    return alert


async def handle_expense_created(expense_id: int) -> None:
    """Event bus subscriber. Owns its own DB session — runs outside request scope."""
    db = SessionLocal()
    try:
        alert = evaluate_expense(db, expense_id)
    finally:
        db.close()

    if alert is not None:
        logger.info("Anomaly detected: expense_id=%s severity=%s", expense_id, alert.severity)
        await ws_manager.broadcast(
            {
                "type": "alert",
                "id": alert.id,
                "expense_id": alert.expense_id,
                "severity": alert.severity,
                "reason": alert.reason,
                "z_score": alert.z_score,
                "created_at": alert.created_at,
            }
        )
