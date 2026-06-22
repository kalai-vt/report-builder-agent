import logging
import time
from datetime import datetime, timezone
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from app.config import settings

from app.graph.nodes import (
    cache_lookup_node,
    cache_store_node,
    cache_write_node,
    context_manager_node,
    error_handler_node,
    execution_engine_node,
    greeting_node,
    increment_retry_execution_node,
    increment_retry_test_node,
    increment_retry_validation_node,
    intent_detector_node,
    kra_clarification_detector_node,
    kra_clarification_node,
    llm_node,
    load_context_node,
    memory_store_node,
    off_topic_node,
    prompt_builder_node,
    refresh_execution_node,
    relationship_clarification_node,
    relationship_classifier_node,
    response_node,
    result_formatter_node,
    route_after_execution,
    route_after_intent_detection,
    route_after_kra_clarification,
    route_after_relationship_classification,
    route_after_sql_test,
    route_after_validation,
    sql_agent_node,
    sql_test_node,
    sql_validator_node,
)
from app.graph.state import AgentState

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# Shared routing helpers
# ─────────────────────────────────────────────────────────────────────────────

def _route_after_cache_lookup(state: AgentState) -> str:
    return "memory_store" if state.get("cache_hit") else "intent_detector"


# ─────────────────────────────────────────────────────────────────────────────
# Main workflow (first-query path)
# ─────────────────────────────────────────────────────────────────────────────

def build_workflow():
    graph = StateGraph(AgentState)

    # ── Nodes ────────────────────────────────────────────────────────────────
    graph.add_node("load_context",               load_context_node)
    graph.add_node("cache_lookup",               cache_lookup_node)
    graph.add_node("intent_detector",            intent_detector_node)
    graph.add_node("greeting",                   greeting_node)
    graph.add_node("off_topic",                  off_topic_node)
    graph.add_node("kra_clarification_detector", kra_clarification_detector_node)
    graph.add_node("kra_clarification",          kra_clarification_node)
    graph.add_node("relationship_classifier",    relationship_classifier_node)
    graph.add_node("relationship_clarification", relationship_clarification_node)
    graph.add_node("context_manager",            context_manager_node)
    graph.add_node("prompt_builder",             prompt_builder_node)
    graph.add_node("llm",                        llm_node)
    graph.add_node("sql_agent",                  sql_agent_node)
    graph.add_node("sql_validator",              sql_validator_node)
    graph.add_node("sql_test",                   sql_test_node)
    graph.add_node("increment_retry_test",       increment_retry_test_node)
    graph.add_node("cache_write",                cache_write_node)
    graph.add_node("increment_retry_validation", increment_retry_validation_node)
    graph.add_node("execution_engine",           execution_engine_node)
    graph.add_node("increment_retry_execution",  increment_retry_execution_node)
    graph.add_node("result_formatter",           result_formatter_node)
    graph.add_node("cache_store",                cache_store_node)
    graph.add_node("memory_store",               memory_store_node)
    graph.add_node("error_handler",              error_handler_node)

    # ── Entry point ───────────────────────────────────────────────────────────
    graph.set_entry_point("load_context")
    graph.add_edge("load_context", "cache_lookup")

    # ── Cache hit → skip pipeline; miss → intent detection ───────────────────
    graph.add_conditional_edges(
        "cache_lookup",
        _route_after_cache_lookup,
        {"memory_store": "memory_store", "intent_detector": "intent_detector"},
    )

    # ── Intent routing (Track A / B / C) ─────────────────────────────────────
    # Track C ("prompt_builder") goes to kra_clarification_detector first,
    # which either asks for clarification or passes through to relationship_classifier.
    graph.add_conditional_edges(
        "intent_detector",
        route_after_intent_detection,
        {
            "greeting":       "greeting",
            "off_topic":      "off_topic",
            "prompt_builder": "kra_clarification_detector",
        },
    )
    graph.add_edge("greeting",  END)
    graph.add_edge("off_topic", END)

    # ── KRA clarification → ask user or proceed ───────────────────────────────
    graph.add_conditional_edges(
        "kra_clarification_detector",
        route_after_kra_clarification,
        {
            "clarify": "kra_clarification",
            "proceed": "relationship_classifier",
        },
    )
    graph.add_edge("kra_clarification", END)

    # ── Relationship classification → clarify or proceed ─────────────────────
    graph.add_conditional_edges(
        "relationship_classifier",
        route_after_relationship_classification,
        {
            "clarify": "relationship_clarification",
            "proceed": "context_manager",
        },
    )
    graph.add_edge("relationship_clarification", END)
    graph.add_edge("context_manager", "prompt_builder")

    # ── SQL generation pipeline ───────────────────────────────────────────────
    graph.add_edge("prompt_builder", "llm")
    graph.add_edge("llm",            "sql_agent")
    graph.add_edge("sql_agent",      "sql_validator")

    # ── After validation: dry-run test, then cache + execute; retry/stop on failure ─
    graph.add_conditional_edges(
        "sql_validator",
        route_after_validation,
        {
            "execute": "sql_test",                  # validated → LIMIT 0 dry-run
            "retry":   "increment_retry_validation",
            "stop":    "error_handler",
        },
    )
    # sql_test: pass → cache → execute; fail → retry or stop
    graph.add_conditional_edges(
        "sql_test",
        route_after_sql_test,
        {
            "cache": "cache_write",
            "retry": "increment_retry_test",
            "stop":  "error_handler",
        },
    )
    graph.add_edge("increment_retry_test",       "prompt_builder")
    graph.add_edge("cache_write",                "execution_engine")
    graph.add_edge("increment_retry_validation", "prompt_builder")

    # ── After execution ───────────────────────────────────────────────────────
    graph.add_conditional_edges(
        "execution_engine",
        route_after_execution,
        {
            "format": "result_formatter",
            "retry":  "increment_retry_execution",
            "stop":   "error_handler",
        },
    )
    graph.add_edge("increment_retry_execution", "prompt_builder")

    # ── Terminal ──────────────────────────────────────────────────────────────
    graph.add_edge("result_formatter", "cache_store")
    graph.add_edge("cache_store",      "memory_store")
    graph.add_edge("memory_store",     END)
    graph.add_edge("error_handler",    END)

    return graph.compile()


