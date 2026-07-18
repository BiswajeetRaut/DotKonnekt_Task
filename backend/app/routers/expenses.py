from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session

from app.database import get_db
from app.events import publish_expense_created
from app.models import Expense
from app.schemas import ExpenseCreate, ExpenseOut, ExpenseUpdate

router = APIRouter(prefix="/expenses", tags=["expenses"])


@router.post("", response_model=ExpenseOut, status_code=201)
def create_expense(payload: ExpenseCreate, background_tasks: BackgroundTasks, db: Session = Depends(get_db)):
    expense = Expense(
        amount=payload.amount,
        category=payload.category,
        description=payload.description,
        occurred_at=payload.occurred_at,
    )
    db.add(expense)
    db.commit()
    db.refresh(expense)

    # CRUD layer only publishes the event — it has no knowledge of anomaly
    # detection. The analytics service (app/analytics) is the sole subscriber.
    background_tasks.add_task(publish_expense_created, expense.id)

    return expense


@router.get("", response_model=list[ExpenseOut])
def list_expenses(
    category: str | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
):
    stmt = select(Expense)
    if category:
        stmt = stmt.where(Expense.category == category)
    if start_date:
        stmt = stmt.where(Expense.occurred_at >= start_date)
    if end_date:
        stmt = stmt.where(Expense.occurred_at <= end_date)
    stmt = stmt.order_by(Expense.occurred_at.desc()).limit(limit).offset(offset)

    return db.execute(stmt).scalars().all()


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db)):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


@router.put("/{expense_id}", response_model=ExpenseOut)
def update_expense(expense_id: int, payload: ExpenseUpdate, db: Session = Depends(get_db)):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")

    # Optimistic locking: the UPDATE only succeeds if the row's version still
    # matches what the client last read. rowcount == 0 means someone else wrote
    # to this record first -> surface a 409 rather than silently overwriting.
    result = db.execute(
        update(Expense)
        .where(Expense.id == expense_id, Expense.version == payload.version)
        .values(
            amount=payload.amount,
            category=payload.category,
            description=payload.description,
            occurred_at=payload.occurred_at,
            version=Expense.version + 1,
        )
    )
    if result.rowcount == 0:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Expense was modified by another request. Refetch and retry.",
        )
    db.commit()
    db.refresh(expense)
    return expense


@router.delete("/{expense_id}", status_code=204)
def delete_expense(expense_id: int, db: Session = Depends(get_db)):
    expense = db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(status_code=404, detail="Expense not found")
    db.delete(expense)
    db.commit()
    return None
