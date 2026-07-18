# Design Doc — Live Expense Tracker with Anomaly Alerts

## 1. Scope decision

Project A from the assessment: expense logging + spending dashboard + a separate
rule-based anomaly detection service, with real-time alert delivery over WebSockets
and optimistic locking for concurrent writes.

Time-boxed to a 2-hour build. Cuts are listed in "Out of scope" below and repeated
in the README's Known Limitations section — nothing is silently dropped.

## 2. Architecture

```
┌─────────────┐        REST (CRUD)        ┌──────────────────┐
│   React     │ ───────────────────────▶  │   FastAPI app    │
│  Dashboard  │ ◀───────────────────────  │  (routers/crud)  │
│             │                            └────────┬─────────┘
│             │        WebSocket (alerts)            │ calls (in-process function call,
│             │ ◀────────────────────────────────────┤  not HTTP — same process,
└─────────────┘                                       │  separate module)
                                              ┌────────▼─────────┐
                                              │  Analytics /     │
                                              │  Anomaly Service │
                                              │  (app/analytics) │
                                              └────────┬─────────┘
                                                        │ writes
                                              ┌────────▼─────────┐
                                              │   PostgreSQL     │
                                              │ expenses, alerts │
                                              └──────────────────┘
```

Key architectural decision: **event-driven separation, in-process for this build**.

- `POST /expenses` in the CRUD router does exactly one thing: validate, insert, commit.
- After commit, it publishes an `ExpenseCreated` event onto a tiny in-process event
  bus (`app/events.py`) using a FastAPI `BackgroundTask`.
- The **analytics service** (`app/analytics/anomaly_service.py`) is the only
  subscriber. It has zero knowledge of HTTP, request/response shapes, or the router
  layer — it takes an `Expense` row and DB session, and produces `Alert` rows.
  It could be lifted into its own worker process / separate microservice consuming
  from a real queue (Redis Streams, RabbitMQ, Kafka) without changing its public
  interface. That's the "separation of concerns" the ground rules ask for.
- The router never imports anomaly logic directly; it only imports `publish()`.
- New alerts are pushed to connected dashboards via a WebSocket manager
  (`app/ws_manager.py`), which the analytics service calls after writing an alert.

This gets us real separation of concerns and an event-driven shape without the
operational overhead of standing up Kafka/Redis in a 2-hour build — that trade-off
is called out explicitly in Known Limitations.

## 3. Data model

```
expenses
--------
id            SERIAL PK
amount        NUMERIC(12,2) NOT NULL
category      TEXT NOT NULL           -- e.g. "food", "transport", "software"
description   TEXT
occurred_at   TIMESTAMPTZ NOT NULL    -- when the expense happened
created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
updated_at    TIMESTAMPTZ NOT NULL DEFAULT now()
version       INTEGER NOT NULL DEFAULT 1   -- optimistic concurrency token

alerts
------
id            SERIAL PK
expense_id    INTEGER NOT NULL REFERENCES expenses(id) ON DELETE CASCADE
reason        TEXT NOT NULL           -- e.g. "z-score 3.4 above category mean"
severity      TEXT NOT NULL           -- "warning" | "critical"
z_score       NUMERIC(6,2)
created_at    TIMESTAMPTZ NOT NULL DEFAULT now()
acknowledged  BOOLEAN NOT NULL DEFAULT false
```

Relationship: one expense → zero-or-more alerts (1:N). Kept to two tables
deliberately — a `categories` lookup table would be more "normalized" but adds
migration overhead disproportionate to the value in a 2-hour scope; category is a
free-text column validated against a small allow-list in the API layer instead.

## 4. Anomaly detection rule

Rule-based, per category, using rolling z-score:

1. On each new expense, fetch the trailing N (default 20) expenses in the same
   category (excluding the new one).
2. Compute mean (μ) and sample stddev (σ) of `amount` for that set.
3. If fewer than 5 prior data points exist, skip detection (not enough signal) —
   avoids false positives on a cold-start category.
4. `z = (amount - μ) / σ`. If `σ == 0` (all prior amounts identical), flag only if
   `amount` differs from μ at all.
5. Thresholds: `z >= 2.5` → `warning`, `z >= 4` → `critical`. Write an `Alert` row,
   broadcast it over the WebSocket.

This is deliberately simple and explainable (an interviewer can verify it by hand)
rather than a black-box model. A bonus stretch (isolation forest / seasonal
decomposition) is listed as optional follow-up in Known Limitations, time permitting.

**Known weakness, confirmed during testing**: the trailing window can include a
previous outlier (e.g. a spike from a minute ago), which inflates mean/stddev and
can mask a second anomaly shortly after the first. A more robust version would use
median/MAD instead of mean/stddev, or exclude previously-flagged expenses from the
window. Documented in README's Known Limitations rather than fixed, given the time box.

## 5. Concurrency control

Optimistic locking on `expenses.version`:

- Every `PUT /expenses/{id}` request must include the `version` it read.
- The UPDATE is `UPDATE expenses SET ..., version = version + 1 WHERE id = :id AND
  version = :expected_version`.
- If `rowcount == 0`, the API returns `409 Conflict` — someone else updated the
  record first. The client is expected to refetch and retry.
- This avoids lost-update races without taking DB row locks, and is visible/testable
  by firing two concurrent `PUT`s at the same expense.

## 6. Real-time strategy (ideation angle — full answer lives in README)

WebSockets chosen for alert delivery; short version: alerts are rare, low-volume,
push-shaped events — a persistent WebSocket avoids poll overhead and gives
sub-second latency. Full polling vs. WebSocket trade-off table is in the README
per the assignment's requirement that this be answered there directly.

## 7. Frontend

React + Vite + TypeScript.
- `Dashboard` page: expense form (create), expense table (list, with optimistic
  `version` handling on edit/delete), spending-by-category chart (Recharts),
  and a live "Alerts" panel fed by the WebSocket connection.
- Loading/error states handled per-request via a small `useApi` hook — no global
  loading spinner masking partial failures.

## 8. Out of scope (2-hour cut list)

- Auth/multi-user (single implicit workspace; no login).
- Categories as a managed lookup table.
- Real message broker (Kafka/Redis Streams) — in-process event bus instead.
- ML-based anomaly model (rule-based z-score only; noted as bonus follow-up).
- Full test suite (a handful of backend unit tests for the anomaly rule and
  optimistic-lock conflict path only).
- Pagination beyond a simple limit/offset on the expense list.
