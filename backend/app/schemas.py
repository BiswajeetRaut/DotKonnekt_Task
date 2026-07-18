from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field


class ExpenseCreate(BaseModel):
    amount: Decimal = Field(gt=0)
    category: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    occurred_at: datetime


class ExpenseUpdate(BaseModel):
    amount: Decimal = Field(gt=0)
    category: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=500)
    occurred_at: datetime
    version: int = Field(description="Version read by the client; used for optimistic locking")


class ExpenseOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    amount: Decimal
    category: str
    description: str | None
    occurred_at: datetime
    created_at: datetime
    updated_at: datetime
    version: int


class AlertOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    expense_id: int
    reason: str
    severity: str
    z_score: Decimal | None
    created_at: datetime
    acknowledged: bool
