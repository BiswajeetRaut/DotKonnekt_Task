# API Reference

Base URL (local dev): `http://localhost:8000`

This is a hand-written reference for demo/interview purposes. FastAPI also
serves interactive, always-in-sync docs at **`GET /docs`** (Swagger UI) and
the raw schema at **`GET /openapi.json`** — point to those for the
authoritative, generated contract; this file exists to explain the *shape and
reasoning* behind the API rather than replace them.

## Conventions

- **Auth**: every endpoint except `/health`, `/auth/register`, and
  `/auth/login` requires the `access_token` httpOnly cookie set by
  register/login. There is no header/token-based auth — the frontend calls
  `fetch(..., { credentials: "include" })` and the browser attaches the
  cookie automatically (same-site, since dev frontend/backend are both
  `localhost`, just different ports).
- **Ownership scoping**: every query is filtered by the authenticated user's
  `id` at the database layer. A row that exists but belongs to someone else
  returns `404`, not `403` — this avoids confirming a resource ID exists for
  an account that isn't yours.
- **Errors**: FastAPI's default shape, `{"detail": "..."}`. A `422` (Pydantic
  validation failure) instead uses FastAPI's default list-of-errors body.
- **`409 Conflict` is overloaded across two unrelated cases** — always read
  `detail` rather than branching on status code alone:
  - Expense update: someone else changed the row first (optimistic lock).
  - Category delete: the category still has expenses pointing at it.
  - Register/create-category: a duplicate that violates a unique constraint.

## Auth — `/auth`

| Method | Path | Auth required | Body | Response |
|---|---|---|---|---|
| POST | `/auth/register` | no | `{email, password}` | `201` → `UserOut`; sets cookie |
| POST | `/auth/login` | no | `{email, password}` | `200` → `UserOut`; sets cookie |
| POST | `/auth/logout` | yes | — | `204`; clears cookie |
| GET | `/auth/me` | yes | — | `200` → `UserOut` |

`password` must be 8–128 characters (`register` only; `login` just checks
against the stored hash). `email` must be a valid email format.

```jsonc
// UserOut
{ "id": 1, "email": "demo@example.com", "created_at": "2026-07-20T10:00:00Z" }
```

