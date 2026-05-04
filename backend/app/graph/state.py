from typing import Any, Dict, List, TypedDict


class AgentState(TypedDict, total=False):
    # Input
    user_id: str
    user_query: str
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

    # Observability
    steps: List[Dict[str, Any]]
