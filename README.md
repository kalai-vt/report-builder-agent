# KRA AI Report Builder Agent

An AI-powered report generation backend that converts plain-English questions into MySQL queries, executes them against the `vthink_kra` database, and returns structured results with filter recommendations for frontend dashboards.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Pipeline Flow](#pipeline-flow)
- [Project Structure](#project-structure)
- [Prerequisites](#prerequisites)
- [Installation](#installation)
- [Configuration](#configuration)
- [Running the Server](#running-the-server)
- [API Reference](#api-reference)
- [Component Reference](#component-reference)
  - [main.py — Entry Point](#mainpy--entry-point)
  - [app/config.py — Settings](#appconfigpy--settings)
  - [app/graph/state.py — Shared State](#appgraphstatepy--shared-state)
  - [app/graph/workflow.py — Pipeline Definition](#appgraphworkflowpy--pipeline-definition)
  - [app/graph/nodes.py — Pipeline Nodes](#appgraphnodespy--pipeline-nodes)
  - [app/agents/prompt_builder.py — Prompt Assembly](#appagentsprompt_builderpy--prompt-assembly)
  - [app/agents/llm_agent.py — GPT-4o-mini Client](#appagentsllm_agentpy--gpt-4o-mini-client)
  - [app/agents/sql_agent.py — SQL Cleaner](#appagentssql_agentpy--sql-cleaner)
  - [app/validators/sql_validator.py — Safety Gate](#appvalidatorssql_validatorpy--safety-gate)
  - [app/db/connection.py — MySQL Connection Pool](#appdbconnectionpy--mysql-connection-pool)
  - [app/db/schema_manager.py — Schema Loader](#appdbschema_managerpy--schema-loader)
  - [app/memory/conversation_memory.py — Conversation History](#appmemoryconversation_memorypy--conversation-history)
  - [app/services/filter_recommender.py — Filter Recommender](#appservicesfilter_recommenderpy--filter-recommender)
  - [app/services/report_service.py — Service Layer](#appservicesreport_servicepy--service-layer)
  - [app/api/routes.py — API Endpoint](#appapiroutespy--api-endpoint)
  - [app/models/schemas.py — Data Schemas](#appmodelsschemaspython--data-schemas)
- [Retry Logic](#retry-logic)
- [SQL Safety Rules](#sql-safety-rules)
- [Filter Recommendation Logic](#filter-recommendation-logic)
- [Conversation Memory](#conversation-memory)
- [Performance Optimizations](#performance-optimizations)
- [Dependencies](#dependencies)

---

## Overview

The KRA AI Report Builder Agent sits alongside your existing KRA application (port 8000) and exposes a single HTTP endpoint on **port 8001**. A user submits a question in plain English; the agent:

1. Loads the live database schema and conversation history
2. Builds a structured prompt and sends it to **GPT-4o-mini**
3. Cleans and validates the generated SQL (safety check + LIMIT enforcement)
4. Executes the query against MySQL (`vthink_kra` on `192.168.2.8`)
5. Returns structured results with `dimensions` and `recommended_column_filters` for frontend drill-down

If the generated SQL fails validation or execution, the agent automatically retries up to 2 times, feeding the error back to GPT so it can self-correct.

---

## Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        FastAPI (port 8001)                        │
│                      POST /api/v1/query                           │
└───────────────────────────────┬──────────────────────────────────┘
                                │
                                ▼
┌──────────────────────────────────────────────────────────────────┐
│                     LangGraph StateGraph                          │
│                                                                    │
│  load_context → prompt_builder → llm → sql_agent → sql_validator  │
│                      ▲                                    │        │
│                      │          ┌────────────────────────┤        │
│                      │          ▼                         ▼        │
│                 retry_loop   execute                    stop       │
│                      ▲          │                         │        │
│                      │          ▼                         ▼        │
│                      │   execution_engine          error_handler   │
│                      │          │                                  │
│                      │    ┌─────┴──────┐                           │
│                      │    ▼            ▼                           │
│                      └─retry      result_formatter                 │
│                                        │                           │
│                                   memory_store → END               │
└──────────────────────────────────────────────────────────────────┘
                                │
                    ┌───────────┼───────────┐
                    ▼           ▼           ▼
               MySQL DB    GPT-4o-mini   Memory
              (vthink_kra)              (MySQL table +
                                         in-memory deque)
```

---

## Pipeline Flow

A single request passes through up to 10 nodes in sequence:

```
Step 1  load_context           Load DB schema + user conversation history
Step 2  prompt_builder         Assemble full LLM prompt (schema + rules + memory + query)
Step 3  llm                    Call GPT-4o-mini → get SQL + explanation
Step 4  sql_agent              Strip markdown fences, normalize whitespace/quotes
Step 5  sql_validator          Safety check (19 patterns) + enforce LIMIT
         ├─ VALID  ──────────► Step 7
         ├─ INVALID (retry) ─► Step 6a → back to Step 2
         └─ UNSAFE (stop) ──► Step 10

Step 6a increment_retry_validation   Set retry feedback message (validation failure)
Step 6b increment_retry_execution    Set retry feedback message (execution failure)

Step 7  execution_engine       Run validated SQL on MySQL
         ├─ success ──────────► Step 8
         ├─ error (retry) ───► Step 6b → back to Step 2
         └─ error (exhausted) ► Step 10

Step 8  result_formatter       Classify dimensions, call filter recommender
Step 9  memory_store           Save Q&A interaction to conversation_history table
Step 10 error_handler          Build user-friendly error response
```

**Maximum retries:** 2 (shared across validation and execution failures). On each retry, the failed SQL and error message are injected back into the prompt so GPT-4o-mini can self-correct.

---

## Project Structure

```
backend/
├── main.py                              ← FastAPI app + startup lifespan
├── .env                                 ← Secrets (DB password, OpenAI key)
├── .gitignore                           ← Prevents .env from being committed
├── requirements.txt                     ← Python dependencies
└── app/
    ├── config.py                        ← All settings loaded from .env
    ├── graph/
    │   ├── state.py                     ← AgentState TypedDict (shared pipeline data)
    │   ├── workflow.py                  ← LangGraph graph definition + compiler
    │   └── nodes.py                     ← All 10 node functions + edge routers
    ├── agents/
    │   ├── prompt_builder.py            ← Assembles the LLM prompt string
    │   ├── llm_agent.py                 ← Calls GPT-4o-mini, parses JSON response
    │   └── sql_agent.py                 ← Cleans raw LLM SQL output
    ├── validators/
    │   └── sql_validator.py             ← SQL safety checker + LIMIT enforcer
    ├── db/
    │   ├── connection.py                ← SQLAlchemy connection pool + execute_query()
    │   └── schema_manager.py            ← Reads live DB schema for LLM context
    ├── memory/
    │   └── conversation_memory.py       ← Per-user Q&A history (MySQL + fallback deque)
    ├── services/
    │   ├── report_service.py            ← Thin wrapper calling run_report_agent()
    │   └── filter_recommender.py        ← Picks filterable column names from result
    ├── api/
    │   └── routes.py                    ← FastAPI router with POST /query endpoint
    └── models/
        └── schemas.py                   ← Pydantic request/response models
```

---

## Prerequisites

- Python 3.10+
- MySQL 8.x (`vthink_kra` database on `192.168.2.8`)
- OpenAI API key (GPT-4o-mini access)
- Network access to `192.168.2.8:3306`

---

## Installation

```bash
cd backend

# Create and activate virtual environment
python -m venv .venv
.venv\Scripts\activate          # Windows
# source .venv/bin/activate     # Linux/macOS

# Install dependencies
pip install -r requirements.txt
```

---

## Configuration

Create a `.env` file in the `backend/` directory:

```env
# Database
DB_HOST=192.168.2.8
DB_PORT=3306
DB_USER=krauser
DB_PASSWORD=vThink135#
DB_NAME=vthink_kra

# OpenAI
OPENAI_API_KEY=sk-...

# Optional tuning (these are the defaults)
LLM_MODEL=gpt-4o-mini
LLM_MAX_TOKENS=2000
LLM_TEMPERATURE=0
MAX_RESULT_ROWS=1000
DEFAULT_RESULT_LIMIT=100
MAX_RETRY_COUNT=2
MAX_CONVERSATION_HISTORY=10
MAX_QUERY_TIMEOUT=30
LOG_LEVEL=INFO
```

All settings have sensible defaults. Only `OPENAI_API_KEY` is required (without it, LLM calls will fail).

> **Note:** The `#` character in `vThink135#` is handled automatically — `config.py` URL-encodes it with `quote_plus` so it does not break the SQLAlchemy connection string.

---

## Running the Server

```bash
cd backend

# Development (with auto-reload)
python main.py

# Production
python -m uvicorn main:app --host 0.0.0.0 --port 8001
```

On startup the server will:
1. Verify the MySQL connection
2. Load the full `vthink_kra` schema into memory (2 batch queries)
3. Warn if `OPENAI_API_KEY` is missing
4. Print the Swagger UI URL: `http://localhost:8001/docs`

---

## API Reference

### `POST /api/v1/query`

Convert a plain-English question to SQL, execute it, and return results.

**Request:**
```json
{
  "query": "Show all active KRAs with employee names and current progress"
}
```

**Response:**
```json
{
  "sql_query": "SELECT e.name, k.kra_title, k.progress FROM employees e JOIN kras k ON e.id = k.employee_id WHERE e.is_active = 1 LIMIT 100",
  "explanation": "This query fetches active employees along with their KRA titles and current progress.",
  "dimensions": ["name", "kra_title"],
  "recommended_column_filters": ["designation", "stream", "is_active"],
  "data": [
    { "name": "John Doe", "kra_title": "Q1 Revenue Target", "progress": 75 },
    ...
  ],
  "row_count": 45,
  "execution_time": 0.043,
  "error": null
}
```

**Error response (when SQL generation or execution fails):**
```json
{
  "sql_query": "",
  "explanation": "",
  "dimensions": [],
  "recommended_column_filters": [],
  "data": [],
  "row_count": 0,
  "execution_time": 0.0,
  "error": "SQL execution failed: Table 'xyz' doesn't exist — The referenced table may not exist. Try refreshing the schema via POST /api/v1/refresh-schema."
}
```

**Response fields:**

| Field | Type | Description |
|---|---|---|
| `sql_query` | string | The MySQL SELECT statement that was executed |
| `explanation` | string | One-sentence description of what the query returns |
| `dimensions` | List[str] | Non-aggregate columns (suitable for grouping/row display) |
| `recommended_column_filters` | List[str] | Columns suitable for frontend filter dropdowns |
| `data` | List[object] | Result rows as JSON objects keyed by column name |
| `row_count` | int | Number of rows returned |
| `execution_time` | float | MySQL query execution time in seconds |
| `error` | string or null | Error message if the query failed; null on success |

**Example queries to try in Swagger:**
- `Show all active KRAs with employee names and current progress`
- `List employees grouped by designation`
- `How many employees are there per stream?`
- `Show employees who joined in 2024`
- `Show all goals and their completion status`

**Swagger UI:** `http://localhost:8001/docs`

---

## Component Reference

### [main.py](backend/main.py) — Entry Point

The FastAPI application entry point. Responsibilities:

- Loads `.env` before any other imports (so `config.py` sees the environment variables)
- Configures structured logging (`%(asctime)s %(levelname)s %(name)s %(message)s`)
- Defines the `lifespan` async context manager that runs on startup:
  - Calls `db_manager.health_check()` to verify the DB is reachable
  - Calls `schema_manager.refresh_schema()` to load all table/column metadata into memory
  - Warns if `OPENAI_API_KEY` is not set
- Registers CORS middleware (allows all origins — restrict in production)
- Mounts the API router at `/api/v1`
- Runs uvicorn on port **8001** (your existing KRA app occupies 8000)

---

### [app/config.py](backend/app/config.py) — Settings

Centralizes all configuration in a single `settings` object. Every other module imports from here instead of calling `os.getenv()` directly.

The DB password `vThink135#` contains a `#` character which would break a URL string. `_build_db_url()` uses `urllib.parse.quote_plus` to encode it as `%23` before inserting it into the SQLAlchemy connection URL.

| Setting | Default | Description |
|---|---|---|
| `DB_HOST` | `192.168.2.8` | MySQL server IP |
| `DB_PORT` | `3306` | MySQL port |
| `DB_USER` | `krauser` | DB username |
| `DB_PASSWORD` | `vThink135#` | DB password (URL-encoded internally) |
| `DB_NAME` | `vthink_kra` | Database name |
| `DB_POOL_SIZE` | `10` | Persistent connection pool size |
| `DB_MAX_OVERFLOW` | `20` | Extra connections allowed under load |
| `OPENAI_API_KEY` | — | Required for LLM calls |
| `LLM_MODEL` | `gpt-4o-mini` | OpenAI model to use |
| `LLM_MAX_TOKENS` | `2000` | Max tokens in LLM response |
| `LLM_TEMPERATURE` | `0` | 0 = deterministic SQL output |
| `MAX_RESULT_ROWS` | `1000` | Hard cap on rows returned |
| `DEFAULT_RESULT_LIMIT` | `100` | LIMIT added if query has none |
| `MAX_RETRY_COUNT` | `2` | Max retries on SQL failure |
| `MAX_CONVERSATION_HISTORY` | `10` | Past interactions loaded into prompt |
| `MAX_QUERY_TIMEOUT` | `30` | MySQL `MAX_EXECUTION_TIME` in seconds |
| `LOG_LEVEL` | `INFO` | Logging verbosity |

---

### [app/graph/state.py](backend/app/graph/state.py) — Shared State

`AgentState` is a `TypedDict` (with `total=False`) that acts as the shared whiteboard passed through all pipeline nodes. Each node reads what it needs and writes what it produces.

| Field | Type | Written by | Read by |
|---|---|---|---|
| `user_id` | str | routes.py | all nodes (for logging) |
| `user_query` | str | routes.py | prompt_builder |
| `schema` | str | load_context | prompt_builder |
| `memory_context` | str | load_context | prompt_builder |
| `prompt` | str | prompt_builder | llm |
| `llm_response` | dict | llm | sql_agent, result_formatter |
| `refined_sql` | str | sql_agent, sql_validator | execution_engine, result_formatter |
| `validation_status` | str | sql_validator | route_after_validation |
| `validation_message` | str | sql_validator | increment_retry_validation, error_handler |
| `execution_result` | dict | execution_engine | result_formatter, route_after_execution |
| `execution_time` | float | execution_engine | result_formatter |
| `formatted_result` | dict | result_formatter, error_handler | routes.py (final response) |
| `retry_count` | int | increment_retry nodes | routers (stop if exhausted) |
| `retry_feedback` | str | increment_retry nodes | prompt_builder |
| `steps` | list | every node | routes.py (debug mode only) |

---

### [app/graph/workflow.py](backend/app/graph/workflow.py) — Pipeline Definition

Builds and compiles the LangGraph `StateGraph` once at module import. The compiled graph (`_workflow`) is reused for every request — zero compilation overhead per call.

**Graph topology:**

```python
load_context → prompt_builder → llm → sql_agent → sql_validator
                    ▲                                    │
                    │         ┌─────────────────────────┤
               (retry)        ▼                 ▼        ▼
                    │    increment_retry    execute     stop
                    │         │                │
                    └─────────┘         execution_engine
                                               │
                               ┌───────────────┤
                               ▼       ▼        ▼
                          increment  format    stop
                          _retry         │
                               │   result_formatter
                               │         │
                               │    memory_store → END
                               │
                           error_handler → END
```

`run_report_agent()` is the async entry point called by `report_service`. It initializes the full `AgentState` with default values and calls `_workflow.ainvoke(initial)`.

---

### [app/graph/nodes.py](backend/app/graph/nodes.py) — Pipeline Nodes

Contains all 10 node functions. Each function signature is `(state: AgentState) -> Dict[str, Any]` — it reads from state and returns a dict of keys to update.

#### Node 1 — `load_context_node`
Loads two things into state:
- **Schema string** from `schema_manager.get_schema_string()` — formatted table/column descriptions for the LLM prompt
- **Memory context** from `memory_manager.get_context_string(user_id)` — last N conversation turns

#### Node 2 — `prompt_builder_node`
Calls `prompt_builder.build_prompt()` to assemble the complete LLM input string. On a retry, `state["retry_feedback"]` is non-empty and gets appended as a suffix to the prompt, telling GPT what went wrong.

#### Node 3 — `llm_node`
Calls `llm_agent.generate_sql(prompt)`. Returns a dict with `sql_query`, `explanation`, `columns`, `filters`. On failure, sets `error` key.

#### Node 4 — `sql_agent_node`
Passes the raw `llm_response["sql_query"]` through `sql_refinement_agent.refine()` to produce a clean SQL string.

#### Node 5 — `sql_validator_node`
Calls `sql_validator.validate(sql, user_id)`. Writes `validation_status` (`valid` / `invalid` / `unsafe`) and `validation_message` to state. Also writes the possibly-modified SQL (with LIMIT enforced).

#### Nodes 6a/6b — `increment_retry_validation_node` / `increment_retry_execution_node`
Increment `retry_count` by 1. Build a `retry_feedback` string describing what failed. This feedback is read by Node 2 on the next loop to give GPT the context it needs to generate better SQL.

#### Node 7 — `execution_engine_node`
Calls `db_manager.execute_query(sql)`. On success, writes `execution_result` with `rows` (List[dict]) and `columns` (List[str]). On failure, writes the exception message to `execution_result["error"]`.

#### Node 8 — `result_formatter_node`
Builds the final `formatted_result` dict:
- **`dimensions`**: columns that do not contain metric keywords (`count`, `sum`, `total`, `avg`, `revenue`, etc.) — uses a `frozenset` for O(1) membership checks
- **`recommended_column_filters`**: calls `filter_recommender.recommended_column_filters(rows, columns)`
- Adds `sql_query`, `explanation`, `data`, `row_count`, `execution_time`

#### Node 9 — `memory_store_node`
Saves the Q&A pair to `conversation_history` so future requests from the same user benefit from context.

#### Node 10 — `error_handler_node`
Builds an error-shaped `formatted_result`. Calls `_suggest(error_message)` to map error patterns to user-friendly guidance:

| Error contains | Suggestion |
|---|---|
| `unsafe`, `forbidden` | Only SELECT queries are allowed |
| `user_id`, `rbac` | Access control prevented this query |
| `cartesian` | Add explicit JOIN conditions |
| `table` + `doesn't exist` | Refresh schema via POST /api/v1/refresh-schema |
| `timeout` | Add more specific filters |
| anything else | Please rephrase your query |

#### Edge Routers

`route_after_validation(state)` — decides what happens after sql_validator:
- `"execute"` if `validation_status == "valid"`
- `"stop"` if `validation_status == "unsafe"` (or retry budget exhausted)
- `"retry"` if `validation_status == "invalid"` and retries remain

`route_after_execution(state)` — decides what happens after execution_engine:
- `"format"` if no execution error
- `"stop"` if error and retry budget exhausted
- `"retry"` if error and retries remain

---

### [app/agents/prompt_builder.py](backend/app/agents/prompt_builder.py) — Prompt Assembly

Assembles the complete prompt sent to GPT-4o-mini. The prompt has two parts:

**System prompt template** includes:
- Full DB schema (table names, column names, types, PK/FK markers)
- Business rules:
  - Only generate SELECT statements
  - Default LIMIT 100, maximum LIMIT 1000
  - Use explicit JOIN ... ON ... syntax (no comma-separated FROM tables)
  - Column names must exactly match the schema
  - No subqueries in FROM clause without aliases
- Output format instruction: respond with JSON only — `{ "sql_query": "...", "explanation": "...", "columns": [...], "filters": [...] }`
- Memory context (last N Q&A turns from this user)

**Retry suffix template** (appended on retries):
- States the validation or execution error message
- Includes the rejected SQL
- Instructs GPT to fix the specific issue

---

### [app/agents/llm_agent.py](backend/app/agents/llm_agent.py) — GPT-4o-mini Client

Wraps `langchain_openai.ChatOpenAI` with lazy initialization.

**Lazy init:** `_llm` starts as `None`. On the first `generate_sql()` call, `_get_llm()` creates the `ChatOpenAI` instance. This prevents an `OpenAIError: api_key must be set` crash at import time (before `.env` is loaded).

**JSON extraction:** The LLM is instructed to respond with JSON only, but sometimes includes surrounding text. The agent uses `re.search(r"\{[\s\S]*\}", raw)` to extract the JSON object regardless of surrounding content.

**Returns a dict with:**
- `sql_query` — the raw SQL string
- `explanation` — one-sentence description
- `columns` — list of column names in the result
- `filters` — list of suggested filter columns (used for reference; `filter_recommender` makes the final decision)
- `error` — set on any exception

---

### [app/agents/sql_agent.py](backend/app/agents/sql_agent.py) — SQL Cleaner

Lightweight string normalizer applied before validation:

| Transformation | Reason |
|---|---|
| Strip ` ```sql ` and ` ``` ` | GPT often wraps SQL in markdown code fences |
| Replace `'` `'` → `'` | Smart/curly quotes break MySQL syntax |
| Replace `"` `"` → `"` | Smart double-quotes break MySQL syntax |
| Collapse multiple spaces | Normalize whitespace |
| Strip trailing `;` | SQLAlchemy adds its own statement terminator |

All patterns use a single pre-compiled `_MULTI_SPACE` regex for repeated spaces. The markdown patterns use `re.sub()` with `re.IGNORECASE`.

---

### [app/validators/sql_validator.py](backend/app/validators/sql_validator.py) — Safety Gate

The most critical security component. Ensures the agent can never modify or destroy data.

**19 compiled unsafe patterns** (compiled once at module import):

```
DELETE, UPDATE, DROP, TRUNCATE, INSERT, ALTER, CREATE, REPLACE,
EXEC, EXECUTE, GRANT, REVOKE, LOAD DATA, OUTFILE, INFILE,
INTO OUTFILE, INTO DUMPFILE, CALL, --, ;.*SELECT
```

**Validation steps:**
1. Clean the SQL (same normalization as sql_agent)
2. Check all 19 unsafe patterns — any match → `UNSAFE` (no retry)
3. Verify the statement starts with `SELECT` — anything else → `UNSAFE`
4. Enforce LIMIT (add `LIMIT 100` if missing; cap any existing LIMIT at 1000)
5. Check for Cartesian joins (comma-separated tables in FROM without matching ON clauses) → `INVALID` (retry allowed)

**Returns:** `Tuple[ValidationStatus, message, modified_sql]`

**Status meanings:**
- `VALID` → proceed to execution
- `INVALID` → recoverable; retry with feedback
- `UNSAFE` → unrecoverable; stop immediately, return error

---

### [app/db/connection.py](backend/app/db/connection.py) — MySQL Connection Pool

Creates a SQLAlchemy engine with **QueuePool** configuration:

| Parameter | Value | Description |
|---|---|---|
| `pool_size` | 10 | Connections kept alive permanently |
| `max_overflow` | 20 | Extra connections allowed under peak load |
| `pool_pre_ping` | True | Tests connections before use (prevents stale connection errors) |
| `pool_recycle` | 3600 | Recycles connections every hour (avoids MySQL's 8-hour idle timeout) |

`execute_query(sql)`:
1. Acquires a connection from the pool
2. Sets `SET SESSION MAX_EXECUTION_TIME = {timeout_ms}` (warns on failure rather than aborting)
3. Executes the SQL
4. Returns `(rows: List[dict], columns: List[str])` — rows are dicts keyed by column name

---

### [app/db/schema_manager.py](backend/app/db/schema_manager.py) — Schema Loader

Reads the live database schema and formats it as a text string injected into the LLM prompt.

**Key optimization — 2 batch queries instead of N+1:**

Without optimization: 3 queries per table × 42 tables = **126 queries** at startup.

With optimization: **2 queries total**, regardless of table count:

```sql
-- Query 1: all columns for all tables
SELECT TABLE_NAME, COLUMN_NAME, DATA_TYPE, IS_NULLABLE, COLUMN_KEY
FROM information_schema.COLUMNS
WHERE TABLE_SCHEMA = 'vthink_kra'
ORDER BY TABLE_NAME, ORDINAL_POSITION;

-- Query 2: all foreign keys for all tables
SELECT TABLE_NAME, COLUMN_NAME, REFERENCED_TABLE_NAME, REFERENCED_COLUMN_NAME
FROM information_schema.KEY_COLUMN_USAGE
WHERE TABLE_SCHEMA = 'vthink_kra'
  AND REFERENCED_TABLE_NAME IS NOT NULL;
```

**Schema string format** (what GPT sees in the prompt):
```
Table: employees
  - employee_id (INT) [PK]
  - name (VARCHAR) [NOT NULL]
  - designation_id (INT) [FK → designations.id]
  - stream_id (INT) [FK → streams.id]
  - is_active (TINYINT)
```

`refresh_schema()` is called at startup and can be called again to pick up schema changes without restarting.

---

### [app/memory/conversation_memory.py](backend/app/memory/conversation_memory.py) — Conversation History

Enables follow-up questions by persisting each user's Q&A history.

**Two-layer storage:**

1. **MySQL `conversation_history` table** (primary):
   - Created automatically at startup if it doesn't exist
   - Schema: `id, user_id, role, content, sql_query, created_at`
   - Index on `(user_id, created_at)` for fast per-user lookups
   - Stores `role='user'` and `role='assistant'` messages per interaction

2. **In-memory `deque(maxlen=100)` per user** (fallback):
   - Used if the DB write fails
   - Bounded at 100 entries per user — prevents unbounded memory growth
   - Lost on server restart (acceptable — MySQL is the source of truth)

`get_context_string(user_id)` returns the last `MAX_CONVERSATION_HISTORY` (default 10) turns formatted as:

```
Previous conversations:
User: Show all employees by designation
Assistant: This query returns employees grouped by designation. (Returned 87 rows)
SQL: SELECT designation, COUNT(*) FROM employees GROUP BY designation LIMIT 100
```

This string is injected into the prompt so GPT understands the conversation context.

---

### [app/services/filter_recommender.py](backend/app/services/filter_recommender.py) — Filter Recommender

Analyzes the SQL result and returns a list of column names suitable for frontend filter dropdowns.

**Inclusion logic (any of these passes):**
- Columns containing date-related keywords: `date`, `time`, `year`, `month`, `day`, `created`, `updated`, `joined`
- Boolean-style columns starting with `is_` or `has_`
- Categorical columns with ≤ 50 unique values in the result (low cardinality)

**Exclusion logic (these are always skipped):**
- Columns ending in `_id` (raw foreign key IDs — not useful for filtering in UI)
- Columns in `_SKIP_COLUMNS`: `password`, `token`, `hash`, `secret`, `salt`, `key`, `otp`
- Columns containing `_TEXT_HINTS`: `name`, `email`, `phone`, `address`, `description`, `remarks`, `notes`, `comment`

**Performance:** For large result sets, only the first 200 rows are sampled for the cardinality check. This avoids scanning thousands of rows on every request.

**Example:** For a result containing `employee_id, name, email, designation, stream, is_active, join_date`, the output would be: `["designation", "stream", "is_active", "join_date"]`

---

### [app/services/report_service.py](backend/app/services/report_service.py) — Service Layer

A thin wrapper that decouples the API layer from the LangGraph layer. The single `generate()` async method calls `run_report_agent()` from `workflow.py`.

This separation means the API routes don't import LangGraph directly, making the service layer replaceable (e.g., swap LangGraph for a different orchestrator without touching `routes.py`).

---

### [app/api/routes.py](backend/app/api/routes.py) — API Endpoint

Defines the single `POST /query` endpoint using FastAPI's `APIRouter`.

**Request validation:** Pydantic automatically validates that `query` is a non-empty string. Invalid requests return `422 Unprocessable Entity` before reaching any agent logic.

**User ID:** Hardcoded as `"demo_user"` for all requests. This is used as the key for conversation history. To support multi-user scenarios, replace this with the authenticated user's ID from a JWT token or session.

**Error handling:** Any unhandled exception from the pipeline returns `HTTP 500` with the exception message in `detail`.

---

### [app/models/schemas.py](backend/app/models/schemas.py) — Data Schemas

Pydantic models for request/response serialization:

**`QueryRequest`** — validates incoming JSON:
```python
query: str   # required, non-empty string
```

**`QueryResponse`** — serializes the pipeline result:
```python
sql_query:                  Optional[str]
explanation:                Optional[str]
dimensions:                 List[str]         # default []
recommended_column_filters: List[str]         # default []
data:                       List[Dict]        # default []
row_count:                  int               # default 0
execution_time:             float             # default 0.0
error:                      Optional[str]     # null on success
```

All fields have defaults so a partial result (e.g., error case) is always valid JSON.

---

## Retry Logic

The agent shares a single `retry_count` across both validation and execution failures. The maximum is controlled by `MAX_RETRY_COUNT` (default: 2).

**Retry flow:**
```
Attempt 1: GPT generates SQL → validation fails
  → retry_count = 1, retry_feedback = "SQL validation failed: Cartesian join detected..."
  → prompt_builder re-runs with error context
Attempt 2: GPT self-corrects → validation passes → execution fails
  → retry_count = 2, retry_feedback = "SQL execution failed: Table 'xyz' doesn't exist..."
  → prompt_builder re-runs with error context
Attempt 3: retry_count (2) >= MAX_RETRY_COUNT (2) → route to error_handler, stop
```

When retries are exhausted, the error handler returns a user-friendly message with a specific suggestion based on the error type.

---

## SQL Safety Rules

The validator enforces these rules on every request, regardless of what GPT generates:

| Rule | Action on violation |
|---|---|
| Query must start with `SELECT` | UNSAFE — stop, no retry |
| No `DELETE`, `UPDATE`, `DROP`, `TRUNCATE` | UNSAFE — stop, no retry |
| No `INSERT`, `ALTER`, `CREATE`, `REPLACE` | UNSAFE — stop, no retry |
| No `EXEC`, `EXECUTE`, `GRANT`, `REVOKE` | UNSAFE — stop, no retry |
| No `LOAD DATA`, `OUTFILE`, `INFILE` | UNSAFE — stop, no retry |
| No `INTO OUTFILE`, `INTO DUMPFILE` | UNSAFE — stop, no retry |
| No `CALL` (stored procedures) | UNSAFE — stop, no retry |
| No SQL comments (`--`) | UNSAFE — stop, no retry |
| No stacked queries (`;.*SELECT`) | UNSAFE — stop, no retry |
| All JOINs must have explicit ON conditions | INVALID — retry with feedback |
| Query must have a LIMIT | Auto-added (`LIMIT 100`) |
| LIMIT must not exceed 1000 | Auto-capped at 1000 |

---

## Filter Recommendation Logic

The `recommended_column_filters` field tells the frontend which columns are worth offering as filter controls.

**Decision tree for each column in the result:**

```
Is column name in _SKIP_COLUMNS (password, token, hash, ...)?
  → NO
Does column name contain _TEXT_HINTS (name, email, description, ...)?
  → NO
Does column name end with _id?
  → NO
Does column name contain a date keyword (date, time, created, ...)?
  → YES (date range filter)
Does column name start with is_ or has_?
  → YES (boolean toggle filter)
Does the column have ≤ 50 unique values in the result data?
  → YES (dropdown filter)
Otherwise:
  → EXCLUDE (high-cardinality — not useful as a filter)
```

---

## Conversation Memory

Memory enables follow-up questions. Without it, each request is independent and GPT has no context for queries like "sort those by salary" or "show me only the active ones."

**How it works:**
1. After a successful query, `memory_store_node` writes two rows to `conversation_history`:
   - `role='user'`, `content='Show employees by stream'`
   - `role='assistant'`, `content='This query groups employees by stream. (Returned 12 rows)'`, `sql_query='SELECT...'`
2. On the next request, `load_context_node` reads the last `MAX_CONVERSATION_HISTORY` rows for this user
3. The formatted history is injected into the prompt so GPT can resolve references to previous results

**Fallback:** If MySQL is unavailable when writing, the history is stored in an in-memory `deque(maxlen=100)` so the current session still has context.

---

## Performance Optimizations

| Optimization | Location | Impact |
|---|---|---|
| Schema batch loading (2 queries vs N+1) | `schema_manager.py` | 126 → 2 queries at startup |
| LangGraph compiled once at import | `workflow.py` | Zero graph compilation cost per request |
| Lazy LLM initialization | `llm_agent.py` | No OpenAI client created until first request |
| Pre-compiled regex patterns (19 safety + 5 utility) | `sql_validator.py` | No regex recompilation per request |
| `frozenset` for metric keyword lookup | `nodes.py` | O(1) vs O(n) per column |
| Connection pool (10 + 20 overflow) | `connection.py` | No connection overhead per request |
| Row sampling (first 200 rows) for cardinality | `filter_recommender.py` | Avoids scanning full result on every request |
| Bounded `deque(maxlen=100)` per user | `conversation_memory.py` | Prevents memory growth on memory fallback |

---

## Dependencies

| Package | Purpose |
|---|---|
| `langgraph` | StateGraph orchestration (the pipeline engine) |
| `langchain` | LLM abstractions and chain utilities |
| `langchain-openai` | `ChatOpenAI` wrapper for GPT-4o-mini |
| `langchain-community` | Community integrations (tools, loaders) |
| `fastapi` | Web framework for the REST API |
| `uvicorn[standard]` | ASGI server to run FastAPI |
| `sqlalchemy` | ORM and connection pooling for MySQL |
| `pymysql` | Pure-Python MySQL driver (used by SQLAlchemy) |
| `cryptography` | Required by pymysql for SSL support |
| `pydantic` | Request/response validation and serialization |
| `pydantic-settings` | Settings management from environment variables |
| `python-dotenv` | Loads `.env` file into environment |
| `redis` | Optional: for distributed session/cache (not active by default) |
| `tenacity` | Retry utilities (available for resilience patterns) |