Errors: `409` on register with an email already in use; `401` on login with
wrong email/password (deliberately the same message for both, so a failed
login can't be used to enumerate which emails are registered).

## Categories — `/categories`

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/categories` | — | `200` → `CategoryOut[]`, alphabetical |
| POST | `/categories` | `{name}` | `201` → `CategoryOut` |
| DELETE | `/categories/{id}` | — | `204` |

`name` is 1–64 characters, unique **per user** (two users can each have a
"food" category; one user cannot have two).

Errors: `409` on a duplicate name for the same user; `404` if the category
doesn't exist or belongs to someone else; `409` on delete if any expense
still references it (the FK is `ON DELETE RESTRICT` by design — a silent
cascade-delete would quietly orphan or destroy expense history).

## Expenses — `/expenses`

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/expenses` | query params below | `200` → `ExpenseOut[]` |
| POST | `/expenses` | `ExpenseCreate` | `201` → `ExpenseOut` |
| GET | `/expenses/{id}` | — | `200` → `ExpenseOut` |
| PUT | `/expenses/{id}` | `ExpenseUpdate` | `200` → `ExpenseOut` |
| DELETE | `/expenses/{id}` | — | `204` |

`GET /expenses` query params (all optional): `category_id`, `start_date`,
`end_date` (ISO datetimes, inclusive), `limit` (default 100, max 500),
`offset` (default 0). Results are ordered newest-`occurred_at`-first.

```jsonc
// ExpenseCreate / ExpenseUpdate body
{
  "amount": 42.50,          // Decimal, must be > 0
  "category_id": 3,
  "description": "Groceries",  // optional, ≤500 chars
  "occurred_at": "2026-07-24T09:00:00Z",
  "version": 2               // ExpenseUpdate only — see below
}
```

```jsonc
// ExpenseOut
{
  "id": 12,
  "amount": "42.50",
  "category": { "id": 3, "name": "food", "created_at": "..." },
  "description": "Groceries",
  "occurred_at": "2026-07-24T09:00:00Z",
  "created_at": "2026-07-24T09:00:01Z",
  "updated_at": "2026-07-24T09:00:01Z",
  "version": 1
}
```

**Concurrency control**: `PUT` requires the `version` the client last read.
The `UPDATE` statement conditions on `id AND version` and checks
`rowcount`; if another request updated the row first, `version` no longer
matches, `rowcount == 0`, and the API returns `409` with a message telling
the client to refetch — instead of silently overwriting the other write.

**Side effect on create, not part of the response contract**: `POST
/expenses` schedules `publish_expense_created` as a FastAPI background task
*after* the commit. The CRUD handler has no import of, or awareness of,
anomaly detection or the chatbot indexer — it only publishes an event ID.
Whatever reacts to that event (rule-based detector, LLM detector, embedding
indexer) is wired up in `main.py`'s startup hook, not here. A `201` response
means the expense was saved; it says nothing about whether an alert will
follow — that arrives later, asynchronously, over the WebSocket.

Errors: `400` if `category_id` doesn't exist or belongs to another user;
`404` if the expense doesn't exist or belongs to another user; `409` on
`PUT` version mismatch (see above).

## Alerts — `/alerts`

| Method | Path | Query / Body | Response |
|---|---|---|---|
| GET | `/alerts` | `acknowledged` (bool, optional), `limit` (default 100) | `200` → `AlertOut[]` |
| PATCH | `/alerts/{id}/ack` | — | `200` → `AlertOut` |

Alerts have no `user_id` column of their own — ownership is derived by
joining through `expense_id → expenses.user_id`, so there's one source of
truth for "whose alert is this," not a copy that could drift from the
expense it's about.

```jsonc
// AlertOut
{
  "id": 7,
  "expense_id": 12,
  "reason": "Amount is 5.3 standard deviations above your 'food' mean",
  "severity": "warning",       // or "critical"
  "source": "rule",            // or "llm"
  "z_score": "5.30",           // null for LLM-sourced alerts — no numeric score
  "created_at": "2026-07-24T09:00:03Z",
  "acknowledged": false
}
```

`PATCH /alerts/{id}/ack` is the only mutation — alerts are otherwise
immutable; there's no edit/delete, only acknowledge. `404` if the alert
doesn't exist or its expense belongs to someone else.

## Chatbot — `/chat`

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/chat/history` | — | `200` → `{role, content}[]`, chronological |
| POST | `/chat` | `{message: string}` | `200` → `{reply: string}` |

`POST /chat` is synchronous, non-streaming — the whole tool-calling loop
(up to 4 iterations against OpenAI, including any `search_similar` /
`run_aggregate_query` tool round-trips) completes before the response
returns. Both the user's message and the assistant's reply are persisted to
`chat_messages` after the loop finishes. See `docs/ARCHITECTURE.md` §3.2 for
the full sequence diagram of what happens inside that loop.

There's no dedicated error contract here beyond the standard `401`
(unauthenticated) — a failure inside the OpenAI call surfaces as a generic
`500`.

## WebSocket — `/ws/alerts`

Not a REST endpoint — a persistent connection for real-time alert push.

- **Handshake auth**: reads the same `access_token` httpOnly cookie as REST
  (sent automatically by the browser on same-site WebSocket handshakes, no
  token-in-URL needed). Missing/invalid cookie, or a cookie for a user that
  no longer exists → closed immediately with code `4401`.
- **Client → server**: nothing meaningful is expected; the server just calls
  `receive_text()` in a loop to detect disconnects. No ping/heartbeat
  message is required from the client.
- **Server → client**: one JSON text frame per new alert, shape identical to
  `AlertOut` plus a `type` discriminator field:

```jsonc
{
  "type": "alert",
  "id": 7,
  "expense_id": 12,
  "severity": "critical",
  "source": "rule",
  "reason": "Amount is 5.3 standard deviations above your 'food' mean",
  "z_score": "5.30",
  "created_at": "2026-07-24T09:00:03Z"
}
```

Delivery path: an anomaly detector writes the `Alert` row, then calls
`ws_manager.broadcast(user_id, payload)`, which publishes the payload to a
Redis channel (`alerts_broadcast`) rather than writing to a local socket
directly. Every backend instance's listener receives every broadcast and
forwards it only to that instance's own locally-connected sockets matching
`user_id` — this is what makes delivery correct if the process handling the
`POST /expenses` request isn't the same process holding the user's open
WebSocket (true once there's more than one backend instance).

## Health — `/health`

`GET /health` → `200 {"status": "ok"}`. No auth. Used for container/orchestrator
liveness checks, not a real API resource.

## Known gaps (by design, not oversight)

- No refresh-token rotation — a single ~60-minute access token; re-login
  once it expires. See `docs/V2_ROADMAP.md` "Explicit non-goals."
- No pagination cursor / `Link` header on `GET /expenses` — `limit`/`offset`
  only, capped at 500 per page.
- No rate limiting on `/chat` (each call costs a real OpenAI request).
