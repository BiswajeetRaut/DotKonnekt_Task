# Architecture Reference

Full technical picture of the system: how the pieces fit together, the data
model, the request/event flows, and what each file is responsible for. This
is the "read this to understand the whole system" doc — for *why* each
decision was made, see [DESIGN.md](DESIGN.md) (v1.0) and
[V2_DESIGN.md](V2_DESIGN.md) (auth, categories, Redis, LLM signal, chatbot).

## 1. System architecture

```mermaid
flowchart TB
    subgraph Client["Browser — React SPA"]
        UI["Dashboard components<br/>(ExpenseForm, ExpenseTable, SpendingChart,<br/>AlertsPanel, CategoryManager, ChatPanel)"]
        WSClient["WebSocket client<br/>(useWebSocketAlerts)"]
        Notif["Browser Notification API"]
    end

    subgraph API["FastAPI Backend"]
        Routers["Routers:<br/>auth · categories · expenses · alerts · chat"]
        WSEndpoint["/ws/alerts endpoint"]
    end

    subgraph Analytics["Independent event subscribers"]
        Rule["anomaly_service.py<br/>(rolling z-score rule)"]
        LLM["llm_signal.py<br/>(OpenAI semantic check)"]
        Indexer["chat/indexer.py<br/>(embeds into knowledge_chunks)"]
    end

    subgraph ChatSvc["Chatbot"]
        Loop["chat/service.py<br/>(tool-calling loop)"]
        Tools["chat/tools.py<br/>(search_similar, run_aggregate_query)"]
    end

    subgraph Redis["Redis Pub/Sub"]
        EvBus["expense_created<br/>alert_created"]
        Broadcast["alerts_broadcast"]
    end

    subgraph DB["PostgreSQL + pgvector"]
        Tables[("users · categories · expenses · alerts<br/>knowledge_chunks · chat_messages")]
    end

    OpenAI[["OpenAI API<br/>gpt-4o-mini + text-embedding-3-small"]]

    UI -- "REST (fetch, httpOnly cookie)" --> Routers
    WSClient -. "WebSocket connect (cookie auth)" .-> WSEndpoint
    Routers -- "publish expense_created<br/>(BackgroundTask, after commit)" --> EvBus
    Routers -- "CRUD (read/write)" --> Tables

    EvBus --> Rule
    EvBus --> LLM
    EvBus --> Indexer

    Rule -- "read history, write Alert" --> Tables
    LLM -- "read history, write Alert" --> Tables
    LLM -. "structured-output judgment" .-> OpenAI
    Rule -- "publish alert_created" --> EvBus
    LLM -- "publish alert_created" --> EvBus

    Indexer -. "embed text" .-> OpenAI
    Indexer -- "write knowledge_chunks" --> Tables

    Rule -- "broadcast(user_id, alert)" --> Broadcast
    LLM -- "broadcast(user_id, alert)" --> Broadcast
    Broadcast --> WSEndpoint
    WSEndpoint -. "push" .-> WSClient
    WSClient --> Notif

    Routers --> Loop
    Loop -. "tool-calling loop" .-> OpenAI
    Loop --> Tools
    Tools -- "vector search / aggregate SQL" --> Tables
```

**Key architectural properties:**
- **CRUD is dumb on purpose.** The `expenses` router only validates, inserts,
  and publishes an event — it has zero knowledge that anomaly detection, a
  chatbot, or anything else exists downstream.
- **Three independent subscribers** react to the same `expense_created`
  event without knowing about each other: the rule-based detector, the LLM
  detector, and the chat indexer. Any one of them could be deleted or moved
  to its own process without touching the other two or the router.
- **Two different Redis usage patterns, deliberately**: `expense_created`/
  `alert_created` are consumed by whichever subscribers are registered in
  *this* process (fine for one backend instance; would need Streams +
  consumer groups to fan work across multiple instances without duplicate
  processing — see V2_DESIGN.md). `alerts_broadcast` is genuine fan-out by
  design — every backend instance needs to receive it and check its own
  local WebSocket connections, since the instance that raised the alert
  isn't necessarily the one holding the user's socket.
- **The chatbot is agentic, not a fixed pipeline.** `chat/service.py` doesn't
  hardcode "embed the question, retrieve, answer" — it hands the model two
  tools and lets it decide per-question whether it needs semantic search,
  an aggregate query, both, or neither.

## 2. Entity-relationship diagram

