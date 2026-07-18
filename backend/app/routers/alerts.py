from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.database import get_db
from app.dependencies import get_current_user
from app.models import Alert, Expense, User
from app.schemas import AlertOut

router = APIRouter(prefix="/alerts", tags=["alerts"])


@router.get("", response_model=list[AlertOut])
def list_alerts(
    acknowledged: bool | None = None,
    limit: int = 100,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    # Alerts don't carry their own user_id — ownership is scoped through the
    # expense they belong to, so there's a single source of truth for "whose
    # row is this" (see docs/V2_DESIGN.md, Phase A).
    stmt = select(Alert).join(Expense).where(Expense.user_id == current_user.id)
    if acknowledged is not None:
        stmt = stmt.where(Alert.acknowledged == acknowledged)
    stmt = stmt.order_by(Alert.created_at.desc()).limit(limit)
    return db.execute(stmt).scalars().all()


@router.patch("/{alert_id}/ack", response_model=AlertOut)
def acknowledge_alert(alert_id: int, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    alert = db.get(Alert, alert_id)
    if alert is None or alert.expense.user_id != current_user.id:
        raise HTTPException(status_code=404, detail="Alert not found")
    alert.acknowledged = True
    db.commit()
    db.refresh(alert)
    return alert
