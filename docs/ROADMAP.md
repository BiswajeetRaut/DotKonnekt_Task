# Roadmap / Task Status — Live Expense Tracker

Time budget: ~2 hours total. Status legend: `[ ]` todo, `[~]` in progress, `[x]` done.

Update this file as tasks complete — it's the single source of truth for scope
during the build, not a historical log.

## Phase 0 — Scope & Docs (target: 15 min)
- [x] Read assignment PDF, confirm Project A (Live Expense Tracker)
- [x] Write DESIGN.md (architecture, schema, anomaly rule, concurrency, real-time)
- [x] Write ROADMAP.md (this file)

## Phase 1 — Backend scaffold (target: 35 min) — DONE
- [x] `backend/` project structure (FastAPI, SQLAlchemy, Alembic, Pydantic settings)
- [x] `docker-compose.yml` with Postgres service (+ backend, frontend for one-command up)
- [x] SQLAlchemy models: `Expense`, `Alert`
- [x] Alembic migration: create `expenses`, `alerts` tables
- [x] Pydantic schemas: `ExpenseCreate`, `ExpenseUpdate`, `ExpenseOut`, `AlertOut`
- [x] CRUD router `app/routers/expenses.py` (POST/GET/GET-one/PUT/DELETE)
- [x] Alerts router `app/routers/alerts.py`: `GET /alerts`, `PATCH /alerts/{id}/ack`
- [x] `app/analytics/anomaly_service.py`: rolling z-score detector, pure function +
      thin DB-writing wrapper, unit-testable in isolation from HTTP layer
- [x] `app/events.py`: tiny in-process pub/sub wiring expense-created → anomaly service
- [x] `app/ws_manager.py`: WebSocket connection manager
- [x] `GET /ws/alerts` WebSocket endpoint, broadcasts new `Alert` JSON on creation
- [x] Seed script: backfilled 80 historical expenses across 4 categories + 2 outliers

## Phase 2 — Frontend scaffold (target: 35 min) — DONE
- [x] Vite + React + TypeScript app in `frontend/`
- [x] API client (`src/api.ts`) — typed fetch wrappers for expenses/alerts
- [x] `useWebSocketAlerts` hook — connects to `/ws/alerts`, appends to alert list, reconnects
- [x] `ExpenseForm` component — create expense
- [x] `ExpenseTable` component — list, inline edit (version-aware), delete,
      surfaces 409 conflicts with a "refresh and retry" prompt
- [x] `SpendingChart` component — Recharts bar, spend by category
- [x] `AlertsPanel` component — live-updating list of anomaly alerts, ack button
- [x] `Dashboard` (`App.tsx`) composing the above, loading/error states per section

## Phase 3 — Concurrency & real-time verification (target: 15 min) — DONE
- [x] Manual test: two concurrent `PUT /expenses/{id}` with same stale version →
      confirmed 200 then 409
- [x] Manual test: created an anomalous expense → confirmed alert row created and
      pushed over WebSocket to a connected client (verified with a raw websockets client)
- [x] Playwright screenshot of the running dashboard — real data, chart, alerts panel
      confirmed rendering, zero console errors

## Phase 4 — Docs & submission polish (target: 20 min) — DONE
- [x] README.md: setup/run instructions (Docker + local), migration steps,
      schema summary, anomaly rule description, ideation answer (WebSockets vs
      polling — full trade-off table), concurrency approach, known limitations
- [ ] `docker compose up` not verified — Docker unusable on this dev machine;
      noted explicitly in README as unverified rather than silently assumed working
- [ ] `git tag v1.0` on final commit — pending user confirmation before tagging

## Explicit non-goals (see DESIGN.md §8 for rationale)
- Auth / multi-tenant
- Message broker (Kafka/Redis) instead of in-process event bus
- ML-based anomaly model
- Full automated test suite / CI
