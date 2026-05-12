# KRA AI Report Builder Agent

An AI-powered backend that converts plain-English questions into MySQL queries, executes them against the `vthink_kra` database, and returns structured results. Includes intent detection, clarification flow, live report refresh via Redis, and WebSocket streaming.

---

## Table of Contents

- [Overview](#overview)
- [Key Features](#key-features)
- [Architecture](#architecture)
- [Pipeline Flows](#pipeline-flows)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [API Reference](#api-reference)
- [Module Reference](#module-reference)
- [Retry Logic](#retry-logic)
- [SQL Safety Rules](#sql-safety-rules)
- [Caching Strategy](#caching-strategy)
- [Conversation Memory](#conversation-memory)
- [Dependencies](#dependencies)

---

## Overview

The KRA AI Report Builder Agent exposes a REST + WebSocket API on **port 8001**. A user submits a plain-English question; the agent:

1. **Classifies intent** — is the question KRA-related? Does it have enough detail?
2. **Clarifies if needed** — asks one focused follow-up question (max 2 rounds)
3. **Generates SQL** — builds a prompt, calls GPT-4o-mini, cleans and validates the result
4. **Executes against MySQL** — runs the validated query on `vthink_kra`
5. **Caches the SQL in Redis** — enables instant refresh without re-calling the LLM
6. **Returns structured results** — with `dimensions` and `recommended_column_filters` for frontend dashboards

---

## Key Features

| Feature | Description |
|---------|-------------|
| Intent Detection | Classifies every query into off_topic / incomplete / clear before SQL generation |
| Clarification Flow | Asks focused follow-up questions; forces SQL generation after 2 rounds |
| LLM SQL Generation | GPT-4o-mini converts natural language to validated MySQL SELECT |
| Self-Healing Retries | Feeds validation/execution errors back to GPT for up to 2 self-corrections |
| In-Memory Query Cache | LRU cache with TTL — repeat queries return instantly, no LLM call |
| Redis SQL Cache | Post-validation SQL cached per session — refresh bypasses entire LLM pipeline |
| HTTP Refresh | `GET /report/refresh/{session_id}` — re-executes cached SQL, no LLM cost |
| WebSocket Streaming | `WS /report/stream/{session_id}` — auto-refreshes data every N seconds |
| Conversation Memory | Per-user Q&A history stored in MySQL, injected into future prompts |
| Filter Recommender | Automatically suggests which columns are suitable for frontend filter dropdowns |
| SQL Safety Gate | 19 compiled patterns block any non-SELECT operation before execution |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                        FastAPI  (port 8001)                          │
│                                                                       │
│  POST /api/v1/query              ← Legacy single-step endpoint        │
│  POST /api/v1/report/generate    ← Intent-aware generation            │
│  POST /api/v1/report/clarify     ← Submit answer to follow-up         │
│  GET  /api/v1/report/refresh/:id ← Re-execute cached SQL (no LLM)    │
│  WS   /api/v1/report/stream/:id  ← Auto-refresh via WebSocket         │
│  GET  /api/v1/cache/stats        ← Query cache metrics                │
│  DELETE /api/v1/cache            ← Flush query cache                  │
└──────────────────────┬──────────────────────────────────────────────┘
                       │
          ┌────────────▼────────────┐
          │   LangGraph StateGraph   │    ← Main Pipeline
          │                          │
          │  load_context            │
          │       │                  │
          │  cache_lookup ──hit──► memory_store ─► END
          │       │ miss             │
          │  intent_detector         │
          │  ├─ off_topic ─────────► END
          │  ├─ incomplete ────────► END  (returns follow_up_question)
          │  └─ clear               │
          │       │                  │
          │  prompt_builder          │
          │  llm  (GPT-4o-mini)      │
          │  sql_agent               │
          │  sql_validator           │
          │  ├─ valid ──► cache_write (Redis) ──► execution_engine
          │  ├─ invalid ──────────── retry ──► prompt_builder
          │  └─ unsafe ──────────► error_handler ─► END
          │       │                  │
          │  result_formatter        │
          │  cache_store (LRU)       │
          │  memory_store ─────────► END
          └──────────────────────────┘

          ┌────────────────────────────┐
          │  Refresh Graph (no LLM)    │
          │                            │
          │  refresh_execution_node    │  ← Redis read → MySQL execute
          │  response_node ──────────► END
          └────────────────────────────┘

                    ┌──────────┬──────────┬──────────┐
                    │          │          │          │
               MySQL DB   GPT-4o-mini  Redis    In-Memory
              (vthink_kra)             (SQL      (LRU Cache +
                                        Cache)    Session Store)
```

---

## Pipeline Flows

### Flow 1 — Fresh Query (cache miss, clear intent)

```
load_context → cache_lookup(MISS) → intent_detector(clear)
→ prompt_builder → llm → sql_agent → sql_validator(valid)
→ cache_write(Redis) → execution_engine → result_formatter
→ cache_store(LRU) → memory_store → END
```

### Flow 2 — Incomplete Query (clarification needed)

```
load_context → cache_lookup(MISS) → intent_detector(incomplete)
→ clarification_node → END
  [returns: follow_up_question + follow_up_options + session_id]

POST /report/clarify  (user answers the question)
→ merged_query → intent_detector → ...
  Round 1: may ask another question
  Round 2: FORCED to Track C (clear) → SQL generation proceeds
```

### Flow 3 — Off-Topic Query

```
load_context → cache_lookup(MISS) → intent_detector(off_topic)
→ off_topic_node → END
  [returns: polite message + role-appropriate example queries]
```

### Flow 4 — Repeat Query (LRU cache hit)

```
load_context → cache_lookup(HIT) → memory_store → END
  [returns: cached result instantly, cache_hit=true, no LLM call]
```

### Flow 5 — Refresh (Redis SQL cache)

```
GET /report/refresh/{session_id}
→ refresh_execution_node:
    1. Redis GET sql_cache:{session_id}
    2. Validate user_id matches session owner
    3. MySQL execute(cached_sql)
    4. response_node formats result
  [NO intent_detector, NO prompt_builder, NO LLM, NO sql_validator]
```

### Flow 6 — Validation Retry

```
... → sql_validator(invalid) → increment_retry_validation
→ prompt_builder (with error feedback) → llm → sql_agent → sql_validator
  [repeats up to MAX_RETRY_COUNT=2 times, then routes to error_handler]
```

---

## Project Structure

```
Report Builder Agent/
├── README.md
└── backend/
    ├── main.py                          ← FastAPI app, startup lifespan, CORS
    ├── .env                             ← Secrets (DB, OpenAI, Redis)
    ├── requirements.txt                 ← Python dependencies
    └── app/
        ├── config.py                    ← All settings loaded from .env
        ├── api/
        │   └── routes.py               ← All endpoints + Pydantic models
        ├── graph/
        │   ├── state.py                 ← AgentState TypedDict (pipeline whiteboard)
        │   ├── workflow.py              ← LangGraph graph definitions + entry points
        │   └── nodes.py                 ← All node functions + edge routers
        ├── agents/
        │   ├── intent_agent.py          ← Classifies query intent via LLM
        │   ├── prompt_builder.py        ← Assembles the SQL-generation prompt
        │   ├── llm_agent.py             ← GPT-4o-mini client (lazy init)
        │   └── sql_agent.py             ← Cleans raw LLM SQL output
        ├── validators/
        │   └── sql_validator.py         ← Safety gate + LIMIT enforcer
        ├── db/
        │   ├── connection.py            ← SQLAlchemy connection pool
        │   └── schema_manager.py        ← Reads live DB schema for LLM context
        ├── cache/
        │   ├── query_cache.py           ← In-memory LRU cache with TTL
        │   └── redis_cache.py           ← Redis SQL cache (fallback to memory)
        ├── memory/
        │   └── conversation_memory.py   ← Per-user Q&A history
        ├── services/
        │   ├── report_service.py        ← Thin wrapper for legacy /query endpoint
        │   ├── session_store.py         ← In-memory session store (30-min TTL)
        │   └── filter_recommender.py    ← Picks filterable columns from results
        └── models/
            └── schemas.py               ← (reserved — models defined in routes.py)
```

---

## Prerequisites

- Python 3.10+
- MySQL 8.x — `vthink_kra` database on `192.168.2.8`
- OpenAI API key (GPT-4o-mini access)
- Redis (optional) — enables refresh/stream; falls back to in-memory if unavailable
- Network access to `192.168.2.8:3306`

---

## Installation

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

pip install -r requirements.txt
```

---

## Configuration

All configuration lives in `backend/.env`:

```env
# ── Database ──────────────────────────────────────────
DB_HOST=192.168.2.8
DB_PORT=3306
DB_USER=krauser
DB_PASSWORD=vThink135#
DB_NAME=vthink_kra
DB_POOL_SIZE=10
DB_MAX_OVERFLOW=20
DB_ECHO=false

# ── OpenAI ────────────────────────────────────────────
OPENAI_API_KEY=sk-...
LLM_MODEL=gpt-4o-mini
LLM_MAX_TOKENS=2000
LLM_TEMPERATURE=0

# ── Query Limits ──────────────────────────────────────
MAX_QUERY_TIMEOUT=30
MAX_RESULT_ROWS=1000
DEFAULT_RESULT_LIMIT=100
MAX_RETRY_COUNT=2

# ── Memory ────────────────────────────────────────────
MAX_CONVERSATION_HISTORY=10

# ── Logging ───────────────────────────────────────────
LOG_LEVEL=INFO
DEBUG_MODE=false

# ── Redis (optional) ──────────────────────────────────
# Leave empty to use in-memory fallback (dev mode)
# Use rediss:// (double-s) for TLS (e.g. Upstash)
REDIS_URL=redis://localhost:6379/0

# ── Cache / Stream ────────────────────────────────────
CACHE_TTL_SECONDS=3600
CACHE_MAX_SIZE=500
STREAM_CACHE_TTL_SECONDS=3600
STREAM_REFRESH_INTERVAL_SECONDS=30
```

> The `#` in `vThink135#` is automatically URL-encoded by `config.py` — no manual escaping needed.

---

## Running the Server

```bash
cd backend

# Development (hot-reload on file change)
python main.py

# Production
python -m uvicorn main:app --host 0.0.0.0 --port 8001
```

**Startup log (healthy):**
```
INFO  app.db.connection    Database engine initialised
INFO  main                 KRA Report Builder Agent starting up...
INFO  main                 Database connection OK
INFO  app.db.schema_manager Schema refreshed: 42 tables
INFO  main                 Schema loaded on startup
INFO  main                 Redis connection OK          ← or warning if unavailable
INFO  main                 Startup complete. Swagger UI: http://localhost:8001/docs
```

**Swagger UI:** [http://localhost:8001/docs](http://localhost:8001/docs)

---

## API Reference

### `POST /api/v1/report/generate` — Intent-Aware Report Generation

The primary endpoint. Returns one of three statuses:

**Request:**
```json
{
  "query": "Show my KRA goals for Q1 2025",
  "user_id": "user_42",
  "user_role": "employee"
}
```

`user_role` options: `employee` | `lead` | `manager` | `hr`

**Response — `off_topic`** (query has no KRA context):
```json
{
  "status": "off_topic",
  "message": "I don't have information on greetings. I'm built to generate reports from the KRA system.\n\n• Show my KRA goals for Q1 2025 with completion percentage\n• ...",
  "suggestions": ["Show my KRA goals for Q1 2025...", "..."],
  "session_id": "sess_abc123"
}
```

**Response — `clarification_needed`** (query is KRA-related but missing key info):
```json
{
  "status": "clarification_needed",
  "follow_up_question": "Which time period do you want to see your goals for?",
  "follow_up_options": ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2024", "Full year 2025"],
  "clarification_round": 0,
  "original_prompt": "show my goals",
  "session_id": "sess_abc123"
}
```

**Response — `success`**:
```json
{
  "status": "success",
  "enriched_prompt": "Show my KRA goals for Q1 2025 with completion percentage.",
  "sql_query": "SELECT gm.goal_id, gm.goal_desc, ... FROM master_goals gm ... LIMIT 100",
  "explanation": "This query retrieves KRA goals for Q1 2025 with completion percentage.",
  "dimensions": ["goal_id", "goal_desc"],
  "recommended_column_filters": ["completion_percentage"],
  "data": [{"goal_id": 1, "goal_desc": "...", "completion_percentage": 75.0}],
  "row_count": 12,
  "execution_time": 0.043,
  "cache_hit": false,
  "session_id": "sess_abc123"
}
```

---

### `POST /api/v1/report/clarify` — Submit Clarification Answer

Called when `/report/generate` returns `clarification_needed`. Maximum 2 rounds — round 2 always forces SQL generation.

**Request:**
```json
{
  "session_id": "sess_abc123",
  "user_answer": "Q1 2025",
  "user_id": "user_42",
  "user_role": "employee"
}
```

Returns the same response structure as `/report/generate` (`off_topic` / `clarification_needed` / `success`).

**Error:** `404` if `session_id` is not found or has expired (30-min TTL).

---

### `GET /api/v1/report/refresh/{session_id}` — Live Refresh (No LLM)

Re-executes the cached SQL directly against MySQL. No LLM, no prompt building, no SQL validation.

**Query params:** `user_id` (required), `user_role` (optional, default `employee`)

**Success response:**
```json
{
  "status": "success",
  "explanation": "Live data refresh",
  "dimensions": ["goal_id", "goal_desc"],
  "data": [...],
  "row_count": 12,
  "execution_time": 0.018,
  "refresh_mode": true,
  "refreshed_at": "2026-05-12T06:51:43.883766+00:00",
  "session_id": "sess_abc123"
}
```

**Error responses:**

| HTTP | detail | Cause |
|------|--------|-------|
| `404` | `SESSION_EXPIRED` | session not found / Redis TTL expired |
| `403` | `ACCESS_DENIED` | `user_id` does not match session owner |
| `409` | `SCHEMA_CHANGED` | `OperationalError` — table/column was modified |

---

### `WS /api/v1/report/stream/{session_id}` — WebSocket Auto-Refresh

Connects via WebSocket and pushes a fresh result every N seconds (default: `STREAM_REFRESH_INTERVAL_SECONDS=30`).

**Connect URL:**
```
ws://localhost:8001/api/v1/report/stream/sess_abc123?user_id=user_42&user_role=employee&interval=10
```

- Each message is the same JSON structure as `/report/refresh`
- Server closes with code `1008` (Policy Violation) on `SESSION_EXPIRED`, `ACCESS_DENIED`, or `SCHEMA_CHANGED`
- Client disconnect is handled cleanly — no dangling loops

---

### `POST /api/v1/query` — Legacy Endpoint

Single-step endpoint (no explicit intent detection, uses same LangGraph pipeline). Returns `status`, `session_id`, and all standard result fields. Preserved for backwards compatibility.

---

### `GET /api/v1/cache/stats` — Query Cache Metrics

```json
{
  "size": 3,
  "max_size": 500,
  "ttl_seconds": 3600,
  "hits": 7,
  "misses": 12,
  "hit_rate": 0.3684
}
```

### `DELETE /api/v1/cache` — Flush Query Cache

```json
{ "cleared": 3, "message": "Removed 3 cached entries" }
```

---

## Module Reference

### `main.py` — Entry Point

- Loads `.env` before all imports
- `lifespan` startup: verifies DB, loads schema (42 tables), checks Redis
- CORS: `allow_origins=["*"]` — restrict to frontend origin in production
- Mounts router at `/api/v1`, runs on port **8001**

---

### `app/config.py` — Settings

Centralizes all settings in a single `settings` singleton. Every module imports `from app.config import settings` instead of calling `os.getenv()` directly. The DB password is URL-encoded with `urllib.parse.quote_plus` so special characters (`#`, `@`, etc.) don't break the SQLAlchemy connection URL.

---

### `app/graph/state.py` — Shared Pipeline State

`AgentState` is a `TypedDict(total=False)` — the shared whiteboard passed through all nodes. Every field is optional so partial updates work cleanly.

| Field | Type | Purpose |
|-------|------|---------|
| `user_id`, `user_query`, `user_role` | str | Request inputs |
| `session_id` | str | Links pipeline run to Redis SQL cache |
| `schema`, `memory_context` | str | Loaded by `load_context_node` |
| `intent_track` | str | `off_topic` / `incomplete` / `clear` |
| `clarification_round` | int | 0→1→2; at 2 always forces SQL generation |
| `follow_up_question`, `follow_up_options` | str/list | Returned on `clarification_needed` |
| `enriched_prompt` | str | Intent-rewritten query used instead of raw input |
| `prompt` | str | Full assembled LLM prompt |
| `refined_sql` | str | Cleaned + validated SQL |
| `validation_status` | str | `valid` / `invalid` / `unsafe` |
| `execution_result` | dict | `{rows, columns, error}` |
| `formatted_result` | dict | Final API response payload |
| `retry_count`, `retry_feedback` | int/str | Self-healing retry state |
| `cache_hit`, `cache_key` | bool/str | LRU query cache state |
| `refresh_mode`, `refreshed_at` | bool/str | Set by refresh graph |

---

### `app/graph/workflow.py` — Graph Definitions

Builds and compiles two LangGraph `StateGraph` objects **once at module import** — zero compilation cost per request.

**`_workflow`** — main pipeline (all features)
**`_refresh_workflow`** — refresh only (`refresh_execution → response → END`)

**Three entry points:**

| Function | Used by | Description |
|----------|---------|-------------|
| `run_report_agent()` | `/query` (legacy) | Full pipeline, no explicit user_role |
| `run_intent_report()` | `/report/generate`, `/report/clarify` | Intent-aware, accepts user_role + clarification state |
| `run_refresh_agent()` | `/report/refresh`, `/report/stream` | Refresh-only, no LLM |

---

### `app/graph/nodes.py` — All Pipeline Nodes

Each node has the signature `(state: AgentState) -> Dict[str, Any]`.

| Node | Description |
|------|-------------|
| `load_context_node` | Loads DB schema string + user conversation history |
| `cache_lookup_node` | Checks in-memory LRU cache; on hit, skips entire pipeline |
| `intent_detector_node` | Calls `IntentDetectorAgent.classify()` → sets `intent_track` |
| `off_topic_node` | Builds polite redirect response with role-appropriate examples |
| `clarification_node` | Builds `clarification_needed` response with follow-up question |
| `prompt_builder_node` | Assembles full LLM prompt; uses `enriched_prompt` over raw query |
| `llm_node` | Calls GPT-4o-mini, stores `llm_response` |
| `sql_agent_node` | Strips markdown fences, normalises quotes/whitespace |
| `sql_validator_node` | Safety gate — 19 patterns + LIMIT enforcement |
| `cache_write_node` | Saves validated SQL to Redis (non-blocking; falls back to memory) |
| `execution_engine_node` | Executes SQL on MySQL, stores rows + columns |
| `result_formatter_node` | Classifies dimensions, runs filter recommender, builds final result |
| `cache_store_node` | Saves result to LRU query cache |
| `memory_store_node` | Saves Q&A interaction to `conversation_history` table |
| `error_handler_node` | Maps error patterns to user-friendly suggestions |
| `increment_retry_validation_node` | Increments retry count, sets validation error feedback |
| `increment_retry_execution_node` | Increments retry count, sets execution error feedback |
| `refresh_execution_node` | Redis read → user validation → MySQL execute (no LLM) |
| `response_node` | Formats refresh result, adds `refresh_mode=True`, `refreshed_at` |

---

### `app/agents/intent_agent.py` — Intent Classifier

Classifies every query into one of three tracks using GPT-4o-mini:

| Track | Condition | Action |
|-------|-----------|--------|
| `off_topic` | No KRA/performance context | Returns polite block message + role examples |
| `incomplete` | KRA-related but missing key info | Returns one focused follow-up question + 3-5 options |
| `clear` | Enough info to generate SQL | Returns `enriched_prompt` (precise rewrite) |

**Priority order for missing fields** (asks about the highest priority one):
1. `metric` — what to measure
2. `time_period` — quarter or year
3. `employee_scope` — whose data (never asked for `role=employee`)
4. `status_filter` — all / completed / in-progress / not-started
5. `schema_scope` — which module
6. `comparison_base` — "compare" with one side missing

**Key behaviours:**
- `clarification_round >= 2` → always forces `track=clear` (hard-enforced in `_parse()`, not just the LLM prompt)
- Empty message → returns `off_topic` immediately, no LLM call
- LLM parse failure → falls back to `track=clear` with original query as `enriched_prompt`
- Schema content is brace-escaped before `.format()` to prevent `KeyError` injection

---

### `app/agents/prompt_builder.py` — Prompt Assembly

Builds the full string sent to GPT-4o-mini:

```
[System Prompt]
  - Full DB schema (42 tables, columns, PK/FK markers)
  - SQL rules: SELECT only, LIMIT 100 default, explicit JOINs, exact column names
  - Conversation history (last MAX_CONVERSATION_HISTORY turns)
  - Output format: JSON only { sql_query, explanation, columns, filters }

[Retry Suffix — only on retries]
  - Previous error message
  - Rejected SQL
  - Instruction to fix the specific issue

USER QUERY: <enriched_prompt or raw query>
```

Schema and memory context are brace-escaped (`{` → `{{`) before calling `str.format()` to prevent `KeyError` crashes when column names or values contain curly braces.

---

### `app/agents/llm_agent.py` — GPT-4o-mini Client

Wraps `langchain_openai.ChatOpenAI` with lazy initialization — the OpenAI client is only created on the first actual request, not at import time.

Uses `re.search(r"\{[\s\S]*\}", raw)` to extract the JSON object from the response, tolerating any surrounding text the LLM might add despite instructions.

---

### `app/agents/sql_agent.py` — SQL Cleaner

Normalizes raw LLM output before validation:

| Transformation | Reason |
|----------------|--------|
| Strip ` ```sql ` / ` ``` ` | GPT wraps SQL in markdown fences |
| Replace `'` `'` → `'` | Smart quotes break MySQL syntax |
| Replace `"` `"` → `"` | Smart double-quotes break MySQL syntax |
| Collapse multiple spaces | Normalize whitespace |
| Strip trailing `;` | SQLAlchemy adds its own terminator |

---

### `app/validators/sql_validator.py` — Safety Gate

The critical security component. All patterns compiled once at module import.

**Blocks (UNSAFE — no retry):** `DELETE`, `UPDATE`, `DROP`, `TRUNCATE`, `INSERT`, `ALTER`, `CREATE`, `REPLACE`, `EXEC`, `EXECUTE`, `GRANT`, `REVOKE`, `LOAD DATA`, `OUTFILE`, `INFILE`, `INTO OUTFILE`, `INTO DUMPFILE`, `CALL`, `--` (comments), `;.*SELECT` (stacked queries)

**Requires** statement to start with `SELECT` — anything else → UNSAFE.

**Auto-fixes:** adds `LIMIT 100` if missing; caps any `LIMIT` above `MAX_RESULT_ROWS=1000`.

**Detects cartesian joins** (INVALID — retry allowed): comma in FROM clause at depth 0 with fewer ON clauses than JOINs.

---

### `app/db/connection.py` — MySQL Connection Pool

SQLAlchemy engine with `QueuePool`:

| Setting | Value | Purpose |
|---------|-------|---------|
| `pool_size` | 10 | Persistent connections |
| `max_overflow` | 20 | Burst capacity |
| `pool_pre_ping` | True | Detects stale connections |
| `pool_recycle` | 3600s | Avoids MySQL 8-hour idle timeout |

`execute_query()` sets `MAX_EXECUTION_TIME` per session and returns `(rows: List[dict], columns: List[str])`.

---

### `app/db/schema_manager.py` — Schema Loader

Loads the full DB schema in **2 batch queries** at startup (vs. N×3 per-table queries):

- Query 1: all columns across all tables from `information_schema.COLUMNS`
- Query 2: all foreign keys from `information_schema.KEY_COLUMN_USAGE`

Formats as a text string for the LLM prompt:
```
Table: master_goals
  goal_id (int NOT NULL) [PK]
  goal_desc (text)
  target_date (date)  -- FK→ ...
```

---

### `app/cache/query_cache.py` — In-Memory LRU Cache

TTL-based LRU cache keyed by `md5(user_id:normalized_query)`. Normalized = lowercase + collapsed whitespace, so casing/spacing variations hit the same cache entry.

- Eviction: LRU when `max_size` (default 500) is exceeded
- TTL: lazy expiry on access (entries are removed when read after expiry)
- Tracks `hits`, `misses`, `hit_rate` — exposed via `/cache/stats`

---

### `app/cache/redis_cache.py` — Redis SQL Cache

Stores post-validation, RBAC-secured SQL per session for the refresh/stream features.

**Key:** `sql_cache:{session_id}` **TTL:** `STREAM_CACHE_TTL_SECONDS` (default 1 hour)

**Payload:** `{ session_id, user_id, secured_sql, created_at }`

**Automatic fallback:** When Redis is unreachable, silently falls back to an in-memory dict with the same TTL. This means refresh/stream work in development without a Redis server. When Redis becomes available again, the next successful `store()` call switches back automatically.

```
Redis available    → stores in Redis  (survives restarts, shared across instances)
Redis unavailable  → stores in memory (dev/test mode, lost on restart)
```

---

### `app/services/session_store.py` — Session Store

In-memory store for clarification state between `/report/generate` and `/report/clarify` calls.

**Stores per session:** `original_prompt`, `user_id`, `user_role`, `clarification_round`, `prior_followup`

**TTL:** 30 minutes (lazy expiry on `get()`). Session IDs are `sess_{16 hex chars}`.

---

### `app/memory/conversation_memory.py` — Conversation History

Stores each user's Q&A history so future requests have context for follow-up questions ("show only active ones", "sort by score").

**Two-layer storage:**
1. **MySQL `conversation_history` table** (primary) — created automatically at startup
2. **In-memory `deque(maxlen=100)` per user** (fallback) — used if MySQL write fails

`get_context_string(user_id)` returns the last `MAX_CONVERSATION_HISTORY` turns formatted for injection into the LLM prompt.

---

### `app/services/filter_recommender.py` — Filter Recommender

Analyzes result columns and returns which are suitable for frontend filter dropdowns.

**Always include:** columns with date keywords (`date`, `_at`, `time`, `created`...) · boolean-prefix columns (`is_`, `has_`, `can_`, `flag_`...)

**Include if low-cardinality (≤ 50 unique values):** enum-like columns (e.g. `status`, `designation`)

**Always exclude:** `*_id`, `*_key` columns · text columns (`name`, `email`, `description`, `remark`...) · sensitive columns (`password`, `token`, `secret`, `hash`, `otp`)

Samples first 200 rows on large result sets to keep the cardinality check fast.

---

### `app/api/routes.py` — All Endpoints + Models

Defines all Pydantic request/response models and FastAPI route handlers in one file:

| Model | Used by |
|-------|---------|
| `QueryRequest` / `QueryResponse` | `/query` (legacy) |
| `GenerateRequest` / `ReportResponse` | `/report/generate`, `/report/clarify`, `/report/refresh` |
| `ClarifyRequest` | `/report/clarify` |

`_build_report_response()` maps the three status tracks to the correct `ReportResponse` shape.

---

## Retry Logic

`retry_count` is shared across validation and execution failures. Maximum: `MAX_RETRY_COUNT=2`.

```
Request 1:  GPT generates SQL  →  validator: INVALID (cartesian join)
            retry_count=1, feedback="Cartesian join detected: ..."
            prompt_builder re-runs with error context appended

Request 2:  GPT self-corrects  →  validator: VALID  →  execution: ERROR (unknown column)
            retry_count=2, feedback="Unknown column 'xyz' in field list"
            prompt_builder re-runs again

Request 3:  retry_count(2) >= MAX_RETRY_COUNT(2) → error_handler → return error
```

---

## SQL Safety Rules

| Rule | Violation → |
|------|-------------|
| Must start with `SELECT` | UNSAFE (stop) |
| No DML: `DELETE`, `UPDATE`, `INSERT`, `REPLACE` | UNSAFE (stop) |
| No DDL: `DROP`, `TRUNCATE`, `ALTER`, `CREATE` | UNSAFE (stop) |
| No privilege ops: `GRANT`, `REVOKE` | UNSAFE (stop) |
| No file ops: `LOAD DATA`, `OUTFILE`, `INFILE`, `DUMPFILE` | UNSAFE (stop) |
| No stored procs: `EXEC`, `EXECUTE`, `CALL` | UNSAFE (stop) |
| No SQL comments: `--` | UNSAFE (stop) |
| No stacked queries: `;.*SELECT` | UNSAFE (stop) |
| All JOINs need explicit ON | INVALID (retry) |
| Missing LIMIT | Auto-added: `LIMIT 100` |
| LIMIT > 1000 | Auto-capped: `LIMIT 1000` |

---

## Caching Strategy

The system uses two independent caches:

### 1. Query Cache (in-memory LRU)
- **Key:** `md5(user_id + normalized_query)`
- **What's cached:** Full formatted result (data, SQL, explanation, dimensions, filters)
- **TTL:** `CACHE_TTL_SECONDS` (default 1 hour)
- **Scope:** Same user + same query text → instant response, no LLM, no DB
- **Bypass:** Always bypassed on refresh (refresh reads live DB data)

### 2. Redis SQL Cache
- **Key:** `sql_cache:{session_id}`
- **What's cached:** Post-validation RBAC-secured SQL + session owner `user_id`
- **TTL:** `STREAM_CACHE_TTL_SECONDS` (default 1 hour)
- **Scope:** Per session — used by `/report/refresh` and `/report/stream`
- **Purpose:** Enables re-executing the exact validated SQL with zero LLM cost
- **Fallback:** In-memory dict when Redis is unavailable

---

## Conversation Memory

Memory enables follow-up questions by persisting each user's Q&A history in the `conversation_history` MySQL table (auto-created at startup).

**On every successful query:**
```sql
INSERT INTO conversation_history (user_id, role, content, sql_query) VALUES
  ('user_42', 'user', 'Show all KRA goals for Q1 2025', NULL),
  ('user_42', 'assistant', 'This query retrieves KRA goals... (Returned 12 rows)', 'SELECT ...');
```

**On next request from same user:**
The last 10 turns are loaded and injected into the LLM prompt, so GPT understands references like "sort those by score" or "show only the active ones."

---

## Dependencies

| Package | Purpose |
|---------|---------|
| `langgraph` | StateGraph pipeline orchestration |
| `langchain` | LLM abstractions |
| `langchain-openai` | ChatOpenAI wrapper for GPT-4o-mini |
| `langchain-community` | Community integrations |
| `fastapi` | REST + WebSocket framework |
| `uvicorn[standard]` | ASGI server |
| `sqlalchemy` | ORM + connection pooling for MySQL |
| `pymysql` | Pure-Python MySQL driver |
| `cryptography` | Required by pymysql for SSL |
| `pydantic` | Request/response validation |
| `pydantic-settings` | Settings management |
| `python-dotenv` | Loads `.env` into environment |
| `redis` | Redis client for SQL cache |
| `tenacity` | Retry utilities |
