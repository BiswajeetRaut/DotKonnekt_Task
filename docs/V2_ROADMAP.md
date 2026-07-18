# V2 Roadmap — Task Checklist

Companion to [V2_DESIGN.md](V2_DESIGN.md). Nothing here is started — this is
the checklist to work through once phase order and open questions are
resolved. Status legend: `[ ]` todo, `[~]` in progress, `[x]` done.

## Open questions to resolve before starting (see V2_DESIGN.md §"Open questions")
- [x] LLM + embedding provider — OpenAI for both (user has API key, adds to
      `.env` when Phase D/F start)
- [ ] Email provider/credentials chosen for Phase E
- [ ] `pgvector` extension confirmed available on local Postgres 18
- [x] JWT storage: httpOnly cookie (`SameSite=Lax`) — built and confirmed
      working; localhost:5173/8000 are same-site so the cookie round-trips
      automatically on `fetch` (with `credentials: "include"`) and on the
      WebSocket handshake, no query-param token needed

## Phase A — Auth & multi-tenancy foundation — DONE, verified end-to-end
- [x] `users` table + Alembic migration (`0002_users_and_expense_owner.py`)
- [x] `expenses.user_id` column + migration — backfilled all 86 pre-auth rows
      to a `demo@example.com` / `demo1234` account so v1.0 seed data wasn't lost