```mermaid
erDiagram
    USERS ||--o{ CATEGORIES : owns
    USERS ||--o{ EXPENSES : owns
    USERS ||--o{ KNOWLEDGE_CHUNKS : owns
    USERS ||--o{ CHAT_MESSAGES : owns
    CATEGORIES ||--o{ EXPENSES : classifies
    EXPENSES ||--o{ ALERTS : triggers

    USERS {
        int id PK
        string email UK
        string hashed_password
        datetime created_at
    }
    CATEGORIES {
        int id PK
        int user_id FK
        string name "unique per user"
        datetime created_at
    }
    EXPENSES {
        int id PK
        int user_id FK
        int category_id FK "ON DELETE RESTRICT"
        decimal amount
        string description
        datetime occurred_at
        datetime created_at
        datetime updated_at
        int version "optimistic lock"
    }
    ALERTS {
        int id PK
        int expense_id FK "ON DELETE CASCADE"
        string reason
        string severity "warning | critical"
        string source "rule | llm"
        decimal z_score "null for llm-sourced"
        datetime created_at
        bool acknowledged
    }
    KNOWLEDGE_CHUNKS {
        int id PK
        int user_id FK
        string source_type "expense | alert"
        int source_id "unique with source_type"
        text content
        vector embedding "1536-dim, HNSW cosine index"
        datetime created_at
    }
    CHAT_MESSAGES {
        int id PK
        int user_id FK
        string role "user | assistant"
        text content
        datetime created_at
    }
```

Notes on deliberate design choices:
- **`alerts` has no `user_id` column.** Ownership is derived through
  `alerts.expense_id -> expenses.user_id` — a single source of truth for
  "whose row is this," rather than a denormalized copy that could drift.
- **`categories.name` is unique per user, not globally** — two different
  users can both have a category called "Groceries"; they're different rows
  with different ids.
- **`expenses.category_id` is `ON DELETE RESTRICT`**, not `CASCADE` or
  `SET NULL` — deleting a category that still has expenses fails loudly
  (`409`) rather than silently orphaning or nulling data.
- **`knowledge_chunks` is a dedicated table**, not embedding columns bolted
  onto `expenses`/`alerts` — keeps the core CRUD tables free of
  chatbot-specific concerns; `(source_type, source_id)` is unique so
  re-indexing (e.g. the backfill script) upserts rather than duplicates.

## 3. Request & event flows

### 3.1 Expense creation → dual anomaly detection → real-time push

```mermaid
sequenceDiagram
    actor User
    participant React
    participant API as FastAPI (expenses router)
    participant Redis
    participant Rule as anomaly_service.py
    participant LLM as llm_signal.py
    participant Indexer as chat/indexer.py
    participant OpenAI
    participant DB as PostgreSQL
    participant WSMgr as ws_manager.py

    User->>React: Submit "Add expense"
    React->>API: POST /expenses (cookie auth)
    API->>DB: INSERT expense
    API-->>React: 201 Created
    API->>Redis: PUBLISH expense_created (BackgroundTask)

    par rule-based detector
        Redis-->>Rule: expense_created
        Rule->>DB: fetch trailing window (user-scoped,<br/>fallback to cross-user if cold-start)
        alt z-score over threshold
            Rule->>DB: INSERT alert (source=rule)
            Rule->>Redis: PUBLISH alert_created
            Rule->>WSMgr: broadcast(user_id, alert)
        end
    and LLM detector
        Redis-->>LLM: expense_created
        LLM->>DB: fetch trailing window
        alt cost gate passes (cold-start or borderline z)
            LLM->>OpenAI: structured-output judgment
            OpenAI-->>LLM: {flagged, reason}
            alt flagged
                LLM->>DB: INSERT alert (source=llm)
                LLM->>Redis: PUBLISH alert_created
                LLM->>WSMgr: broadcast(user_id, alert)
            end
        end
    and chat indexer
        Redis-->>Indexer: expense_created
        Indexer->>OpenAI: embed(expense text)
        OpenAI-->>Indexer: embedding vector
        Indexer->>DB: UPSERT knowledge_chunks
    end

    WSMgr->>Redis: PUBLISH alerts_broadcast
    Redis-->>WSMgr: (every instance's listener receives it)
    WSMgr-->>React: WebSocket push (only to this user's sockets)
    React-->>User: AlertsPanel updates instantly + browser notification if tab backgrounded
```

### 3.2 Chatbot — agentic RAG

