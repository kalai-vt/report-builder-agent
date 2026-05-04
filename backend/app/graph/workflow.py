import logging
from typing import Any, Dict

from langgraph.graph import END, StateGraph

from app.graph.nodes import (
    error_handler_node,
    execution_engine_node,
    increment_retry_execution_node,
    increment_retry_validation_node,
    llm_node,
    load_context_node,
    memory_store_node,
    prompt_builder_node,
    result_formatter_node,
    route_after_execution,
    route_after_validation,
    sql_agent_node,
    sql_validator_node,
)
from app.graph.state import AgentState

logger = logging.getLogger(__name__)


def build_workflow():
    graph = StateGraph(AgentState)

    # ── Register nodes ──────────────────────────────────────────────────────
    graph.add_node("load_context", load_context_node)
    graph.add_node("prompt_builder", prompt_builder_node)
    graph.add_node("llm", llm_node)
    graph.add_node("sql_agent", sql_agent_node)
    graph.add_node("sql_validator", sql_validator_node)
    graph.add_node("increment_retry_validation", increment_retry_validation_node)
    graph.add_node("execution_engine", execution_engine_node)
    graph.add_node("increment_retry_execution", increment_retry_execution_node)
    graph.add_node("result_formatter", result_formatter_node)
    graph.add_node("memory_store", memory_store_node)
    graph.add_node("error_handler", error_handler_node)

    # ── Entry point ──────────────────────────────────────────────────────────
    graph.set_entry_point("load_context")

    # ── Linear edges ────────────────────────────────────────────────────────
    graph.add_edge("load_context", "prompt_builder")
    graph.add_edge("prompt_builder", "llm")
    graph.add_edge("llm", "sql_agent")
    graph.add_edge("sql_agent", "sql_validator")

    # ── Conditional: after validation ───────────────────────────────────────
    graph.add_conditional_edges(
        "sql_validator",
        route_after_validation,
        {
            "execute": "execution_engine",
            "retry": "increment_retry_validation",
            "stop": "error_handler",
        },
    )

    # Retry loop: validation failure → rebuild prompt → re-run pipeline
    graph.add_edge("increment_retry_validation", "prompt_builder")

    # ── Conditional: after execution ─────────────────────────────────────────
    graph.add_conditional_edges(
        "execution_engine",
        route_after_execution,
        {
            "format": "result_formatter",
            "retry": "increment_retry_execution",
            "stop": "error_handler",
        },
    )

    # Retry loop: execution failure → rebuild prompt → re-run pipeline
    graph.add_edge("increment_retry_execution", "prompt_builder")

    # ── Terminal edges ───────────────────────────────────────────────────────
    graph.add_edge("result_formatter", "memory_store")
    graph.add_edge("memory_store", END)
    graph.add_edge("error_handler", END)

    return graph.compile()


# Compiled once at module import
_workflow = build_workflow()


async def run_report_agent(user_id: str, query: str, debug: bool = False) -> Dict[str, Any]:
    initial: AgentState = {
        "user_id": user_id,
        "user_query": query,
        "debug": debug,
        "schema": "",
        "memory_context": "",
        "prompt": "",
        "llm_response": {},
        "refined_sql": "",
        "validation_status": "",
        "validation_message": "",
        "execution_result": {},
        "execution_time": 0.0,
        "formatted_result": {},
        "retry_count": 0,
        "retry_feedback": "",
        "steps": [],
    }

    try:
        final: AgentState = await _workflow.ainvoke(initial)
        result = dict(final.get("formatted_result", {}))
        if debug:
            result["steps"] = final.get("steps", [])
        logger.info(f"[workflow] completed for user={user_id} rows={result.get('row_count', 0)}")
        return result
    except Exception as e:
        logger.exception(f"[workflow] unhandled exception for user={user_id}: {e}")
        return {
            "data": [],
            "columns": [],
            "metrics": [],
            "dimensions": [],
            "row_count": 0,
            "execution_time": 0.0,
            "error": f"Pipeline error: {e}",
            "suggestion": "An unexpected error occurred. Please try again.",
        }