- [x] Password hashing (`passlib[bcrypt]`, pinned to `bcrypt==4.0.1` — newer
      bcrypt 4.1+ breaks passlib 1.7.4's version probe) + JWT issue/verify
      (`python-jose`)
- [x] `POST /auth/register`, `POST /auth/login`, `POST /auth/logout`, `GET /auth/me`
- [x] `get_current_user` dependency; applied to all expense/alert routes
- [x] Scoped `GET/POST/PUT/DELETE /expenses`, `GET/PATCH /alerts`, and the
      anomaly service's trailing-window query all to `user_id` — verified a
      second user sees zero of the first user's data
- [x] Cold-start fallback implemented and verified: a brand-new user's first
      expense correctly fell back to the global category baseline, with the
      alert reason explicitly noting "cross-user category baseline — not
      enough personal history yet"
- [x] WebSocket auth via the httpOnly cookie (sent automatically on the
      handshake, same-site) — verified an unauthenticated connection gets
      rejected with HTTP 403 pre-accept, and an authenticated connection
      receives only its own user's alert pushes
- [x] Frontend: `AuthContext`, `AuthPage` (login/register), `Dashboard`
      extracted from `App.tsx`, session persists across reload — verified
      with a Playwright login-through-dashboard screenshot

## Phase B — Custom categories (depends on Phase A) — DONE, verified end-to-end
- [x] `categories` table (`user_id`, `name`, unique together) + migration
      (`0003_categories.py`)
- [x] Migration: backfilled each user's distinct free-text categories into
      rows — verified demo user got 4 category rows, the second test user
      got its own separate `food` row (no cross-user leakage)
- [x] `expenses.category_id` replacing `category` text column; FK is
      `ondelete=RESTRICT` — deleting a category with expenses on it returns
      `409`, verified directly
- [x] `GET/POST/DELETE /categories` — duplicate name correctly rejected with
      `409`, unused category deletes cleanly with `204`
- [x] Frontend: `CategoryManager` (list + delete + inline add form),
      `ExpenseForm`/`ExpenseTable` select sourced from the API instead of a
      hardcoded list — verified adding "utilities" via the UI and using it
      immediately to log an expense
- [x] Anomaly service: rolling window keyed by `category_id`; alert reason
      now names the category (e.g. `'transport' mean`) instead of the
      generic "category mean"

## Phase C — Redis event bus + WS fan-out — DONE, verified end-to-end
- [x] Added `redis` service to `docker-compose.yml` (untested there, same
      Docker caveat as before) and installed Redis locally via Homebrew
      (built from source — no bottle for this unsupported macOS 12 config —
      took ~15-20 min) to verify live instead
- [x] Replaced `events.publish`/`subscribe` internals with Redis Pub/Sub
      (`app/events.py`) — router-facing interface unchanged, per the design
- [x] `ws_manager.py` broadcast now publishes to a Redis channel
      (`alerts_broadcast`) instead of writing to local sockets directly;
      each instance's listener forwards only to its own locally-connected
      matching sockets
- [x] Documented an honest correctness caveat directly in `events.py`: Pub/Sub
      is fan-out, correct for one backend instance; scaling the analytics
      service to multiple consumers would need Redis Streams + a consumer
      group to avoid duplicate-processing — not built, flagged as a
      follow-up rather than silently risking double alerts
- [x] Verified live: created an anomalous expense, confirmed it flowed
      through the real Redis `expense_created` channel to the analytics
      service, which wrote an Alert and broadcast it through the real Redis
      `alerts_broadcast` channel to a connected WebSocket client — full
      round trip through actual Redis, not mocked

## Phase D — LLM secondary anomaly signal (independent, better after A)
- [ ] `alerts.source` column (`'rule'` | `'llm'`) + migration

## Phase D — LLM secondary anomaly signal — DONE, verified end-to-end
- [x] `alerts.source` column (`'rule'` | `'llm'`) + migration (`0004_alert_source.py`)
- [x] `analytics/llm_signal.py` — second independent subscriber on
      `expense.created`, alongside (not instead of) the rule-based detector
- [x] OpenAI Structured Outputs call (`gpt-4o-mini`, Pydantic schema via
      `client.beta.chat.completions.parse` — not free-text parsing) —
      `{flagged, reason}`
- [x] Cost-control gate implemented as designed: only invoke the LLM on
      cold-start (insufficient history) or borderline z-score cases; skip
      when the rule already fired or the expense is clearly ordinary
- [x] Frontend: color-coded "RULE"/"AI" badge on each alert (`AlertsPanel`)
- [x] Verified live against the real OpenAI API (key added to `backend/.env`,
      gitignored, never committed):
  - A cold-start category (1 prior expense) with a mismatched description
    ("concert tickets" filed under "utilities") was correctly flagged by
    the LLM with a clear explanation — a case the z-score rule structurally
    cannot catch (no numeric outlier at all)
  - A second mismatched expense in the same category was also caught and
    correctly pushed over the WebSocket with `source: "llm"` intact
  - A legitimately normal, correctly-categorized expense in the same
    cold-start category ("monthly electricity bill") was *not* flagged —
    confirms the check isn't just flagging everything indiscriminately
  - A rule-triggering expense (z=5.30) did not also fire the LLM check —
    the cost gate correctly skipped it since the rule already handled it

## Phase E — Email notifications (depends on Phase A for user.email)
- [ ] Choose provider, store credentials in `.env` (never commit)
- [ ] `notifications/email_service.py` — subscriber on alert-created,
      critical severity only by default
- [ ] `notification_preferences` table (email on/off, instant vs. digest)
- [ ] Frontend: notification preferences UI

## Phase F — Chatbot / RAG over expenses & alerts (depends on Phase A)
- [ ] Confirm/install `pgvector` extension
- [ ] `knowledge_chunks` table (dedicated, not bolted onto core tables)
- [ ] Indexer subscriber: embeds new expense/alert text via OpenAI
      `text-embedding-3-small` on create event
- [ ] `search_similar(query)` tool — vector similarity search
- [ ] `run_aggregate_query(filters)` tool — constrained/parameterized, not
      free-form SQL generation (injection + correctness risk)
- [ ] Chat LLM (OpenAI, function/tool calling) wired with both tools
      (agentic RAG, not naive single-retrieval RAG)
- [ ] `chat_messages` table for conversation history
- [ ] `POST /chat` endpoint (streaming — SSE or reuse WebSocket infra)
- [ ] Frontend: chat panel

## Explicit non-goals for V2 (unless you ask otherwise)
- Refresh-token rotation (single reasonably-short-lived access token instead)
- Free-form SQL generation for the chatbot (constrained query tool instead)
- Multi-tenant org/team accounts (single user per account, no sharing/teams)
