from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    user_id: str
    user_query: str
    user_role: str          # "employee" | "lead" | "manager" | "hr"
    debug: bool

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
    intent_track: str           # "greeting" | "off_topic" | "incomplete" | "clear"
    intent_confidence: float    # 0.0 to 1.0
    intent_reasoning: str       # debug explanation from LLM
    greeting_message: str       # warm reply + report suggestion (greeting track only)
    off_topic_reason: str       # "general_knowledge" | "unrelated" | "personal"
    off_topic_message: str      # polite decline + KRA redirect (no bullet list)
    clarification_round: int    # 0 = first call, 1 = after first followup, max 2
    follow_up_question: str     # question to show the user
    follow_up_options: List[str]  # suggested answer choices
    user_answer: str            # user's response (populated on /clarify calls)
    prior_followup: str         # last question asked (prevent repeating same question)
    extracted_filters: Dict[str, Any]  # e.g. {"period": "Q1 2025"}
    enriched_prompt: str        # rewritten prompt passed into prompt_builder

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
