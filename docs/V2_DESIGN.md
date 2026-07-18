# V2 Design Doc — Multi-user, Notifications, LLM Signal, Chatbot

Originally a planning doc; five of the six phases below (A-D, F) are now
built and verified live — see [V2_ROADMAP.md](V2_ROADMAP.md) for the
phase-by-phase status and what was actually confirmed working. Only Phase E
(email notifications) remains, blocked on choosing a provider. The design
decisions below reflect what shipped, with notes where reality diverged from
the original plan (mainly: Phase F needed a backfill step the original plan
didn't call out, and the pgvector install hit real local-toolchain friction).

Scope note: this is meaningfully bigger than the original 2-hour assessment.
It's organized as six phases with explicit dependencies, so work proceeded
phase by phase rather than all at once. See [V2_ROADMAP.md](V2_ROADMAP.md)
for the task checklist derived from this doc.

## Dependency graph

```
Phase A (Auth)
   │
   ├──▶ Phase B (Custom categories)
   │        │
   ├──▶ Phase D (LLM signal) ── independent of auth, but better with it
   │        │
   ├──▶ Phase E (Email notifications) ── needs user.email from Phase A
   │
   └──▶ Phase F (Chatbot/RAG) ── needs "my data" scoping from Phase A

Phase C (Redis event bus) ── independent, can run anytime, but pairs
                              naturally with Phase A (WS fan-out needs
                              per-user routing once users exist)
```

Auth (Phase A) is the one true prerequisite — categories, per-user anomaly
baselines, email, and the chatbot all need `user_id` scoping. Redis (Phase C)
and the LLM signal (Phase D) are the two pieces that can technically be built
independently of auth, but Redis's main *payoff* (routing WebSocket alerts to
the right user across instances) only matters once users exist.

## Phase A — Auth & multi-tenancy foundation

**Goal:** every expense/alert belongs to a user; all existing endpoints scope
to the authenticated user.

**Data model changes:**
```
users (id, email UNIQUE, hashed_password, created_at)
expenses: + user_id INTEGER NOT NULL REFERENCES users(id)
alerts: no new column — scoped via expenses.user_id through a join,
        not duplicated onto alerts (avoids two sources of truth for
        "whose row is this")
```

**Auth mechanism:** JWT access tokens (stateless, simple for an SPA) —
`passlib[bcrypt]` for password hashing, `python-jose` for signing/verifying.
No refresh-token rotation in v2 (flagged as a limitation) — a single
reasonably-short-lived access token (e.g. 60 min) is enough for this scope;
real refresh-token rotation is its own project.

**New endpoints:** `POST /auth/register`, `POST /auth/login` (returns JWT),
`GET /auth/me`. A `get_current_user` FastAPI dependency reused across every
existing router.

**Migration path for existing data:** the current seeded expenses have no
owner. Migration creates a `users` row for a default/demo account and backfills
existing `expenses.user_id` to it, so v1.0 data isn't lost.

**WebSocket auth:** browsers can't set custom headers on a WS handshake, so
the token is passed as a query param at connect time
(`/ws/alerts?token=...`), verified once at `connect()`. The `ConnectionManager`
changes from a flat list of sockets to a `dict[user_id, list[WebSocket]]` so
broadcasts route to the right user, not everyone.

**Anomaly detection impact — the interesting design decision:** once
detection is scoped per-user, the cold-start problem (need ≥5 prior points)
gets worse — a brand-new user has zero history in every category. Proposed
fallback: if a user has fewer than `MIN_SAMPLES` expenses in a category, fall
back to a global cross-user baseline for that category (existing behavior)
until they build up enough personal history, then switch to their own. This
needs a flag on the `Alert` record (or just in the `reason` text) noting which
baseline was used, so it's transparent rather than silently different logic.

**Frontend:** Login/Signup pages, an `AuthContext` holding the token, a
protected-route wrapper around the existing dashboard, `Authorization` header
attached to all `fetch` calls in `api.ts`. Token storage: httpOnly cookie
is safer against XSS than `localStorage`; recommend that over
`localStorage`, accepting the small added complexity of CSRF protection
(SameSite=Strict cookie handles most of it for this scope).

**Effort:** the single largest phase — touches every router, every table,
and the frontend's data-fetching layer.

