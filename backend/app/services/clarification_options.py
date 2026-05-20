"""
Fetches real schema-driven options for each clarification missing_field type.
Results are cached for 5 minutes to avoid DB hits on every classification request.

Override strategy: options are injected into the LLM prompt AND used to replace
whatever the LLM returns — guaranteeing no hallucinated names or dates.
"""

import logging
import time
from typing import Dict, List

from sqlalchemy import text

logger = logging.getLogger(__name__)

_CACHE_TTL = 300  # seconds


class ClarificationOptionsBuilder:

    def __init__(self):
        self._cache: Dict[str, tuple] = {}  # key -> (options, expiry_epoch)

    # ── Public API ────────────────────────────────────────────────────────────

    def get_options(self, missing_field: str) -> List[str]:
        """Return DB-fetched options for a missing_field; falls back to safe defaults."""
        if missing_field in self._cache:
            options, expiry = self._cache[missing_field]
            if time.time() < expiry:
                return options

        try:
            options = self._fetch(missing_field)
        except Exception as exc:
            logger.warning(f"[clarification_options] fetch failed field={missing_field}: {exc}")
            options = self._fallback(missing_field)

        self._cache[missing_field] = (options, time.time() + _CACHE_TTL)
        logger.debug(f"[clarification_options] field={missing_field} count={len(options)}")
        return options

    def get_all_options(self) -> Dict[str, List[str]]:
        """Return options for every missing_field — used to build the prompt section."""
        fields = [
            "time_period", "employee_scope", "status_filter",
            "metric", "schema_scope", "designation",
        ]
        return {f: self.get_options(f) for f in fields}

    def invalidate(self):
        self._cache.clear()
        logger.info("[clarification_options] cache cleared")

    # ── Private helpers ───────────────────────────────────────────────────────

    def _fetch(self, missing_field: str) -> List[str]:
        from app.db.connection import db_manager

        with db_manager.engine.connect() as conn:

            if missing_field == "time_period":
                rows = conn.execute(text(
                    "SELECT DISTINCT "
                    "  CONCAT('Q', QUARTER(target_date), ' ', YEAR(target_date)) AS qtr "
                    "FROM user_goal_mapping "
                    "WHERE target_date IS NOT NULL AND target_date > '2020-01-01' "
                    "ORDER BY target_date DESC "
                    "LIMIT 8"
                ))
                options = [r[0] for r in rows if r[0]]
                if not options:
                    options = self._fallback("time_period")
                return options

            if missing_field == "employee_scope":
                rows = conn.execute(text(
                    "SELECT CONCAT(TRIM(firstname), ' ', TRIM(lastname)) AS name "
                    "FROM user_table "
                    "WHERE is_active = 1 AND is_delete = 0 "
                    "  AND firstname IS NOT NULL AND lastname IS NOT NULL "
                    "ORDER BY firstname "
                    "LIMIT 15"
                ))
                options = [r[0].strip() for r in rows if r[0] and r[0].strip()]
                if not options:
                    options = self._fallback("employee_scope")
                return options

            if missing_field == "status_filter":
                rows = conn.execute(text(
                    "SELECT status_name FROM status ORDER BY id LIMIT 10"
                ))
                options = [r[0] for r in rows if r[0]]
                if "All" not in options:
                    options.append("All")
                return options or self._fallback("status_filter")

            if missing_field == "designation":
                rows = conn.execute(text(
                    "SELECT designation_name FROM designation "
                    "WHERE is_active = 1 "
                    "ORDER BY designation_level "
                    "LIMIT 10"
                ))
                options = [r[0] for r in rows if r[0]]
                return options or self._fallback("designation")

        # metric and schema_scope are schema-structural — no DB row query needed
        if missing_field == "metric":
            return [
                "Goal completion status",
                "Goal count by employee",
                "Progress percentage",
                "Rating summary",
                "Goals by designation",
            ]

        if missing_field == "schema_scope":
            return [
                "KRA Goals (master_goals)",
                "Goal Assignments (user_goal_mapping)",
                "R&R Nominations (rnr_nominations)",
                "Certifications (certification_completion)",
                "Skills (skills)",
            ]

        return self._fallback(missing_field)

    def _fallback(self, missing_field: str) -> List[str]:
        return {
            "time_period":    ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2025", "Full year 2025"],
            "employee_scope": ["All team members", "Specific employee"],
            "status_filter":  ["In Progress", "Completed", "Hold", "Achieved", "All"],
            "metric":         ["Goal completion status", "Goal count", "Progress percentage", "Rating summary"],
            "schema_scope":   ["KRA Goals", "Goal Assignments", "R&R Nominations", "Certifications"],
            "designation":    ["Associate", "Senior Associate", "Professional", "Lead Professional"],
        }.get(missing_field, [])


clarification_options_builder = ClarificationOptionsBuilder()
