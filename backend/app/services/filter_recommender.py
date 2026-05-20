import logging
from typing import Any, Dict, List

logger = logging.getLogger(__name__)

# ── Column classification hints ───────────────────────────────────────────────

_DATE_HINTS   = {"date", "_at", "time", "created", "updated", "joined",
                 "started", "ended", "expired", "modified", "timestamp",
                 "dob", "birth", "month", "quarter", "year"}
_TEXT_HINTS   = {"name", "email", "mail", "description", "remark", "comment",
                 "address", "note", "message", "subject", "summary"}
_BOOL_PREFIX  = ("is_", "has_", "can_", "allow", "flag", "enable",
                 "active", "delete", "visible")
_SKIP_COLUMNS = {"password", "token", "secret", "hash", "otp",
                 "profile_url", "folder_id"}

# Columns whose names end with these suffixes are IDs — skip for filter UX
_ID_SUFFIXES  = ("_id", "_key", "_code", "_ref")

_MAX_CARDINALITY  = 50    # more unique values → text_search, not categorical
_SAMPLE_THRESHOLD = 200   # rows to sample for cardinality check


def _col_to_label(col: str) -> str:
    """Convert snake_case column name to a human-readable display label."""
    label = col
    if label.endswith("_id"):
        label = label[:-3]
    elif label.endswith("_at"):
        label = label[:-3] + " date"
    return label.replace("_", " ").title()


def _is_numeric(values: list) -> bool:
    return all(isinstance(v, (int, float)) for v in values)


def _is_date_col(col_lower: str) -> bool:
    return any(hint in col_lower for hint in _DATE_HINTS)


class FilterRecommender:
    """
    Classifies result-set columns into filterable metadata blocks the
    frontend can use to render filter controls.  All filtering happens
    client-side on the already-fetched result set — no new SQL or LLM
    calls are triggered when a filter is applied.
    """

    # ── Public API ────────────────────────────────────────────────────────────

    def filterable_columns(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
    ) -> List[Dict[str, Any]]:
        """Return a list of filterable-column metadata dicts.

        Each dict has keys: column, label, filter_type, values.
        filter_type is one of: categorical | date_range | text_search | numeric_range
        values holds distinct values for categorical columns; empty list otherwise.
        """
        if not rows or not columns:
            return []

        sample = rows[:_SAMPLE_THRESHOLD] if len(rows) > _SAMPLE_THRESHOLD else rows
        result = []
        for col in columns:
            meta = self._classify(col, sample)
            if meta:
                result.append(meta)

        logger.debug(
            f"[filter_recommender] {len(result)}/{len(columns)} columns classified as filterable"
        )
        return result

    def recommended_column_filters(
        self,
        rows: List[Dict[str, Any]],
        columns: List[str],
    ) -> List[str]:
        """Backward-compatible: return just the filterable column names."""
        return [fc["column"] for fc in self.filterable_columns(rows, columns)]

    # ── Internal ──────────────────────────────────────────────────────────────

    def _classify(self, col: str, rows: List[Dict]) -> Dict | None:
        col_lower = col.lower()

        # Skip sensitive / low-value columns
        if col_lower in _SKIP_COLUMNS:
            return None
        if col_lower == "id" or any(col_lower.endswith(s) for s in _ID_SUFFIXES):
            return None

        label = _col_to_label(col)

        # Date columns → date_range
        if _is_date_col(col_lower):
            return {"column": col, "label": label,
                    "filter_type": "date_range", "values": []}

        # Boolean columns → categorical (True / False)
        if col_lower.startswith(_BOOL_PREFIX):
            values = sorted({str(row[col]) for row in rows if row.get(col) is not None})
            return {"column": col, "label": label,
                    "filter_type": "categorical", "values": values}

        # Gather non-null values for cardinality / type analysis
        raw_values = [row[col] for row in rows if row.get(col) is not None]
        if not raw_values:
            return None

        # Numeric columns → numeric_range
        if _is_numeric(raw_values):
            return {"column": col, "label": label,
                    "filter_type": "numeric_range", "values": []}

        # String columns — check cardinality
        unique_vals = sorted({str(v) for v in raw_values})
        if len(unique_vals) <= _MAX_CARDINALITY:
            # Low cardinality → categorical dropdown with distinct values
            return {"column": col, "label": label,
                    "filter_type": "categorical", "values": unique_vals}

        # High cardinality strings — text_search (skip pure free-text hints)
        if any(hint in col_lower for hint in _TEXT_HINTS):
            return {"column": col, "label": label,
                    "filter_type": "text_search", "values": []}

        # High-cardinality non-text string — omit (too many values to filter usefully)
        return None


filter_recommender = FilterRecommender()