# ─────────────────────────────────────────────────────────────────────────────
# Refresh workflow (bypass all LLM / validation / RBAC nodes)
# ─────────────────────────────────────────────────────────────────────────────

def build_refresh_workflow():
    graph = StateGraph(AgentState)
    graph.add_node("refresh_execution", refresh_execution_node)
    graph.add_node("response",          response_node)
    graph.set_entry_point("refresh_execution")
    graph.add_edge("refresh_execution", "response")
    graph.add_edge("response",          END)
    return graph.compile()


# Compiled once at module import — singletons
_workflow         = build_workflow()
_refresh_workflow = build_refresh_workflow()


# ─────────────────────────────────────────────────────────────────────────────
# Entry points
# ─────────────────────────────────────────────────────────────────────────────

def _base_state(extra: Dict[str, Any]) -> AgentState:
    """Merge caller-supplied fields with safe defaults for all AgentState keys."""
    defaults: AgentState = {
        "user_id":             "",
        "user_query":          "",
        "user_role":           "employee",
        "session_id":          "",
        "chat_session_id":     "",
        "debug":               False,
        "schema":              "",
        "memory_context":      "",
        "prompt":              "",
        "llm_response":        {},
        "refined_sql":         "",
        "validation_status":   "",
        "validation_message":  "",
        "execution_result":    {},
        "execution_time":      0.0,
        "formatted_result":    {},
        "cache_hit":           False,
        "cache_key":           "",
        "retry_count":         0,
        "retry_feedback":      "",
        "steps":               [],
        "intent_track":        "",
        "intent_confidence":   0.0,
        "intent_reasoning":    "",
        "greeting_message":    "",
        "off_topic_reason":    "",
        "off_topic_message":   "",
        "extracted_filters":   {},
        "enriched_prompt":     "",
        # KRA clarification (slot-based)
        "kra_clarification_needed":        False,
        "kra_clarification_reason":        "",
        "kra_clarification_question":      "",
        "kra_clarification_options":       [],
        "kra_clarification_missing_slots": [],
        "kra_is_clarification_answer":     False,
        # Relationship classification
        "relationship_type":       "new_request",
        "relationship_confidence": 0.0,
        "clarification_question":  "",
        "active_report_context":   {},
        "refresh_mode":          False,
        "refreshed_at":          "",
        # SQL test execution
        "test_execution_passed": False,
        "test_execution_error":  "",
        # Pagination
        "page":          1,
        "page_size":     settings.PAGE_SIZE,
        "total_rows":    0,
        "total_pages":   1,
        "has_next_page": False,
        "has_prev_page": False,
    }
    defaults.update(extra)
    return defaults


async def run_report_agent(
    user_id: str,
    query: str,
    debug: bool = False,
    session_id: str = "",
) -> Dict[str, Any]:
    """Original entry point — preserved for the existing /api/v1/query endpoint."""
    initial = _base_state({"user_id": user_id, "user_query": query, "debug": debug, "session_id": session_id})
    try:
        final: AgentState = await _workflow.ainvoke(initial)
        result = dict(final.get("formatted_result", {}))
        result.setdefault("cache_hit", False)
        if debug:
            result["steps"] = final.get("steps", [])
        logger.info(
            f"[workflow] completed user={user_id} rows={result.get('row_count', 0)} "
            f"cache_hit={result['cache_hit']}"
        )
        return result
    except Exception as exc:
        logger.exception(f"[workflow] unhandled error user={user_id}: {exc}")
        return {
            "data": [], "row_count": 0, "execution_time": 0.0,
            "error": f"Pipeline error: {exc}",
        }


