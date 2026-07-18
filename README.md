# Live Expense Tracker with Anomaly Alerts

A full-stack expense tracker (React + FastAPI + PostgreSQL) with a rule-based
anomaly detection service that flags unusual spending in real time over
WebSockets, and optimistic locking to keep concurrent edits safe.

Built for the dotkonnekt Full Stack Assessment — Project A. Since the
original submission (tagged `v1.0`), a multi-user auth layer has been added
on top (see "V2 additions" below) as an extension beyond the assessment scope.

> Supporting docs: [docs/DESIGN.md](docs/DESIGN.md) (v1.0 architecture,
> schema, anomaly rule, concurrency), [docs/ROADMAP.md](docs/ROADMAP.md)
> (v1.0 task breakdown), [docs/V2_DESIGN.md](docs/V2_DESIGN.md) (auth,
> custom categories, Redis, LLM signal, email, chatbot — architecture) and
> [docs/V2_ROADMAP.md](docs/V2_ROADMAP.md) (what's built vs. still planned).

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

## V2 additions (post-submission, beyond the assessment scope)

- **Auth & multi-tenancy**: JWT sessions in an httpOnly cookie, `POST
  /auth/register`, `/login`, `/logout`, `GET /auth/me`. Every expense/alert
  is scoped to its owner — verified directly that a second account sees zero
  of the first account's data.
- **Per-user anomaly baselines with a cold-start fallback**: the z-score
  rule now runs against the logged-in user's own trailing history; a
  brand-new account with no history yet falls back to a cross-user category
  baseline, and the alert reason says so explicitly rather than silently
  behaving differently.
- **WebSocket auth**: `/ws/alerts` reads the same httpOnly cookie at the
  handshake (browsers send cookies automatically on same-site WebSocket
  connections — no token-in-URL needed) and only pushes alerts to the
  connected user's own sockets. Verified: an unauthenticated connection
  attempt is rejected with HTTP 403 before `accept()`.
- **Custom per-user categories**: a `categories` table replaces the old
  free-text column; each user manages their own list (add/delete) from the
  dashboard. Deleting a category that still has expenses on it is blocked
  with `409`, not silently orphaned.
- **Redis-backed event bus + WebSocket fan-out**: `app/events.py` and
  `app/ws_manager.py` now run on real Redis Pub/Sub instead of an in-process
  list, so the analytics service and alert delivery aren't tied to a single
  process anymore. Verified against a real local Redis instance, full round
  trip (expense created → Redis → analytics service → Alert written → Redis
  → WebSocket push). Honest caveat documented directly in `events.py`:
  Pub/Sub is fan-out, which is correct for one backend instance but would
  need Redis Streams + a consumer group to avoid duplicate processing if the
  analytics service is ever scaled to multiple instances — not built, since
  that's real added complexity disproportionate to a single-instance app.
- **LLM secondary anomaly signal**: `app/analytics/llm_signal.py` is a
  second, independent subscriber on the same `expense.created` event as the
  z-score rule — it catches semantic mismatches (a description that doesn't
  fit its category) that a purely numeric rule structurally cannot, using
  OpenAI (`gpt-4o-mini`) with a structured-output schema. Gated to run only
  on cold-start categories or borderline z-scores to control cost. Verified
  live against the real OpenAI API: correctly flagged "concert tickets"
  logged under "utilities," did *not* flag a legitimate "monthly electricity
  bill" in the same category, and correctly stayed silent when the rule-based
  detector had already caught an expense. Alerts carry a `source: "rule" |
  "llm"` field; the dashboard shows a color-coded badge for each.
- Still planned, not yet built: email notifications and a RAG chatbot over
  expenses/alerts — see [docs/V2_ROADMAP.md](docs/V2_ROADMAP.md) for the
  phase-by-phase plan.

## Architecture at a glance

```
React dashboard  ──REST──▶  FastAPI routers (auth, categories, expenses, alerts)
       ▲                          │
       │ WebSocket                │ publishes "expense.created" to Redis
       │ (push, via Redis         ▼
       │  alerts_broadcast)  Redis Pub/Sub ── expense_created channel
       │                          │
       │                          ├──▶ analytics/anomaly_service.py (rule, z-score)
       │                          └──▶ analytics/llm_signal.py (OpenAI, semantic check)
       │                                   │              │
       │                                   ▼              ▼
       └───────────────────────────  writes Alert (source: rule | llm)
                                            │
                                            ▼
                                       PostgreSQL
```

The CRUD router never imports anomaly logic — it publishes an event and
moves on. Both analytics services subscribe independently to the same
event; neither has a FastAPI/HTTP dependency, and either could be lifted
into its own process without touching the router. Full rationale in
[docs/DESIGN.md](docs/DESIGN.md) (v1.0) and [docs/V2_DESIGN.md](docs/V2_DESIGN.md)
(auth, categories, Redis, LLM signal).

## Data model

```
users      (id, email, hashed_password, created_at)
categories (id, user_id -> users.id, name, created_at)
expenses   (id, user_id -> users.id, category_id -> categories.id, amount,
            description, occurred_at, created_at, updated_at, version)
alerts     (id, expense_id -> expenses.id, reason, severity,
            source ['rule'|'llm'], z_score, created_at, acknowledged)
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

## LLM secondary anomaly signal (V2)

`app/analytics/llm_signal.py` is a second, independent subscriber on the
same `expense.created` event — not a replacement for the z-score rule, a
complement to it. It catches a failure mode the numeric rule structurally
can't: a description that doesn't match its category, even when the amount
itself is completely unremarkable (a rule based purely on amount has no
signal to work with there at all).

- Uses OpenAI (`gpt-4o-mini`) with a Pydantic-validated structured-output
  schema (`{flagged: bool, reason: str}`) — not free-text parsing.
- **Cost gate**: only invoked when the numeric rule is inconclusive —
  cold-start categories (not enough history for the rule to judge) or a
  borderline z-score (elevated but under the rule's own warning threshold).
  Skipped entirely when the rule already fired, or the expense is
  numerically unremarkable with plenty of history. This is a deliberate
  trade-off: a normal-looking amount with a wildly wrong category (no
  numeric signal at all) can slip through both gates — noted honestly in
  Known Limitations rather than solved by running the LLM on every expense.
- Requires `OPENAI_API_KEY` in `.env`; runs the rule-only detector if unset.
- Verified live against the real API: correctly flagged "concert tickets"
  logged under "utilities" and a second mismatched entry, did *not* flag a
  legitimate "monthly electricity bill" in the same category, and correctly
  stayed silent on an expense the rule-based detector had already caught.
- Alerts carry `source: "rule" | "llm"`; the dashboard shows a color-coded
  badge so it's clear which detector raised each one.

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

**Update (V2):** this is no longer purely theoretical — the event bus and
alert broadcast now run on real Redis Pub/Sub (`app/events.py`,
`app/ws_manager.py`), specifically so that a second backend instance's
locally-connected socket can still receive an alert raised by the instance
that handled the write. See the "Redis-backed event bus" note above and
[docs/V2_DESIGN.md](docs/V2_DESIGN.md) Phase C for the full reasoning,
including the one caveat that's *not* solved by Pub/Sub (duplicate
processing if the analytics service itself is scaled to multiple
consumers — would need Redis Streams + a consumer group for that).

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

Requires Python 3.11+, Node 20+/21, a running PostgreSQL instance, and a
running Redis instance (`brew install redis && brew services start redis`,
or any local Redis — no auth needed for local dev, just the default
`redis://localhost:6379/0`). Docker Compose files are included but **not
verified in this environment** — see Known Limitations.

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
cp .env.example .env   # edit DATABASE_URL/REDIS_URL if not on default ports;
                       # set OPENAI_API_KEY to enable the LLM secondary signal (optional)
alembic upgrade head   # also creates a demo@example.com / demo1234 account and
                       # backfills any pre-auth seed data to it
python -m app.seed     # optional but recommended — backfills sample data + 2 outliers
uvicorn app.main:app --reload --port 8000
```

**Auth (added in V2):** the API now requires a session. Log in through the
dashboard with `demo@example.com` / `demo1234` (created by the migration), or
register a new account — new accounts start with zero data and see their own
expenses only. See [docs/V2_DESIGN.md](docs/V2_DESIGN.md) for the full design
(JWT-in-httpOnly-cookie, per-user anomaly baselines with a cross-user
cold-start fallback) and [docs/V2_ROADMAP.md](docs/V2_ROADMAP.md) for what's
built vs. still planned (custom categories, Redis event bus, an LLM secondary
anomaly signal, email notifications, and a RAG chatbot).

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
| `POST` | `/auth/register` | Create account; sets the session cookie |
| `POST` | `/auth/login` | Sets the session cookie |
| `POST` | `/auth/logout` | Clears the session cookie |
| `GET` | `/auth/me` | Current user; `401` if not authenticated |
| `GET` | `/categories` | List caller's categories |
| `POST` | `/categories` | Create; `409` on duplicate name |
| `DELETE` | `/categories/{id}` | Delete; `409` if expenses still use it |
| `POST` | `/expenses` | Create (scoped to caller); publishes `expense.created` event after commit |
| `GET` | `/expenses` | List (scoped to caller); filters: `category_id`, `start_date`, `end_date`, `limit`, `offset` |
| `GET` | `/expenses/{id}` | Fetch one; `404` if it's not yours |
| `PUT` | `/expenses/{id}` | Update; requires `version`; `409` on stale version |
| `DELETE` | `/expenses/{id}` | Delete (cascades its alerts) |
| `GET` | `/alerts` | List (scoped to caller); filter by `acknowledged`; each has `source: "rule" \| "llm"` |
| `PATCH` | `/alerts/{id}/ack` | Acknowledge an alert |
| `WS` | `/ws/alerts` | Live alert push; requires the session cookie, delivers only the caller's own alerts |

All routes except `/auth/*` and `/health` require an authenticated session
(the httpOnly cookie set by `/auth/login` or `/auth/register`); requests
without it get `401`.

## Known limitations

- **No refresh-token rotation.** A single access token (60 min default) —
  session just expires and requires re-login. No password reset or email
  verification flow either. Fine for this scope; a real product needs both.
- **Auth is per-account, not per-team.** Each user sees only their own data;
  there's no sharing/org concept.
- **Rolling-window contamination.** The z-score's trailing window can include
  a previous outlier (e.g. a prior spike still within the last 20 records),
  which inflates the mean/stddev and can suppress detection of a second,
  smaller anomaly shortly after a big one. Observed directly during testing.
  A more robust version would use a robust statistic (median + MAD) or
  exclude previously-flagged expenses from the training window.
- **Redis Pub/Sub is fan-out, not a work queue.** `app/events.py` now runs
  on real Redis (V2), which correctly solves WebSocket delivery across
  multiple backend instances. But if the analytics service itself were ever
  scaled to multiple consumer instances, every instance would independently
  process every event and could each write a duplicate Alert — Pub/Sub has
  no "exactly one consumer" semantics. Redis Streams with a consumer group
  would be the correct fix; not built, since it's real added complexity
  (XACK, pending-entry handling) disproportionate to the single-instance
  deployment this app actually runs as. Documented directly in `events.py`.
- **LLM signal has a cold-start/borderline-only gate.** A normal-looking
  amount filed under a badly wrong category, with plenty of prior history in
  that category (so the numeric rule is confident it's "normal"), won't
  trigger the LLM check under the current cost gate. Running the LLM on
  every expense would catch that too, at full per-expense cost — a
  configurable "always check" mode is a reasonable follow-up, not built here.
- **No automated test suite.** Verification for this build was done by
  direct exercise (curl/Python scripts hitting the running API, a Playwright
  screenshot of the dashboard) rather than a committed pytest/Vitest suite —
  a real follow-up would add unit tests for `detect_anomaly` (pure function,
  easy to test), the LLM gate's `_should_invoke`, and the optimistic-locking
  conflict path.
- **Docker Compose is unverified**, as noted above (now includes a `redis`
  service alongside `db`/`backend`/`frontend`, same caveat applies).
- **No ML-based forecasting model** — the rule-based z-score and the LLM
  semantic check cover the "bonus" ground differently (statistical +
  semantic) rather than via a trained forecasting/isolation-forest model.
- **Pagination is basic** limit/offset, no cursor-based pagination.

## Repo layout

```
backend/
  app/
    routers/         # CRUD-only route handlers (auth, categories, expenses, alerts)
    analytics/        # anomaly_service.py (rule) + llm_signal.py (OpenAI) — separate from routers
    auth.py           # password hashing, JWT issue/verify
    dependencies.py   # get_current_user
    events.py         # Redis Pub/Sub connecting CRUD -> analytics (both subscribers)
    ws_manager.py     # Redis-backed WebSocket broadcast, per-user delivery
    models.py, schemas.py, database.py, config.py
  alembic/            # migrations (0001 initial, 0002 auth, 0003 categories, 0004 alert source)
docs/
  DESIGN.md           # v1.0 architecture, schema, anomaly rule, concurrency detail
  ROADMAP.md          # v1.0 task breakdown
  V2_DESIGN.md        # auth, categories, Redis, LLM signal, email, chatbot — architecture
  V2_ROADMAP.md       # V2 task breakdown, phase by phase
frontend/
  src/
    auth/             # AuthContext, AuthPage (login/register)
    components/       # ExpenseForm, ExpenseTable, SpendingChart, AlertsPanel, CategoryManager
    hooks/            # useWebSocketAlerts
    Dashboard.tsx, api.ts
docker-compose.yml
```
