"""
Schema-driven suggestion builder.

Reads the live MySQL schema from SchemaManager, detects which KRA capabilities
are present (goals, ratings, departments, completion tracking, etc.), and
generates role-appropriate natural-language query suggestions.

Results are cached by a schema-table-name hash and invalidated automatically
whenever SchemaManager.refresh_schema() is called.
"""

import hashlib
import json
import logging
from typing import Dict, List, Optional

logger = logging.getLogger(__name__)

from app.db.schema_manager import schema_manager  # noqa: E402 — after logger

# ── Keyword sets for table / column matching ──────────────────────────────────

_GOAL_KW       = frozenset({"goal", "kra", "objective", "target", "task"})
_RATING_KW     = frozenset({"rating", "score", "grade", "appraisal", "review",
                             "evaluation", "performance"})
_EMPLOYEE_KW   = frozenset({"employee", "emp", "staff", "user", "member"})
_DEPT_KW       = frozenset({"department", "dept", "division", "team", "unit"})
_STATUS_KW     = frozenset({"status", "state", "stage"})
_COMPLETION_KW = frozenset({"completion", "progress", "percent", "percentage", "complete"})
_QUARTER_KW    = frozenset({"quarter", "quarter_id", "qtr"})
_YEAR_KW       = frozenset({"year", "appraisal_year", "fiscal_year"})


def _hit(name: str, kws: frozenset) -> bool:
    n = name.lower()
    return any(kw in n for kw in kws)


class SuggestionBuilder:
    """Generates and caches role-based query suggestions from the live schema."""

    def __init__(self) -> None:
        self._cache: Optional[Dict[str, List[str]]] = None
        self._cache_hash: str = ""

    # ── Public API ────────────────────────────────────────────────────────────

    def get_suggestions(self, user_role: str) -> List[str]:
        """Return 3 schema-driven query suggestions for the given role."""
        built = self._get_or_build()
        return built.get(user_role) or built["employee"]

    def get_greeting_redirect(self, user_role: str) -> str:
        """Return a short inline suggestion phrase for greeting responses."""
        items = self.get_suggestions(user_role)
        if len(items) >= 2:
            return (
                f"would you like to start with '{items[0]}' "
                f"or explore '{items[1]}'?"
            )
        if items:
            return f"want to start with: {items[0]}?"
        return "want to check your KRA goal progress or performance ratings?"

    def get_off_topic_redirect(self, user_role: str) -> str:
        """Return a short inline suggestion phrase for off-topic redirects."""
        items = self.get_suggestions(user_role)
        if items:
            return f"Can I help you with '{items[0]}' instead?"
        return "Can I help you with your KRA reports instead?"

    def invalidate(self) -> None:
        """Flush the cache — call this after every schema refresh."""
        self._cache = None
        self._cache_hash = ""
        logger.info("[suggestion_builder] cache invalidated")

    # ── Internal ──────────────────────────────────────────────────────────────

    def _get_or_build(self) -> Dict[str, List[str]]:
        schema = schema_manager.get_schema()
        schema_hash = hashlib.md5(
            json.dumps(sorted(schema.keys()), sort_keys=True).encode()
        ).hexdigest()[:12]

        if self._cache is not None and schema_hash == self._cache_hash:
            return self._cache

        caps = self._detect(schema)
        result = self._build(caps)

        self._cache = result
        self._cache_hash = schema_hash
        logger.info(
            f"[suggestion_builder] rebuilt — goals={caps['has_goals']} "
            f"ratings={caps['has_ratings']} departments={caps['has_departments']} "
            f"tables={len(schema)}"
        )
        return result

    def _detect(self, schema: Dict) -> Dict:
        table_names = list(schema.keys())

        goal_tables   = [t for t in table_names if _hit(t, _GOAL_KW)]
        rating_tables = [t for t in table_names if _hit(t, _RATING_KW)]
        dept_tables   = [t for t in table_names if _hit(t, _DEPT_KW)]

        all_cols: List[str] = [
            col["name"].lower()
            for info in schema.values()
            for col in info.get("columns", [])
        ]

        return {
            "has_goals":       bool(goal_tables),
            "has_ratings":     bool(rating_tables),
            "has_departments": bool(dept_tables) or any(_hit(c, _DEPT_KW) for c in all_cols),
            "has_completion":  any(_hit(c, _COMPLETION_KW) for c in all_cols),
            "has_status":      any(_hit(c, _STATUS_KW) for c in all_cols),
            "has_quarter":     any(_hit(c, _QUARTER_KW) for c in all_cols),
            "has_year":        any(_hit(c, _YEAR_KW) for c in all_cols),
        }

    def _build(self, caps: Dict) -> Dict[str, List[str]]:
        time_phrase = (
            "for Q1 2025" if caps["has_quarter"]
            else "this year" if caps["has_year"]
            else ""
        )
        pct_suffix = " with completion percentage" if caps["has_completion"] else ""

        result: Dict[str, List[str]] = {role: [] for role in ("employee", "lead", "manager", "hr")}

        # ── employee ─────────────────────────────────────────────────────────
        if caps["has_goals"]:
            result["employee"].append(
                f"Show my KRA goals {time_phrase}{pct_suffix}".strip()
            )
        if caps["has_ratings"]:
            result["employee"].append(
                ("What is my average performance rating " + time_phrase).strip() + "?"
            )
        if caps["has_status"] and caps["has_goals"]:
            result["employee"].append("List all my in-progress goals for this quarter")

        # ── lead ─────────────────────────────────────────────────────────────
        if caps["has_goals"]:
            result["lead"].append(
                f"Show completion rates for my team members {time_phrase}".strip()
            )
        if caps["has_ratings"]:
            result["lead"].append("Compare Q1 vs Q2 ratings for my reportees")
        if caps["has_status"] and caps["has_goals"]:
            result["lead"].append("List overdue goals for my team members this quarter")

        # ── manager ──────────────────────────────────────────────────────────
        if caps["has_ratings"]:
            result["manager"].append(
                f"Show average KRA scores for all team members {time_phrase}".strip()
            )
        if caps["has_goals"]:
            result["manager"].append("Who has the lowest goal completion rate in Q1 2025?")
        if caps["has_ratings"]:
            result["manager"].append("List all employees with pending appraisals this cycle")

        # ── hr ───────────────────────────────────────────────────────────────
        if caps["has_departments"] and caps["has_ratings"]:
            result["hr"].append("Show average KRA scores by department for 2025")
        if caps["has_ratings"]:
            result["hr"].append("List all employees with ratings below 3 this year")
        if caps["has_goals"] or caps["has_ratings"]:
            result["hr"].append(
                "Generate a full performance summary for the current appraisal cycle"
            )

        # ── Pad each role to at least 3 with generic fallbacks ────────────────
        _fallbacks: Dict[str, List[str]] = {
            "employee": [
                "Show my KRA goals with completion percentage",
                "What is my average performance rating?",
                "List my in-progress goals for this quarter",
            ],
            "lead": [
                "Show my team's goal completion rates",
                "List overdue goals for my reportees",
                "Compare performance ratings for my team this quarter",
            ],
            "manager": [
                "Show average KRA scores for all team members",
                "Who has the lowest goal completion rate?",
                "List all employees with pending appraisals",
            ],
            "hr": [
                "Show average KRA scores by department",
                "List all employees with low ratings",
                "Generate a full performance summary",
            ],
        }
        for role, items in result.items():
            for fb in _fallbacks.get(role, []):
                if len(items) >= 3:
                    break
                if fb not in items:
                    items.append(fb)

        return result


suggestion_builder = SuggestionBuilder()
