from typing import Any, Dict, List, Optional, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    user_id: str
    user_query: str
    user_role: str          # "employee" | "lead" | "manager" | "hr"
    debug: bool

    # Conversation-level session (frontend-generated UUID, stable across multiple queries)
    # Distinct from session_id which is a per-query Redis cache key.
    chat_session_id: str

    # Context
    schema: str
    memory_context: str

    # Pipeline
    prompt: str
    llm_response: Dict[str, Any]
    refined_sql: str

    # Validation
    validation_status: str
    validation_message: str

    # Execution
    execution_result: Dict[str, Any]
    execution_time: float

    # Output
    formatted_result: Dict[str, Any]

    # Control
    retry_count: int
    retry_feedback: str

    # Cache
    cache_key: str
    cache_hit: bool

    # Observability
    steps: List[Dict[str, Any]]

    # Session / Refresh
    session_id: str             # API-layer session id used as Redis cache key
    refresh_mode: bool          # True when executing via the refresh graph
    refreshed_at: str           # ISO timestamp of last refresh execution

    # Intent Detection
    intent_track: str           # "greeting" | "off_topic" | "clear"
    intent_confidence: float    # 0.0 to 1.0
    intent_reasoning: str       # debug explanation from LLM
    greeting_message: str       # warm reply + report suggestion (greeting track only)
    off_topic_reason: str       # "general_knowledge" | "unrelated" | "personal"
    off_topic_message: str      # polite decline + KRA redirect (no bullet list)
    extracted_filters: Dict[str, Any]  # e.g. {"period": "Q1 2025"}
    enriched_prompt: str        # rewritten prompt passed into prompt_builder

    # Relationship Classification (follow-up vs new request)
    relationship_type: str           # "followup" | "new_request" | "uncertain"
    relationship_confidence: float   # 0.0 to 1.0
    clarification_question: str      # populated when relationship_type == "uncertain"

    # Active Report Context (structured state for the current open report)
    active_report_context: Dict[str, Any]

    # SQL Test Execution (LIMIT 0 dry-run before full execution)
    test_execution_passed: bool   # True when LIMIT 0 dry-run succeeded
    test_execution_error: str     # DB error message from a failed dry-run

    # Pagination
    page: int                   # 1-based current page number
    page_size: int              # rows per page (capped at MAX_PAGE_SIZE)
    total_rows: int             # total rows the base query would return
    total_pages: int            # ceil(total_rows / page_size)
    has_next_page: bool         # page < total_pages
    has_prev_page: bool         # page > 1
