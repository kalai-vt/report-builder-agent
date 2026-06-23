"""
Schema Metadata Service

Fetches live dimension values from the KRA database to use as grounded
clarification options (streams, statuses, designations, categories).

Falls back to curated static lists when the database is unreachable.
Results are cached in-memory with a 5-minute TTL to avoid repeated queries
on every clarification request.
"""

import logging
import time
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # 5 minutes


# ── Static fallbacks (used when DB is unreachable) ────────────────────────────

_FALLBACK_STREAMS = ["QA", "Development", "DevOps", "Design", "Management"]
_FALLBACK_STATUSES = ["Completed", "In Progress", "Pending", "Not Started", "Overdue"]
_FALLBACK_DESIGNATIONS = [
    "Software Engineer",
    "Senior Software Engineer",
    "Lead Engineer",
    "Engineering Manager",
    "QA Engineer",
]
_FALLBACK_CATEGORIES = [
    "Technical Skills",
    "Communication",
    "Leadership",
    "Delivery Excellence",
]

_REPORT_TYPES = [
    "KRA Report",
    "Compliance Report",
    "Feedback Report",
    "Goal Completion Report",
    "Missing Remarks Report",
    "At-Risk Goals Report",
]

_KRA_METRIC_OPTIONS = [
    "Goal completion",
    "Remark compliance",
    "At-risk goals",
    "Missing remarks",
    "Overall KRA health",
]

_PERFORMANCE_TYPE_OPTIONS = [
    "Employee performance report",
    "Stream-wise performance report",
    "Team performance report",
    "Company-wide performance report",
]

_PERIOD_OPTIONS = [
    "Current Month",
    "Last Month",
    "Current Quarter",
    "Last Quarter",
    "This Year",
    "Custom Range",
]

_COMPLIANCE_SCOPE_OPTIONS = [
    "Company-wide compliance",
    "Stream-wise compliance",
    "Team-wise compliance",
    "Employee-wise compliance",
]


class SchemaMetadataService:
    """
    Provides schema-grounded option lists for the Clarification Agent.

    All methods are safe to call even when the database is down — they
    return fallback values and log a warning rather than raising.
    """

    def __init__(self) -> None:
        self._cache: Dict[str, Tuple[List[str], float]] = {}

    # ── Cache helpers ─────────────────────────────────────────────────────────

    def _cached(self, key: str) -> Optional[List[str]]:
        entry = self._cache.get(key)
        if entry and time.time() - entry[1] < _CACHE_TTL:
            return entry[0]
        return None

    def _store(self, key: str, values: List[str]) -> List[str]:
        self._cache[key] = (values, time.time())
        return values

    def _query(self, sql: str, col: int = 0) -> List[str]:
        from app.db.connection import db_manager
        from sqlalchemy import text

        with db_manager.engine.connect() as conn:
            rows = conn.execute(text(sql))
            return [str(r[col]) for r in rows if r[col]]

    # ── Live schema dimensions ────────────────────────────────────────────────

    def get_streams(self) -> List[str]:
        """Active stream/tag names from the `tags` table."""
        cached = self._cached("streams")
        if cached is not None:
            return cached
        try:
            values = self._query(
                "SELECT DISTINCT tag FROM tags WHERE is_active = 1 ORDER BY tag LIMIT 20"
            )
            if not values:
                values = self._query(
                    "SELECT DISTINCT tag FROM tags ORDER BY tag LIMIT 20"
                )
            if values:
                return self._store("streams", values)
        except Exception as exc:
            logger.warning("[schema_metadata] streams fetch failed: %s", exc)
        return self._store("streams", _FALLBACK_STREAMS)

    def get_statuses(self) -> List[str]:
        """Goal/task status names from the `status` table."""
        cached = self._cached("statuses")
        if cached is not None:
            return cached
        try:
            values = self._query(
                "SELECT DISTINCT status_name FROM status ORDER BY status_name LIMIT 20"
            )
            if values:
                return self._store("statuses", values)
        except Exception as exc:
            logger.warning("[schema_metadata] statuses fetch failed: %s", exc)
        return self._store("statuses", _FALLBACK_STATUSES)

    def get_designations(self) -> List[str]:
        """Active designation names from the `designation` table."""
        cached = self._cached("designations")
        if cached is not None:
            return cached
        try:
            values = self._query(
                "SELECT DISTINCT designation_name FROM designation "
                "WHERE is_active = 1 ORDER BY designation_name LIMIT 20"
            )
            if values:
                return self._store("designations", values)
        except Exception as exc:
            logger.warning("[schema_metadata] designations fetch failed: %s", exc)
        return self._store("designations", _FALLBACK_DESIGNATIONS)

    def get_categories(self) -> List[str]:
        """Active KRA category names from `master_categories`."""
        cached = self._cached("categories")
        if cached is not None:
            return cached
        try:
            values = self._query(
                "SELECT DISTINCT category_name FROM master_categories "
                "WHERE is_active = 1 ORDER BY category_name LIMIT 20"
            )
            if values:
                return self._store("categories", values)
        except Exception as exc:
            logger.warning("[schema_metadata] categories fetch failed: %s", exc)
        return self._store("categories", _FALLBACK_CATEGORIES)

    # ── Static option lists ───────────────────────────────────────────────────

    def get_report_types(self) -> List[str]:
        return list(_REPORT_TYPES)

    def get_kra_metric_options(self) -> List[str]:
        return list(_KRA_METRIC_OPTIONS)

    def get_performance_type_options(self) -> List[str]:
        return list(_PERFORMANCE_TYPE_OPTIONS)

    def get_period_options(self) -> List[str]:
        return list(_PERIOD_OPTIONS)

    def get_compliance_scope_options(self) -> List[str]:
        return list(_COMPLIANCE_SCOPE_OPTIONS)

    # ── Cache management ──────────────────────────────────────────────────────

    def invalidate(self, key: Optional[str] = None) -> None:
        """Invalidate one cached entry or all entries (key=None)."""
        if key is None:
            self._cache.clear()
        else:
            self._cache.pop(key, None)

    def cache_info(self) -> Dict[str, float]:
        """Return age-in-seconds for each cached key (useful for debugging)."""
        now = time.time()
        return {k: round(now - v[1], 1) for k, v in self._cache.items()}


# ── Module-level singleton ────────────────────────────────────────────────────
schema_metadata_service = SchemaMetadataService()
