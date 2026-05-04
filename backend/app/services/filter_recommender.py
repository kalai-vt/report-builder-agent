import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

_DATE_HINTS    = {"date", "_at", "time", "created", "updated", "joined",
                  "started", "ended", "expired", "modified", "timestamp", "dob", "birth"}
_TEXT_HINTS    = {"name", "email", "mail", "description", "remark", "comment",
                  "address", "note", "message", "subject", "summary"}
_BOOL_PREFIXES = ("is_", "has_", "can_", "allow", "flag", "enable", "active", "delete", "visible")
_SKIP_COLUMNS  = {"id", "password", "token", "secret", "hash", "otp", "profile_url", "folder_id"}

_MAX_CARDINALITY  = 50    # columns with more unique values are free-text → skip
_SAMPLE_THRESHOLD = 200   # sample first N rows for cardinality check on large result sets


class FilterRecommender:

    def recommended_column_filters(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
    ) -> List[str]:
        if not rows or not columns:
            return []

        # Sample rows for large result sets to keep cardinality check fast
        sample = rows[:_SAMPLE_THRESHOLD] if len(rows) > _SAMPLE_THRESHOLD else rows

        result = [col for col in columns if self._should_include(col, sample)]
        logger.debug(f"[filter_recommender] {len(result)}/{len(columns)} columns recommended")
        return result

    def _should_include(self, col: str, rows: List[Dict]) -> bool:
        col_lower = col.lower()

        if col_lower in _SKIP_COLUMNS:
            return False
        if col_lower == "id" or col_lower.endswith("_id") or col_lower.endswith("_key"):
            return False
        if any(hint in col_lower for hint in _TEXT_HINTS):
            return False

        # Date columns → always useful for range filter
        if any(hint in col_lower for hint in _DATE_HINTS):
            return True

        # Boolean columns
        if col_lower.startswith(_BOOL_PREFIXES):
            return True

        values = [row[col] for row in rows if row.get(col) is not None]
        if not values:
            return False

        # Low-cardinality check (numeric or string)
        unique_count = len(set(str(v) for v in values))
        return unique_count <= _MAX_CARDINALITY


filter_recommender = FilterRecommender()