## Phase B — Custom categories

**Goal:** replace the free-text `category` column with a per-user managed
list.

```
categories (id, user_id, name, created_at) — UNIQUE(user_id, name)
expenses: category_id INTEGER REFERENCES categories(id)  (replaces category TEXT)
```

Migration: for each user, create `categories` rows from their existing
distinct `expenses.category` text values, then point `expenses.category_id`
at the matching row.

**New endpoints:** `GET/POST/DELETE /categories`.

**Frontend:** category management (add/rename/delete) in a settings area;
`ExpenseForm`'s category `<select>` now sources from `GET /categories`
instead of the hardcoded list.

**Anomaly service impact:** trivial — key the rolling window by
`category_id` instead of the category string.

## Phase C — Redis event bus + WebSocket fan-out

**Goal:** replace the in-process `events.py` pub/sub with Redis Pub/Sub, and
make WebSocket broadcast work across more than one backend instance.

**Why Redis over Kafka** (as discussed): this app's event volume doesn't
justify Kafka's operational weight (partitions, consumer groups, a
Zookeeper/KRaft cluster). Redis gets three wins in one dependency: Pub/Sub
for the event bus, Streams if replay/durability is ever needed, and it's the
natural place to put WebSocket fan-out state and — later — server-side
sessions.

**Mechanics:**
- `POST /expenses` publishes `expense.created` to a Redis channel instead of
  calling `BackgroundTasks.add_task` directly.
- The analytics service becomes a small standalone subscriber process (can
  still run in the same container to start, but the interface no longer
  assumes "same process" — this is the point where the assessment's
  "separate service" language becomes literally true, not just
  modularly true).
- WebSocket fan-out: every backend instance subscribes to a shared
  `alerts:{user_id}` (or a single `alerts` channel with `user_id` in the
  payload) and forwards to whichever of its own locally-connected sockets
  match — solves the "alert fires on instance A, user's socket is on
  instance B" problem that the current in-memory `ConnectionManager` can't.

**Effort:** moderate. New dependency (`redis-py` async client), a small
subscriber loop started as an asyncio task at app startup, and the existing
`events.py`/`ws_manager.py` interfaces stay basically the same shape from the
router's point of view — this is an internal swap, not a router-facing change.

## Phase D — LLM as a second anomaly signal

**Goal:** catch semantic mismatches the z-score can't — e.g. a $2,000 expense
described as "lunch," or a description that doesn't match its category.

