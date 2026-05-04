import logging
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

from app.services.report_service import report_service

logger = logging.getLogger(__name__)
router = APIRouter()


class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the KRA data")

    class Config:
        json_schema_extra = {
            "example": {
                "query": "Show all active KRAs with employee names and current progress"
            }
        }


class QueryResponse(BaseModel):
    sql_query: Optional[str] = Field(
        None, description="Generated MySQL SELECT statement"
    )
    explanation: Optional[str] = Field(
        None, description="One-sentence description of what the query returns"
    )
    dimensions: List[str] = Field(
        default_factory=list,
        description="Categorical columns in the result (non-aggregate fields)"
    )
    recommended_column_filters: List[str] = Field(
        default_factory=list,
        description=(
            "Column names from the result that are suitable for frontend filtering. "
            "Excludes free-text fields (names, emails) and raw IDs. "
            "Includes categorical, boolean, date, and low-cardinality numeric columns."
        )
    )
    data: List[Dict[str, Any]] = Field(
        default_factory=list,
        description="Result rows as JSON objects"
    )
    row_count: int = Field(0, description="Total number of rows returned")
    execution_time: float = Field(0.0, description="DB query execution time in seconds")
    error: Optional[str] = Field(None, description="Error message if the query failed")


@router.post(
    "/query",
    response_model=QueryResponse,
    summary="Natural Language → SQL → Result",
    description=(
        "Submit a plain-English question. The AI agent:\n\n"
        "1. Converts it to a MySQL SELECT via **GPT-4o-mini**\n"
        "2. Validates the SQL (safety + LIMIT enforcement)\n"
        "3. Executes it against the **vthink_kra** database\n"
        "4. Returns results with `dimensions` and `recommended_column_filters` "
        "for frontend drill-down\n\n"
        "---\n"
        "**Example queries to try:**\n"
        "- `Show all active KRAs with employee names and current progress`\n"
        "- `List employees grouped by designation`\n"
        "- `How many employees are there per stream?`\n"
        "- `Show employees who joined in 2024`\n"
        "- `Show all goals and their completion status`"
    ),
)
async def run_query(request: QueryRequest):
    try:
        result = await report_service.generate(
            user_id="demo_user",
            query=request.query,
            debug=False,
        )
        return QueryResponse(
            sql_query=result.get("sql_query"),
            explanation=result.get("explanation"),
            dimensions=result.get("dimensions", []),
            recommended_column_filters=result.get("recommended_column_filters", []),
            data=result.get("data", []),
            row_count=result.get("row_count", 0),
            execution_time=result.get("execution_time", 0.0),
            error=result.get("error"),
        )
    except Exception as e:
        logger.exception(f"query error: {e}")
        raise HTTPException(status_code=500, detail=str(e))
