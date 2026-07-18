import logging

from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware

from app.analytics.anomaly_service import handle_expense_created
from app.config import settings
from app.events import subscribe
from app.routers import alerts, expenses
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

app.include_router(expenses.router)
app.include_router(alerts.router)


@app.on_event("startup")
def wire_event_subscribers() -> None:
    # This is the only place the CRUD layer and the analytics service meet:
    # a subscription, not an import in a route handler.
    subscribe(handle_expense_created)


@app.get("/health")
def health():
    return {"status": "ok"}


@app.websocket("/ws/alerts")
async def alerts_websocket(websocket: WebSocket):
    await ws_manager.connect(websocket)
    try:
        while True:
            # Dashboard doesn't send anything meaningful; just keep the socket
            # alive and detect disconnects.
            await websocket.receive_text()
    except WebSocketDisconnect:
        ws_manager.disconnect(websocket)
