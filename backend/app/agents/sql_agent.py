import logging
import re

logger = logging.getLogger(__name__)

# Single compiled pattern for repeated whitespace
_MULTI_SPACE = re.compile(r"[ \t]{2,}")

# Matches the SELECT body (everything between SELECT and the first FROM)
_SELECT_BODY = re.compile(r"(?is)(?<=select)(.*?)(?=\bfrom\b)")

# Matches u.stream (optionally aliased) inside the SELECT list
_U_STREAM_COL = re.compile(r"\bu\.stream\b(?:\s+AS\s+\w+)?", re.IGNORECASE)

# Detects whether the tags table is already joined
_TAGS_JOIN_PRESENT = re.compile(r"\btags\b", re.IGNORECASE)


class SQLRefinementAgent:
    """Lightweight pre-processing of raw LLM SQL before validation."""

    def refine(self, sql: str) -> str:
        sql = re.sub(r"```sql\s*", "", sql, flags=re.IGNORECASE)
        sql = re.sub(r"```\s*", "", sql)
        sql = sql.replace("‘", "'").replace("’", "'")
        sql = sql.replace("“", '"').replace("”", '"')
        sql = _MULTI_SPACE.sub(" ", sql)
        sql = sql.strip().rstrip(";")
        sql = self._fix_stream_select(sql)
        return sql

    def _fix_stream_select(self, sql: str) -> str:
        """
        If the LLM selected u.stream (a numeric tag_id) in the SELECT list,
        replace it with t.tag AS stream and ensure the tags JOIN is present.
        user_table.stream stores a foreign key into tags.tag_id and must never
        be returned directly to the user.
        """
        m = _SELECT_BODY.search(sql)
        if not m:
            return sql

        select_body = m.group(0)
        if not _U_STREAM_COL.search(select_body):
            return sql  # u.stream not in SELECT — nothing to fix

        fixed_select = _U_STREAM_COL.sub("t.tag AS stream", select_body)
        sql = sql[: m.start()] + fixed_select + sql[m.end():]
        logger.debug("stream_fix: replaced u.stream with t.tag AS stream in SELECT")

        # Add tags JOIN if the query does not already have one
        if not _TAGS_JOIN_PRESENT.search(sql):
            sql = re.sub(
                r"\bWHERE\b",
                "LEFT JOIN tags t ON t.tag_id = u.stream\nWHERE",
                sql,
                count=1,
                flags=re.IGNORECASE,
            )
            logger.debug("stream_fix: injected LEFT JOIN tags t ON t.tag_id = u.stream")

        return sql


sql_refinement_agent = SQLRefinementAgent()