**Design:** a new `analytics/llm_signal.py`, subscribed to the same
`expense.created` event as the z-score detector — a second, independent
subscriber, not a replacement. Input: `(description, amount, category, a
handful of the user's recent expenses in that category)`. Output: a
structured judgment via OpenAI's Structured Outputs (`response_format:
{"type": "json_schema", ...}` on `chat.completions`, or the newer
`responses` API) — not free-text parsing — `{flagged: bool, reason: str}`.
Model choice: a small/cheap model (e.g. `gpt-4o-mini`) is enough for this
classification-shaped task; no need for a top-tier model here.

**Cost control (the real open question):** running an LLM call on *every*
expense creation has real per-call cost and latency. Two options:
1. Always run it (simplest, highest cost).
2. Run it only when the z-score is inconclusive/borderline (e.g. z between
   1.5–2.5) — use the LLM as a net for cases the cheap statistical rule is
   unsure about, not for everything. **Recommended** — better cost/value
   ratio, and it's a defensible architectural story ("cheap rule first, LLM
   as an escalation path") for an interviewer too.

**Schema change:** `alerts.source` (`'rule'` | `'llm'`) so the dashboard can
distinguish and the two detectors' track records can be compared.

**Requires:** an OpenAI API key for the actual LLM calls — user has one,
to be added via `.env` when this phase is implemented.

## Phase E — Email notifications

**Goal:** critical alerts reach the user even with the browser closed.

**Requires (open questions to resolve before building):**
- An email-sending provider — SMTP credentials, or an API key for
  Resend/Postmark/SendGrid. Not yet chosen/provided.
- `users.email` from Phase A.

**Design:** `notifications/email_service.py` — another independent
subscriber on the alert-created event (same pattern as the WebSocket
broadcaster and the LLM signal: one event, multiple decoupled subscribers).
Only `critical` severity triggers an email by default, to avoid spam; a
`notification_preferences` table (per user: email on/off, instant vs. daily
digest) is a natural small addition here rather than hardcoding the policy.

## Phase F — Chatbot over expenses & alerts (pgvector RAG) — BUILT, verified

**Goal:** let a user ask conversational questions ("why was I flagged last
week?", "how much did I spend on food in June?") against their own data.

**This was the largest, highest-uncertainty phase — and the estimate held.**
Full verification detail in [V2_ROADMAP.md](V2_ROADMAP.md) Phase F.

**Infra dependency, resolved (with real friction):** `pgvector` was not
available on the local Postgres 18 (EDB installer) instance and had to be
built from source. Two blockers came up, not just one: `-march=native` isn't
valid for a universal (x86_64+arm64) build (fixed with pgvector's own
`OPTFLAGS=""` override), and the EDB Postgres build itself was compiled
expecting an Xcode SDK (`MacOSX14.sdk`) that isn't installed on this
unsupported macOS 12 machine at all (fixed with a symlink to the closest
available SDK). Both the `make install` step and the SDK symlink needed
`sudo` — done by the user directly in their own terminal, since there's no
way to pass a password through an automated tool call. Docker Compose's `db`
image was switched to `pgvector/pgvector:pg16` (bundles the extension) for
whoever runs the containerized setup instead.

**Gap found during verification, not anticipated in the original plan:** the
indexer only embeds *new* expenses/alerts going forward (event-driven, by
design). The first real chatbot question asked about a pre-existing flagged
expense and got a confidently wrong answer — the vector store had exactly
one chunk (from a brand-new test expense), and the model reasoned from
whatever was closest, since nothing relevant actually existed yet. Fixed
with a one-time backfill script (`app/chat/backfill.py`) that indexes all
existing expenses/alerts; re-running the same question afterward returned
the correct, grounded answer. Documented rather than silently patched over,
since it's a real instance of "a RAG system is only as good as what's
actually indexed."

**Why naive RAG isn't enough here:** "how much did I spend in June" is an
*aggregation* question, not a similarity-search question — pure vector
retrieval over expense text can't sum numbers. The design needs to be
**agentic RAG**, not naive RAG: give the chat LLM two tools and let it decide
which to call per question, rather than always doing one fixed retrieval step:
- `search_similar(query) -> chunks` — semantic search over embedded
  expense/alert text, for "why"/"which" questions.
- `run_aggregate_query(filters) -> numbers` — a constrained, parameterized
  query (not free-form SQL generation — that's a SQL-injection and
  correctness risk) for "how much"/"how many" questions.

**Data model:**
```
knowledge_chunks (id, user_id, source_type ['expense'|'alert'], source_id,
                   content, embedding vector(N), created_at)
```
A dedicated table rather than embedding columns bolted onto `expenses`/
`alerts` — keeps the core CRUD tables free of chatbot-specific concerns, and
reuses the same "subscribe to the event, do your own thing" pattern already
established by the analytics service and (planned) email service: an indexer
subscribes to `expense.created`/`alert.created` and upserts a chunk.

**Requires:** an OpenAI API key — user has one, to be added via `.env` when
this phase is implemented. Embeddings via `text-embedding-3-small` (1536
dims, cheap, plenty for this scale — `-large` is overkill here), chat
completions via a standard chat model (e.g. `gpt-4o` or `gpt-4o-mini`
depending on how much reasoning the tool-routing needs) using OpenAI's
function/tool calling for the two tools below.

**New surface:** `POST /chat` (streaming, likely via SSE or the existing
WebSocket infra) + a `chat_messages` table for conversation history, plus a
chat panel in the frontend.

## Open questions before implementation can start

1. ~~LLM/embedding provider~~ — resolved: OpenAI for both. Key added to
   `backend/.env` (gitignored).
2. Email provider/credentials — still needed, only remaining blocker, for
   Phase E.
3. ~~Confirm `pgvector` extension availability~~ — resolved: not available
   out of the box, built from source (see Phase F notes above). Now
   installed and verified working on the local Postgres 18 instance.
4. ~~JWT-in-cookie vs. `localStorage`~~ — resolved: httpOnly cookie,
   built and verified in Phase A.
