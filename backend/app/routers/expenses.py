from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Query
from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from app.database import get_db
from app.dependencies import get_current_user
from app.events import publish_expense_created
from app.models import Category, Expense, User
from app.schemas import ExpenseCreate, ExpenseOut, ExpenseUpdate

router = APIRouter(prefix="/expenses", tags=["expenses"])


def _get_owned_category(db: Session, category_id: int, user_id: int) -> Category:
    category = db.get(Category, category_id)
    if category is None or category.user_id != user_id:
        raise HTTPException(status_code=400, detail="Category not found")
    return category


@router.post("", response_model=ExpenseOut, status_code=201)
def create_expense(
    payload: ExpenseCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    _get_owned_category(db, payload.category_id, current_user.id)

    expense = Expense(
        user_id=current_user.id,
        category_id=payload.category_id,
        amount=payload.amount,
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
    category_id: int | None = None,
    start_date: datetime | None = None,
    end_date: datetime | None = None,
    limit: int = Query(default=100, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    stmt = (
        select(Expense)
        .where(Expense.user_id == current_user.id)
        .options(selectinload(Expense.category))
    )
    if category_id:
        stmt = stmt.where(Expense.category_id == category_id)
    if start_date:
        stmt = stmt.where(Expense.occurred_at >= start_date)
    if end_date:
        stmt = stmt.where(Expense.occurred_at <= end_date)
    stmt = stmt.order_by(Expense.occurred_at.desc()).limit(limit).offset(offset)

    return db.execute(stmt).scalars().all()


@router.get("/{expense_id}", response_model=ExpenseOut)
def get_expense(expense_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    expense = db.get(Expense, expense_id)
    # 404 (not 403) when it belongs to someone else — don't reveal that the row exists.
    if expense is None or expense.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Expense not found")
    return expense


@router.put("/{expense_id}", response_model=ExpenseOut)
def update_expense(
    expense_id: int,
    payload: ExpenseUpdate,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    expense = db.get(Expense, expense_id)
    if expense is None or expense.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Expense not found")
    _get_owned_category(db, payload.category_id, current_user.id)

    # Optimistic locking: the UPDATE only succeeds if the row's version still
    # matches what the client last read. rowcount == 0 means someone else wrote
    # to this record first -> surface a 409 rather than silently overwriting.
    result = db.execute(
        update(Expense)
        .where(Expense.id == expense_id, Expense.version == payload.version)
        .values(
            amount=payload.amount,
            category_id=payload.category_id,
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
def delete_expense(expense_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    expense = db.get(Expense, expense_id)
    if expense is None or expense.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Expense not found")
    db.delete(expense)
    db.commit()
    return None
