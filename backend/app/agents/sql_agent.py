import logging
import re

logger = logging.getLogger(__name__)

# Single compiled pattern for repeated whitespace
_MULTI_SPACE = re.compile(r"[ \t]{2,}")


class SQLRefinementAgent:
    """Lightweight pre-processing of raw LLM SQL before validation."""

    def refine(self, sql: str) -> str:
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql)
        sql = sql.replace("‘", "'").replace("’", "'")
        sql = sql.replace("“", '"').replace("”", '"')
        sql = _MULTI_SPACE.sub(" ", sql)
        return sql.strip().rstrip(";")


sql_refinement_agent = SQLRefinementAgent()
