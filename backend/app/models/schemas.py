from pydantic import BaseModel, Field
from typing import Any, Dict, List, Optional


class QueryRequest(BaseModel):
    query: str = Field(..., description="Natural language question about the KRA data")

    class Config:
        json_schema_extra = {"example": {"query": "Show all employees with designation and stream"}}


class QueryResponse(BaseModel):
    sql_query: Optional[str] = None
    explanation: Optional[str] = None
    dimensions: List[str] = Field(default_factory=list)
    recommended_column_filters: List[str] = Field(default_factory=list)
    data: List[Dict[str, Any]] = Field(default_factory=list)
    row_count: int = 0
    execution_time: float = 0.0
    error: Optional[str] = None
