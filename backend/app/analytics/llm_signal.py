"""LLM-based secondary anomaly signal — a second, independent detector.

Catches what the rolling z-score rule (anomaly_service.py) structurally
cannot: a semantic mismatch between an expense's description and its
category (e.g. "grocery run" logged under "software"), even when the amount
itself isn't a numeric outlier. Subscribes to the same expense.created event
as the rule-based detector — a second, independent subscriber, not a
replacement; both can fire on the same expense, and each writes its own
Alert with `source` set to say which one it came from.

Cost control: an LLM call on every single expense would be needless spend
for the overwhelming majority of unremarkable entries. Gate — only invoke
the LLM when:
  - Cold start: not enough history for the numeric rule to say anything
    (a brand-new category/user has zero baseline) — the LLM is the only
    signal available in that case.
  - Borderline z-score: elevated but below the rule's own WARNING_Z
    threshold — a case the rule wasn't confident about either way, worth a
    second opinion.
Otherwise (very ordinary, or already flagged by the rule) — skip.

Known gap, stated honestly rather than silently accepted: a normal-looking
amount with a wildly wrong category (no numeric signal at all — e.g. a gift
purchase at a perfectly typical software-subscription price, filed under
"software") won't hit either gate and won't reach the LLM. Running the LLM
on every expense would catch that too, at full per-expense cost — worth a
config toggle if that gap matters in practice; not built here to keep this
phase's cost profile sane by default.
"""

import logging
import statistics

from openai import AsyncOpenAI
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.anomaly_service import MIN_SAMPLES, WARNING_Z, _fetch_history
from app.config import settings
from app.database import SessionLocal
from app.models import Alert, Category, Expense
from app.ws_manager import manager as ws_manager

logger = logging.getLogger(__name__)

MODEL = "gpt-4o-mini"
BORDERLINE_Z_FLOOR = 1.0
RECENT_SAMPLE_SIZE = 10

_client: AsyncOpenAI | None = None


def _get_client() -> AsyncOpenAI | None:
    global _client
    if not settings.openai_api_key:
        return None
    if _client is None:
        _client = AsyncOpenAI(api_key=settings.openai_api_key)
    return _client


class LLMJudgment(BaseModel):
    flagged: bool
    reason: str


def _should_invoke(amount: float, history: list[float]) -> bool:
    """Pure gate logic — no I/O, easy to unit test independently of the OpenAI call."""
    if len(history) < MIN_SAMPLES:
        return True
    mean = statistics.fmean(history)
    stddev = statistics.pstdev(history)
    if stddev == 0:
        return False
    z = abs((amount - mean) / stddev)
    return BORDERLINE_Z_FLOOR <= z < WARNING_Z


async def _judge(expense: Expense, category_name: str, recent_lines: str) -> LLMJudgment | None:
    client = _get_client()
    if client is None:
        logger.debug("OPENAI_API_KEY not configured — skipping LLM anomaly check")
        return None

    prompt = (
        f"A user logged an expense of ${expense.amount} in category '{category_name}' "
        f'with description: "{expense.description or "(none)"}".\n\n'
        f"Their recent expenses in this category:\n{recent_lines}\n\n"
        "Does the description seem inconsistent with the category, or otherwise look "
        "like a mistake (wrong category, placeholder/test text, an obviously different "
        "type of spending than this category usually contains)? Only flag a clear "
        "mismatch — do not flag just because the amount is unusual; that's handled by "
        "a separate statistical check."
    )

    try:
        completion = await client.beta.chat.completions.parse(
            model=MODEL,
            messages=[{"role": "user", "content": prompt}],
            response_format=LLMJudgment,
        )
        return completion.choices[0].message.parsed
    except Exception:
        logger.exception("LLM anomaly check failed for expense_id=%s", expense.id)
        return None


async def handle_expense_created_llm(expense_id: int) -> None:
    """Event bus subscriber — independent of, and in addition to, the rule-based one."""
    db: Session = SessionLocal()
    try:
        expense = db.get(Expense, expense_id)
        if expense is None:
            return

        history = _fetch_history(db, expense, scoped_to_user=True)
        if len(history) < MIN_SAMPLES:
            history = _fetch_history(db, expense, scoped_to_user=False)

        if not _should_invoke(float(expense.amount), history):
            return

        category = db.get(Category, expense.category_id)
        category_name = category.name if category is not None else "category"

        recent = db.execute(
            select(Expense)
            .where(Expense.category_id == expense.category_id, Expense.id != expense.id)
            .order_by(Expense.occurred_at.desc())
            .limit(RECENT_SAMPLE_SIZE)
        ).scalars().all()
        recent_lines = (
            "\n".join(f"- {e.description or '(no description)'}: ${e.amount}" for e in recent)
            or "(no prior expenses in this category)"
        )

        judgment = await _judge(expense, category_name, recent_lines)
        if judgment is None or not judgment.flagged:
            return

        alert = Alert(
            expense_id=expense.id,
            reason=judgment.reason,
            severity="warning",
            source="llm",
            z_score=None,
        )
        db.add(alert)
        db.commit()
        db.refresh(alert)
        user_id = expense.user_id
    finally:
        db.close()

    logger.info("LLM anomaly flagged: expense_id=%s", expense_id)
    await ws_manager.broadcast(
        user_id,
        {
            "type": "alert",
            "id": alert.id,
            "expense_id": alert.expense_id,
            "severity": alert.severity,
            "source": alert.source,
            "reason": alert.reason,
            "z_score": None,
            "created_at": alert.created_at,
        },
    )
