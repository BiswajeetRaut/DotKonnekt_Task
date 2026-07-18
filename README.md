# Live Expense Tracker with Anomaly Alerts

A full-stack expense tracker (React + FastAPI + PostgreSQL) with a rule-based
anomaly detection service that flags unusual spending in real time over
WebSockets, and optimistic locking to keep concurrent edits safe.

Built for the dotkonnekt Full Stack Assessment — Project A.

> Supporting docs: [docs/DESIGN.md](docs/DESIGN.md) (architecture, schema,
> anomaly rule, concurrency) and [docs/ROADMAP.md](docs/ROADMAP.md) (task
> breakdown used during the build).

## What works

- Full CRUD on expenses (create/list/filter/update/delete), backed by
  PostgreSQL via SQLAlchemy + Alembic migrations.
- A **separate analytics service** (`backend/app/analytics/anomaly_service.py`)
  computes a rolling per-category z-score on every new expense and writes
  `Alert` rows — it is wired to the CRUD layer only through an in-process
  event bus (`backend/app/events.py`), never imported by a route handler.
- Alerts push to the dashboard live over a WebSocket
  (`/ws/alerts`) the moment they're created — no polling needed for alerts.
- Dashboard (React + Recharts): expense form, editable/deletable expense
  table, spending-by-category bar chart, and a live alerts panel — all backed
  by real data from the API, with per-section loading/error states.
- Optimistic locking on `expenses.version` — concurrent stale writes return
  `409 Conflict` instead of silently clobbering each other.
- Seed script backfills ~80 historical expenses across 4 categories plus 2
  deliberate outliers, so the chart and alerts panel have real signal
  immediately after setup.

All of the above was exercised directly (not just unit-tested) during
development: REST create/update/delete, a live 409 conflict between two
concurrent `PUT`s, an anomaly firing end-to-end from `POST /expenses` through
the event bus to a connected WebSocket client, and the dashboard rendering
real data in a browser (screenshots taken with Playwright during the build).

## Architecture at a glance

```
React dashboard  ──REST──▶  FastAPI routers (expenses, alerts)
       ▲                          │
       │ WebSocket                │ BackgroundTask publishes "expense.created"
       │ (push)                   ▼
       └──────────────── analytics/anomaly_service.py
                          (rolling z-score, writes Alert, broadcasts)
                                   │
                                   ▼
                              PostgreSQL
```

The CRUD router never imports anomaly logic — it publishes an event and
moves on. The analytics service is the only subscriber, has no FastAPI/HTTP
dependency, and could be lifted into its own process/consumer without
touching the router. Full rationale in [docs/DESIGN.md](docs/DESIGN.md).

## Data model

```
expenses (id, amount, category, description, occurred_at, created_at,
          updated_at, version)
alerts   (id, expense_id -> expenses.id, reason, severity, z_score,
          created_at, acknowledged)
```

One expense → zero-or-more alerts. See [docs/DESIGN.md §3](docs/DESIGN.md)
for full column types and the rationale for keeping this to two tables.

## Anomaly detection rule

Rolling per-category z-score, implemented as a pure function
(`detect_anomaly`) plus a thin DB-writing wrapper (`evaluate_expense`):

1. Pull the trailing 20 expenses in the same category.
2. Require at least 5 prior data points before judging (avoids false
   positives on a cold-start category).
3. `z = (amount - mean) / stddev` of that trailing window.
4. `z >= 2.5` → `warning`, `z >= 4` → `critical`, otherwise no alert.

Deliberately simple and explainable over a black-box model — an interviewer
can verify a flagged alert by hand. Full detail in
[docs/DESIGN.md §4](docs/DESIGN.md).

## Ideation angle: real-time strategy — WebSockets vs. polling

**Chosen approach: WebSockets for alerts, plain REST + refetch for CRUD.**

Alerts are exactly the kind of event polling is bad at: rare, unpredictable,
and each one matters the moment it happens. Concretely:

| | Polling (e.g. every 5s) | WebSocket push |
|---|---|---|
| Latency | Up to one full interval (avg interval/2) | Sub-second, as soon as the analytics service writes the alert |
| Server load | Constant `GET /alerts` traffic from every open dashboard, whether or not anything changed — scales with `clients × 1/interval` regardless of actual event rate | Near-zero idle cost; traffic only when an alert actually fires |
| Client complexity | Trivial — a `setInterval` + fetch | Needs reconnect/backoff handling (implemented here — see `useWebSocketAlerts`), and a fallback story if the socket drops |
| Freshness vs. cost tradeoff knob | Tune the interval — tighter interval = fresher but more load | No knob needed; push is inherently "as fresh as the event" |
| Infra | Works through anything that does plain HTTP (proxies, some serverless setups) | Needs a connection-aware layer (sticky sessions or a pub/sub fan-out once you have more than one backend instance) |

Why WebSockets won for **this specific feature**: anomaly alerts are exactly
the "rare but time-sensitive" event shape — polling would either be too slow
(long interval) or wasteful (short interval, mostly-empty responses) for
something a user wants to know about immediately. It's implemented, not just
described, here: `backend/app/ws_manager.py` broadcasts, and
`frontend/src/hooks/useWebSocketAlerts.ts` subscribes with auto-reconnect.

