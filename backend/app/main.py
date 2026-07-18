import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.analytics.anomaly_service import handle_expense_created
from app.analytics.llm_signal import handle_expense_created_llm
from app.auth import COOKIE_NAME, decode_access_token
from app.config import settings
from app.database import SessionLocal
from app.events import start_listener, stop_listener, subscribe
from app.models import User
from app.routers import alerts, auth, categories, expenses
from app.ws_manager import manager as ws_manager

logging.basicConfig(level=logging.INFO)

app = FastAPI(title="Live Expense Tracker API")

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(categories.router)
app.include_router(expenses.router)
app.include_router(alerts.router)


@app.on_event("startup")
async def on_startup() -> None:
    # This is the only place the CRUD layer and the analytics services meet:
    # subscriptions, not imports in a route handler. Two independent
    # subscribers on the same event — the rule-based detector and the LLM
    # secondary signal — each writes its own Alert if it fires.
    subscribe(handle_expense_created)
    subscribe(handle_expense_created_llm)
    # Redis Pub/Sub listener loops — one for expense.created -> analytics,
    # one for alert broadcast -> this instance's locally-connected sockets.
    start_listener()
    ws_manager.start_listener()


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await stop_listener()
    await ws_manager.stop_listener()


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/alerts")
async def alerts_websocket(websocket: WebSocket):
    # Browsers can't set custom headers on a WebSocket handshake, but they do
    # send cookies automatically for same-site requests — so auth here reads
    # the same httpOnly cookie the REST endpoints use, rather than a token
    # in the URL (which would leak into logs/referrers).
    token = websocket.cookies.get(COOKIE_NAME)
    user_id = decode_access_token(token) if token else None
    if user_id is None:
        await websocket.close(code=4401)
        return

    db = SessionLocal()
    try:
        user_exists = db.get(User, user_id) is not None
    finally:
        db.close()
    if not user_exists:
        await websocket.close(code=4401)
        return

    await ws_manager.connect(websocket, user_id)
    try:
        while True:
            # Dashboard doesn't send anything meaningful; just keep the socket
            # alive and detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket, user_id)
