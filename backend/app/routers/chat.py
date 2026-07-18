from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict
from sqlalchemy.orm import Session

from app.chat.service import answer
from app.database import get_db
from app.dependencies import get_current_user
from app.models import ChatMessage, User

router = APIRouter(prefix="/chat", tags=["chat"])


class ChatRequest(BaseModel):
    message: str


class ChatResponse(BaseModel):
    reply: str


class ChatMessageOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    role: str
    content: str


@router.get("/history", response_model=list[ChatMessageOut])
def get_history(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    return (
        db.query(ChatMessage)
        .filter(ChatMessage.user_id == current_user.id)
        .order_by(ChatMessage.created_at)
        .all()
    )


@router.post("", response_model=ChatResponse)
async def chat(
    payload: ChatRequest, db: Session = Depends(get_db), current_user: User = Depends(get_current_user)
):
    reply = await answer(db, current_user.id, payload.message)

    db.add(ChatMessage(user_id=current_user.id, role="user", content=payload.message))
    db.add(ChatMessage(user_id=current_user.id, role="assistant", content=reply))
    db.commit()

    return ChatResponse(reply=reply)