async def run_intent_report(
    user_id: str,
    query: str,
    user_role: str = "employee",
    session_id: str = "",
    chat_session_id: str = "",
    debug: bool = False,
    page: int = 1,
    page_size: int = 0,
) -> Dict[str, Any]:
    """Intent-aware entry point used by /report/generate."""
    initial = _base_state({
        "user_id":         user_id,
        "user_query":      query,
        "user_role":       user_role,
        "session_id":      session_id,
        "chat_session_id": chat_session_id,
        "debug":           debug,
        "page":            page,
        "page_size":       page_size or settings.PAGE_SIZE,
    })
    try:
        final: AgentState = await _workflow.ainvoke(initial)
        result = dict(final.get("formatted_result", {}))
        result.setdefault("status", "error" if result.get("error") else "success")
        if not result.get("enriched_prompt"):
            result["enriched_prompt"] = final.get("enriched_prompt", "")
        result.setdefault("cache_hit", False)
        if debug:
            result["steps"] = final.get("steps", [])
        logger.info(
            f"[workflow] intent_report user={user_id} status={result.get('status')} "
            f"track={final.get('intent_track', '?')}"
        )
        return result
    except Exception as exc:
        logger.exception(f"[workflow] unhandled error user={user_id}: {exc}")
        return {"status": "error", "data": [], "row_count": 0,
                "execution_time": 0.0, "error": f"Pipeline error: {exc}"}


async def run_replay_agent(
    sql_query: str,
    user_id: str,
    user_role: str = "employee",
    page: int = 1,
    page_size: int = 0,
) -> Dict[str, Any]:
    """
    Execute a pre-saved SQL query and return fresh data.
    No LLM, no prompt building — just validate → execute → format.
    Used by POST /report/replay (Load Report from saved configuration).
    """
    from app.db.connection import db_manager
    from app.services.filter_recommender import filter_recommender
    from app.validators.sql_validator import ValidationStatus, sql_validator

    _METRIC_KW = frozenset({
        "count", "sum", "total", "avg", "average", "max", "min",
        "amount", "revenue", "profit", "cost", "rate", "percentage",
        "score", "salary", "budget",
    })

    effective_page_size = page_size or settings.PAGE_SIZE

    # 1. Re-validate the saved SQL (safety guard)
    status, message, clean_sql = sql_validator.validate(sql_query, user_id)
    if status == ValidationStatus.UNSAFE:
        logger.warning(f"[replay] unsafe SQL rejected user={user_id}: {message}")
        return {"status": "error", "error": f"Unsafe query: {message}", "data": [], "row_count": 0}

    # 2. Execute against MySQL
    t0 = time.time()
    bound_params = {"employee_id": user_id}
    try:
        rows, columns, total_rows, total_pages = db_manager.execute_paginated(
            clean_sql, page=page, page_size=effective_page_size, params=bound_params
        )
    except Exception as exc:
        logger.error(f"[replay] execution error user={user_id}: {exc}")
        return {"status": "error", "error": f"Query execution failed: {exc}", "data": [], "row_count": 0}

    duration = round(time.time() - t0, 4)

    # 3. Format
    dimensions = [col for col in columns if not any(kw in col.lower() for kw in _METRIC_KW)]
    filterable_cols = filter_recommender.filterable_columns(rows=rows, columns=columns)

    logger.info(
        f"[replay] user={user_id} rows={len(rows)} total={total_rows} "
        f"page={page}/{total_pages} ms={round(duration * 1000, 1)}"
    )
    return {
        "status": "success",
        "sql_query": clean_sql,
        "explanation": "",
        "dimensions": dimensions,
        "recommended_column_filters": [fc["column"] for fc in filterable_cols],
        "filterable_columns": filterable_cols,
        "filter_instruction": "client_side_only",
        "llm_call_on_filter": False,
        "data": rows,
        "row_count": len(rows),
        "execution_time": duration,
        "page": page,
        "page_size": effective_page_size,
        "total_rows": total_rows,
        "total_pages": total_pages,
        "has_next_page": page < total_pages,
        "has_prev_page": page > 1,
        "refresh_mode": True,
        "refreshed_at": datetime.now(timezone.utc).isoformat(),
    }


async def run_refresh_agent(
    session_id: str,
    user_id: str,
    page: int = 1,
    page_size: int = 0,
) -> Dict[str, Any]:
    """
    Refresh-only entry point — reads cached SQL from Redis and executes it
    directly.  Never calls any LLM, prompt builder, validator, or RBAC node.
    """
    initial = _base_state({
        "session_id":   session_id,
        "user_id":      user_id,
        "refresh_mode": True,
        "page":         page,
        "page_size":    page_size or settings.PAGE_SIZE,
    })
    try:
        final: AgentState = await _refresh_workflow.ainvoke(initial)
        return dict(final.get("formatted_result", {}))
    except Exception as exc:
        logger.exception(f"[refresh_workflow] error session={session_id}: {exc}")
        return {
            "status":     "error",
            "error_code": "EXECUTION_ERROR",
            "message":    f"Refresh failed: {exc}",
        }