```mermaid
sequenceDiagram
    actor User
    participant React
    participant API as /chat router
    participant ChatSvc as chat/service.py
    participant OpenAI
    participant Tools as chat/tools.py
    participant DB as PostgreSQL + pgvector

    User->>React: Type a question
    React->>API: POST /chat {message}
    API->>ChatSvc: answer(db, user_id, message)
    ChatSvc->>DB: load last 10 chat_messages for context
    ChatSvc->>OpenAI: chat.completions(messages, tools=[search_similar, run_aggregate_query])

    alt model requests search_similar
        OpenAI-->>ChatSvc: tool_call: search_similar(query)
        ChatSvc->>Tools: search_similar(db, user_id, query)
        Tools->>OpenAI: embed(query) [text-embedding-3-small]
        Tools->>DB: ORDER BY embedding <=> :vector LIMIT k<br/>(HNSW cosine index, scoped to user_id)
        DB-->>Tools: matching chunk text
    else model requests run_aggregate_query
        OpenAI-->>ChatSvc: tool_call: run_aggregate_query(args)
        ChatSvc->>Tools: run_aggregate_query(db, user_id, args)
        Tools->>DB: SUM/COUNT/AVG, typed filters only<br/>(never free-form SQL)
        DB-->>Tools: numeric result
    end

    Tools-->>ChatSvc: tool result (JSON)
    ChatSvc->>OpenAI: chat.completions(messages + tool result)
    OpenAI-->>ChatSvc: final natural-language answer
    ChatSvc->>DB: persist user message + assistant reply
    ChatSvc-->>API: reply
    API-->>React: {reply}
    React-->>User: shown in ChatPanel
```

Why two tools instead of one retrieval step: "how much did I spend on
groceries" is a sum, not a similarity search — pure vector retrieval cannot
add numbers. Giving the model both tools and letting it choose is what makes
this **agentic** RAG rather than a fixed embed-then-answer pipeline.

### 3.3 Auth

```mermaid
sequenceDiagram
    actor User
    participant React
    participant API as /auth router
    participant DB

    User->>React: Submit login form
    React->>API: POST /auth/login {email, password}
    API->>DB: fetch user by email
    API->>API: verify_password (bcrypt)
    API->>API: create_access_token (JWT, HS256, 60min expiry)
    API-->>React: Set-Cookie: access_token (httpOnly, SameSite=Lax) + user JSON
    React->>React: AuthContext: status = authenticated

    Note over React,API: Every subsequent fetch() sends the cookie<br/>automatically (credentials: "include") — no<br/>token handling in frontend code at all

    React->>API: GET /expenses (cookie sent automatically)
    API->>API: get_current_user dependency decodes cookie
    API-->>React: data scoped to that user only

    Note over React,API: WebSocket handshake also carries the cookie<br/>automatically (same-site) — same auth path,<br/>no token-in-URL needed
```

### 3.4 Optimistic locking (concurrent edit safety)

```mermaid
sequenceDiagram
    actor UserA as Client A
    actor UserB as Client B
    participant API
    participant DB

    UserA->>API: GET /expenses/5 -> version=1
    UserB->>API: GET /expenses/5 -> version=1
    UserA->>API: PUT /expenses/5 {..., version: 1}
    API->>DB: UPDATE ... WHERE id=5 AND version=1
    DB-->>API: 1 row affected
    API->>DB: version becomes 2
    API-->>UserA: 200 OK (version now 2)

    UserB->>API: PUT /expenses/5 {..., version: 1}
    API->>DB: UPDATE ... WHERE id=5 AND version=1
    DB-->>API: 0 rows affected (version is already 2)
    API-->>UserB: 409 Conflict
    Note over UserB: Frontend catches ConflictError,<br/>shows the real message, refetches
```

## 4. Component-level reference

### Backend (`backend/app/`)

