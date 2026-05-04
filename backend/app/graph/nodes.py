import logging
import time
from typing import Any, Dict

from app.agents.llm_agent import llm_agent
from app.agents.prompt_builder import prompt_builder
from app.agents.sql_agent import sql_refinement_agent
from app.config import settings
from app.db.connection import db_manager
from app.db.schema_manager import schema_manager
from app.graph.state import AgentState
from app.memory.conversation_memory import memory_manager
from app.services.filter_recommender import filter_recommender
from app.validators.sql_validator import ValidationStatus, sql_validator

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────
# Helper
# ─────────────────────────────────────────────

def _steps(state: AgentState) -> list:
    return list(state.get("steps", []))


# ─────────────────────────────────────────────
# Node 1 — Load User Context
# ─────────────────────────────────────────────

def load_context_node(state: AgentState) -> Dict[str, Any]:
    user_id = state["user_id"]
    t0 = time.time()

    schema_str = schema_manager.get_schema_string()
    memory_ctx = memory_manager.get_context_string(user_id)

    step = {
        "node": "load_context",
        "status": "success",
        "schema_tables": len(schema_manager.get_schema()),
        "has_memory": bool(memory_ctx),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[load_context] user={user_id} tables={step['schema_tables']}")
    return {
        "schema": schema_str,
        "memory_context": memory_ctx,
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node 2 — Prompt Builder
# ─────────────────────────────────────────────

def prompt_builder_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    built_prompt = prompt_builder.build_prompt(
        user_query=state["user_query"],
        user_id=state["user_id"],
        schema_string=state.get("schema", ""),
        memory_context=state.get("memory_context", ""),
        retry_feedback=state.get("retry_feedback", ""),
    )
    step = {
        "node": "prompt_builder",
        "status": "success",
        "prompt_chars": len(built_prompt),
        "is_retry": bool(state.get("retry_feedback")),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[prompt_builder] user={state['user_id']} chars={step['prompt_chars']} retry={step['is_retry']}")
    return {"prompt": built_prompt, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 3 — LLM
# ─────────────────────────────────────────────

def llm_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    response = llm_agent.generate_sql(state["prompt"])
    duration = round((time.time() - t0) * 1000, 1)

    has_error = "error" in response and response["error"]
    step = {
        "node": "llm",
        "status": "error" if has_error else "success",
        "sql_generated": bool(response.get("sql_query")),
        "duration_ms": duration,
    }
    if has_error:
        step["error"] = response["error"]

    logger.info(f"[llm] user={state['user_id']} sql_ok={step['sql_generated']} ms={duration}")
    return {"llm_response": response, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 4 — SQL Agent (Refine)
# ─────────────────────────────────────────────

def sql_agent_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    raw_sql = state.get("llm_response", {}).get("sql_query", "")
    refined = sql_refinement_agent.refine(raw_sql)

    step = {
        "node": "sql_agent",
        "status": "success",
        "raw_length": len(raw_sql),
        "refined_length": len(refined),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[sql_agent] user={state['user_id']} refined={refined[:100]}")
    return {"refined_sql": refined, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 5 — SQL Validator
# ─────────────────────────────────────────────

def sql_validator_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    sql = state.get("refined_sql", "")
    user_id = state["user_id"]

    status, message, validated_sql = sql_validator.validate(sql, user_id)

    step = {
        "node": "sql_validator",
        "status": status.value,
        "message": message,
        "sql_snippet": validated_sql[:120],
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[sql_validator] user={user_id} status={status.value} msg={message}")
    return {
        "refined_sql": validated_sql,
        "validation_status": status.value,
        "validation_message": message,
        "steps": _steps(state) + [step],
    }


# ─────────────────────────────────────────────
# Node 6a — Increment Retry (Validation failure)
# ─────────────────────────────────────────────

def increment_retry_validation_node(state: AgentState) -> Dict[str, Any]:
    new_count = state.get("retry_count", 0) + 1
    feedback = (
        f"SQL validation failed: {state.get('validation_message', '')}\n"
        f"Rejected SQL was:\n{state.get('refined_sql', '')}"
    )
    logger.warning(f"[retry_validation] user={state['user_id']} attempt={new_count} reason={state.get('validation_message')}")
    return {"retry_count": new_count, "retry_feedback": feedback}


# ─────────────────────────────────────────────
# Node 6b — Increment Retry (Execution failure)
# ─────────────────────────────────────────────

def increment_retry_execution_node(state: AgentState) -> Dict[str, Any]:
    new_count = state.get("retry_count", 0) + 1
    exec_error = state.get("execution_result", {}).get("error", "Unknown error")
    feedback = (
        f"SQL execution failed with: {exec_error}\n"
        f"Failed SQL was:\n{state.get('refined_sql', '')}"
    )
    logger.warning(f"[retry_execution] user={state['user_id']} attempt={new_count} error={exec_error}")
    return {"retry_count": new_count, "retry_feedback": feedback}


# ─────────────────────────────────────────────
# Node 7 — Execution Engine
# ─────────────────────────────────────────────

def execution_engine_node(state: AgentState) -> Dict[str, Any]:
    sql = state.get("refined_sql", "")
    t0 = time.time()
    try:
        rows, columns = db_manager.execute_query(sql)
        duration = round(time.time() - t0, 4)
        step = {
            "node": "execution_engine",
            "status": "success",
            "row_count": len(rows),
            "duration_ms": round(duration * 1000, 1),
        }
        logger.info(f"[execution_engine] user={state['user_id']} rows={len(rows)} ms={step['duration_ms']}")
        return {
            "execution_result": {"rows": rows, "columns": columns, "error": None},
            "execution_time": duration,
            "steps": _steps(state) + [step],
        }
    except Exception as e:
        duration = round(time.time() - t0, 4)
        logger.error(f"[execution_engine] user={state['user_id']} ERROR: {e}")
        step = {
            "node": "execution_engine",
            "status": "error",
            "error": str(e),
            "duration_ms": round(duration * 1000, 1),
        }
        return {
            "execution_result": {"rows": [], "columns": [], "error": str(e)},
            "execution_time": duration,
            "steps": _steps(state) + [step],
        }


# ─────────────────────────────────────────────
# Node 8 — Result Formatter
# ─────────────────────────────────────────────

_METRIC_KW = frozenset({
    "count", "sum", "total", "avg", "average", "max", "min",
    "amount", "revenue", "profit", "cost", "rate", "percentage",
    "score", "salary", "budget",
})


def result_formatter_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    exec_result = state.get("execution_result", {})
    llm_resp    = state.get("llm_response", {})

    rows: list    = exec_result.get("rows", [])
    columns: list = exec_result.get("columns", [])

    # Columns without aggregate keywords → dimensions
    dimensions = [col for col in columns if not any(kw in col.lower() for kw in _METRIC_KW)]

    recommended_column_filters = filter_recommender.recommended_column_filters(
        rows=rows,
        columns=columns,
    )

    formatted = {
        "sql_query": state.get("refined_sql", ""),
        "explanation": llm_resp.get("explanation", ""),
        "dimensions": dimensions,
        "recommended_column_filters": recommended_column_filters,
        "data": rows,
        "row_count": len(rows),
        "execution_time": state.get("execution_time", 0.0),
    }

    step = {
        "node": "result_formatter",
        "status": "success",
        "row_count": len(rows),
        "filter_columns": len(recommended_column_filters),
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[result_formatter] user={state['user_id']} rows={len(rows)} filter_cols={len(recommended_column_filters)}")
    return {"formatted_result": formatted, "steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 9 — Memory Store
# ─────────────────────────────────────────────

def memory_store_node(state: AgentState) -> Dict[str, Any]:
    t0 = time.time()
    user_id = state["user_id"]
    formatted = state.get("formatted_result", {})
    sql = state.get("refined_sql", "")

    row_count = formatted.get("row_count", 0)
    explanation = formatted.get("explanation", "Query completed.")
    assistant_msg = f"{explanation} (Returned {row_count} rows)"

    memory_manager.add_interaction(
        user_id=user_id,
        user_message=state["user_query"],
        assistant_message=assistant_msg,
        sql_query=sql,
    )

    step = {
        "node": "memory_store",
        "status": "success",
        "duration_ms": round((time.time() - t0) * 1000, 1),
    }
    logger.info(f"[memory_store] user={user_id} stored interaction")
    return {"steps": _steps(state) + [step]}


# ─────────────────────────────────────────────
# Node 10 — Error Handler
# ─────────────────────────────────────────────

def error_handler_node(state: AgentState) -> Dict[str, Any]:
    message = state.get("validation_message", "An unknown error occurred")
    retry_count = state.get("retry_count", 0)

    if state.get("execution_result", {}).get("error"):
        message = state["execution_result"]["error"]

    suggestion = _suggest(message)

    formatted_error = {
        "sql_query": state.get("refined_sql", ""),
        "explanation": "",
        "dimensions": [],
        "recommended_column_filters": [],
        "data": [],
        "row_count": 0,
        "execution_time": state.get("execution_time", 0.0),
        "error": f"{message} — {suggestion}",
    }

    step = {
        "node": "error_handler",
        "status": "error",
        "message": message,
        "retries_exhausted": retry_count >= settings.MAX_RETRY_COUNT,
    }
    logger.error(f"[error_handler] user={state['user_id']} error={message}")
    return {"formatted_result": formatted_error, "steps": _steps(state) + [step]}


def _suggest(error_message: str) -> str:
    msg = error_message.lower()
    if "unsafe" in msg or "forbidden" in msg:
        return "Only SELECT queries are allowed. Please rephrase your request."
    if "user_id" in msg or "rbac" in msg:
        return "Access control prevented this query. Contact support if this is unexpected."
    if "cartesian" in msg:
        return "Try a more specific query with explicit table relationships."
    if "table" in msg and "doesn't exist" in msg:
        return "The referenced table may not exist. Try refreshing the schema via POST /api/v1/refresh-schema."
    if "timeout" in msg:
        return "Query timed out. Try adding more specific filters to reduce result size."
    return "Please rephrase your query or contact support if the issue persists."


# ─────────────────────────────────────────────
# Conditional Edge Functions
# ─────────────────────────────────────────────

def route_after_validation(state: AgentState) -> str:
    status = state.get("validation_status", "")
    retry_count = state.get("retry_count", 0)

    if status == ValidationStatus.VALID.value:
        return "execute"
    if status == ValidationStatus.UNSAFE.value:
        return "stop"
    # INVALID — retry if budget remains
    if retry_count < settings.MAX_RETRY_COUNT:
        return "retry"
    return "stop"


def route_after_execution(state: AgentState) -> str:
    exec_error = state.get("execution_result", {}).get("error")
    retry_count = state.get("retry_count", 0)

    if not exec_error:
        return "format"
    if retry_count < settings.MAX_RETRY_COUNT:
        return "retry"
    return "stop"