Why the **expense list/chart itself stays request/refresh** rather than also
being pushed: it's not an event stream, it's a queryable resource the user
edits directly (create/edit/delete), so a normal REST response after each
mutation is simpler and sufficient — there's no case here where a *different*
client's edit needs to appear on your screen without you taking an action
first. If that requirement existed (e.g. a shared team dashboard where
someone else's edit should appear live), the same WebSocket channel could
carry `expense.created` / `expense.updated` events too — the event bus
already supports adding more event types and subscribers without touching the
router again.

**In production at scale**, the one thing that would need to change: with
more than one backend process, in-memory `ConnectionManager` state (which
sockets are connected) wouldn't be shared across instances. That needs a
pub/sub layer (Redis pub/sub, or the same broker used for the event bus) so
any instance can broadcast to a socket held by another instance. Noted in
Known Limitations below.

## Concurrency: how race conditions are avoided

Optimistic locking via an `expenses.version` integer column:

- Every `PUT /expenses/{id}` request must send back the `version` it read.
- The UPDATE is conditional: `WHERE id = :id AND version = :expected_version`,
  incrementing `version` on success.
- If no row matched (someone else updated it first), the API returns
  `409 Conflict` and the client is expected to refetch and retry — the
  frontend surfaces this explicitly (`ExpenseTable` catches `ConflictError`
  and triggers a refetch) rather than silently overwriting.
- Verified directly during the build: two `PUT` requests against the same
  expense with the same stale `version` — the first succeeds (`version`
  1→2), the second gets `409`.

This was chosen over pessimistic row locking (`SELECT ... FOR UPDATE`)
because expense edits are infrequent and conflicts rare — optimistic locking
avoids holding DB locks across a request/response round trip while still
guaranteeing no lost updates.

## Running it locally

Requires Python 3.11+, Node 20+/21, and a running PostgreSQL instance.
(Docker Compose files are included but **not verified in this environment** —
see Known Limitations.)

### 1. Database

Create a database and a role for the app (adjust host/port to your install;
defaults below assume Postgres on `localhost:5432`):

```sql
CREATE ROLE expense LOGIN PASSWORD 'expense';
CREATE DATABASE expense_tracker OWNER expense;
```

### 2. Backend

```bash
cd backend
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # edit DATABASE_URL if your Postgres isn't on the default port
alembic upgrade head
python -m app.seed     # optional but recommended — backfills sample data + 2 outliers
uvicorn app.main:app --reload --port 8000
```

API docs at `http://localhost:8000/docs`. Health check at `/health`.

### 3. Frontend

```bash
cd frontend
npm install
npm run dev
```

Dashboard at `http://localhost:5173`. It expects the API at
`http://localhost:8000` by default (override with `VITE_API_URL`).

### Docker Compose (bonus, best-effort)

`docker-compose.yml` at the repo root brings up Postgres + backend + frontend
with one command:

```bash
docker compose up --build
```

This was **not exercised in this build session** — Docker wasn't usable on
the dev machine (incompatible macOS/Docker Desktop version), so local
Postgres was used for verification instead. The compose file follows the same
image/env conventions as the manual setup above and should work, but treat it
as unverified until you've run it once.

## API summary

| Method | Path | Notes |
|---|---|---|
| `POST` | `/expenses` | Create; publishes `expense.created` event after commit |
| `GET` | `/expenses` | List; filters: `category`, `start_date`, `end_date`, `limit`, `offset` |
| `GET` | `/expenses/{id}` | Fetch one |
| `PUT` | `/expenses/{id}` | Update; requires `version`; `409` on stale version |
| `DELETE` | `/expenses/{id}` | Delete (cascades its alerts) |
| `GET` | `/alerts` | List; filter by `acknowledged` |
| `PATCH` | `/alerts/{id}/ack` | Acknowledge an alert |
| `WS` | `/ws/alerts` | Live alert push |

## Known limitations

- **No auth / single shared workspace.** Every client sees the same data;
  there's no login or per-user isolation. Out of scope for the time box.
- **Rolling-window contamination.** The z-score's trailing window can include
  a previous outlier (e.g. a prior spike still within the last 20 records),
  which inflates the mean/stddev and can suppress detection of a second,
  smaller anomaly shortly after a big one. Observed directly during testing.
  A more robust version would use a robust statistic (median + MAD) or
  exclude previously-flagged expenses from the training window.
- **In-process event bus, not a real broker.** `app/events.py` is a
  same-process pub/sub, not Kafka/Redis Streams. Fine for one backend
  instance; would need a real broker (and a shared pub/sub for WebSocket
  fan-out) to scale horizontally across multiple backend processes.
- **No automated test suite.** Verification for this build was done by
  direct exercise (curl/Python scripts hitting the running API, a Playwright
  screenshot of the dashboard) rather than a committed pytest/Vitest suite —
  a real follow-up would add unit tests for `detect_anomaly` (pure function,
  easy to test) and the optimistic-locking conflict path.
- **Docker Compose is unverified**, as noted above.
- **Categories are free text**, not a managed lookup table — no admin UI to
  rename/merge categories.
- **No ML-based model** (bonus item) — z-score rule only, by design, to keep
  the detection logic explainable within the time box.
- **Pagination is basic** limit/offset, no cursor-based pagination.

## Repo layout

```
backend/
  app/
    routers/        # CRUD-only route handlers (expenses, alerts)
    analytics/       # anomaly detection — separate from routers
    events.py        # in-process pub/sub connecting CRUD -> analytics
    ws_manager.py    # WebSocket broadcast
    models.py, schemas.py, database.py, config.py
  alembic/           # migrations
docs/
  DESIGN.md          # architecture, schema, anomaly rule, concurrency detail
  ROADMAP.md         # task breakdown used during the build
frontend/
  src/
    components/      # ExpenseForm, ExpenseTable, SpendingChart, AlertsPanel
    hooks/            # useWebSocketAlerts
    api.ts
docker-compose.yml
```
