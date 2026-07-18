from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Category, User
from app.schemas import CategoryCreate, CategoryOut

router = APIRouter(prefix="/categories", tags=["categories"])


@router.get("", response_model=list[CategoryOut])
def list_categories(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    stmt = select(Category).where(Category.user_id == current_user.id).order_by(Category.name)
    return db.execute(stmt).scalars().all()


@router.post("", response_model=CategoryOut, status_code=201)
def create_category(
    payload: CategoryCreate, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    existing = (
        db.query(Category)
        .filter(Category.user_id == current_user.id, Category.name == payload.name)
        .first()
    )
    if existing is not None:
        raise HTTPException(status_code=409, detail="You already have a category with this name")

    category = Category(user_id=current_user.id, name=payload.name)
    db.add(category)
    db.commit()
    db.refresh(category)
    return category


@router.delete("/{category_id}", status_code=204)
def delete_category(
    category_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    category = db.get(Category, category_id)
    if category is None or category.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Category not found")

    db.delete(category)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail="Category still has expenses using it — reassign or delete those first",
        )
    return None