| File | Responsibility |
|---|---|
| `main.py` | FastAPI app assembly; CORS; wires event subscriptions and starts the two Redis listener loops at startup; the `/ws/alerts` endpoint and `/health` |
| `config.py` | Pydantic `Settings` — all env-driven config (DB URL, Redis URL, JWT secret, OpenAI key) |
| `database.py` | SQLAlchemy engine + session factory, `get_db` dependency |
| `models.py` | ORM models: `User`, `Category`, `Expense`, `Alert`, `KnowledgeChunk`, `ChatMessage` |
| `schemas.py` | Pydantic request/response schemas for every endpoint |
| `auth.py` | Password hashing (`passlib`/bcrypt), JWT issue/verify (`python-jose`) |
| `dependencies.py` | `get_current_user` — the one FastAPI dependency every protected route shares |
| `events.py` | Redis Pub/Sub event bus — generalized multi-channel (`expense_created`, `alert_created`); router-facing API is just `publish_expense_created`/`publish_alert_created`/`subscribe` |
| `ws_manager.py` | Redis-backed WebSocket connection manager; tracks per-user local sockets, broadcasts via a separate Redis channel so any backend instance can reach any connected user |
| `seed.py` | Demo data — backfills the `demo@example.com` account with realistic history + 2 outliers |
| `routers/auth.py` | `POST /auth/register`, `/login`, `/logout`, `GET /auth/me` |
| `routers/categories.py` | `GET/POST /categories`, `DELETE /categories/{id}` |
| `routers/expenses.py` | Full expense CRUD; optimistic-lock `PUT`; publishes `expense_created` after commit |
| `routers/alerts.py` | `GET /alerts`, `PATCH /alerts/{id}/ack` |
| `routers/chat.py` | `POST /chat`, `GET /chat/history` |
| `analytics/anomaly_service.py` | Rolling per-category z-score rule — pure `detect_anomaly()` function + DB-aware `evaluate_expense()` wrapper; per-user baseline with cross-user cold-start fallback |
| `analytics/llm_signal.py` | Second, independent detector — OpenAI structured-output call for semantic category/description mismatches; cost-gated to cold-start/borderline cases only |
| `chat/embeddings.py` | Thin wrapper around OpenAI's embeddings API (`text-embedding-3-small`) |
| `chat/indexer.py` | Subscribes to `expense_created`/`alert_created`; embeds new rows into `knowledge_chunks` |
| `chat/backfill.py` | One-time script to index pre-existing data (run once after enabling the chatbot on an established dataset) |
| `chat/tools.py` | The two agentic-RAG tools: `search_similar` (pgvector cosine search) and `run_aggregate_query` (constrained, parameterized — never free-form SQL) |
| `chat/service.py` | The tool-calling loop — orchestrates the conversation, executes whichever tools the model requests, persists history |
| `alembic/versions/` | `0001` initial schema · `0002` users + expense ownership · `0003` categories · `0004` alert source · `0005` pgvector + chatbot tables |

### Frontend (`frontend/src/`)

| File | Responsibility |
|---|---|
| `main.tsx` | Entry point, wraps `App` in `AuthProvider` |
| `App.tsx` | Top-level gate on auth status: loading spinner / `AuthPage` / `Dashboard` |
| `Dashboard.tsx` | The authenticated view — owns all data loading (expenses, alerts, categories), wires the WebSocket callback, composes every card |
| `api.ts` | Typed `fetch` client for every endpoint; central error handling (`ConflictError`, `AuthError`) |
| `notifications.ts` | Browser `Notification` API helpers — permission check/request, `notifyAlert()` |
| `auth/AuthContext.tsx` | Session state (`loading`/`anonymous`/`authenticated`), login/register/logout |
| `auth/AuthPage.tsx` | Combined login/register form |
| `hooks/useWebSocketAlerts.ts` | WebSocket connection with automatic reconnect/backoff |
| `components/ExpenseForm.tsx` | Create-expense form, category-aware |
| `components/ExpenseTable.tsx` | List/inline-edit/delete; surfaces `409` conflicts explicitly rather than silently retrying |
| `components/SpendingChart.tsx` | Recharts bar chart, spend by category |
| `components/AlertsPanel.tsx` | Live alert list with acknowledge action and rule/AI source badges |
| `components/CategoryManager.tsx` | Category add/delete |
| `components/ChatPanel.tsx` | Chat UI — message log, input, loading state |
| `components/NotificationToggle.tsx` | Shows/requests browser notification permission |

## 5. Cross-cutting concerns

| Concern | How it's handled | Where |
|---|---|---|
| **Auth** | JWT in httpOnly cookie, `SameSite=Lax` | `auth.py`, `dependencies.py`, `AuthContext.tsx` |
| **Data isolation** | Every query scoped to `current_user.id`; alerts scoped via their expense's owner | every router |
| **Concurrency** | Optimistic locking (`expenses.version`) | `routers/expenses.py` |
| **Referential integrity** | `ON DELETE RESTRICT` on `expenses.category_id`, `409` surfaced to the user | `routers/categories.py` |
| **Real-time delivery** | WebSocket + Redis Pub/Sub fan-out | `ws_manager.py` |
| **Separation of concerns** | CRUD routers never import analytics/chat logic — only publish events | `events.py` + all three subscribers |
| **Cost control (LLM)** | Gate on cold-start/borderline-z before calling OpenAI | `llm_signal.py::_should_invoke` |
| **Cost/correctness (chatbot)** | Constrained aggregate tool instead of LLM-generated SQL or LLM-estimated sums | `chat/tools.py::run_aggregate_query` |
