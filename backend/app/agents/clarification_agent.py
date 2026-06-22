"""
KRA Clarification Detector

Detects ambiguous or underspecified KRA-related prompts and returns a
structured clarification request. Runs after intent detection (Track C)
and before the relationship classifier.

Priority order for clarification checks:
  0. Pending clarification answer        → merge original + answer, proceed to SQL
  1. "How are we doing?" style           → ask which KRA metric
  2. Purely vague query ("show report")  → ask which report type
  3. Generic performance report          → ask which performance type
  4. Compliance numbers without scope    → ask compliance scope + period
  5. Comparison intent + streams but no named streams → ask which streams
  6. "Last period" / "previous period"   → ask period (and report type if also vague)
  7. Time-based report without period    → ask period
  8. "Show remarks for the team"         → ask which team + period

Never asks clarification when:
  - User provided a person name, period, specific scope, or stream names
  - Query is a master-data lookup (list streams, list designations, …)
  - Query is a follow-up to an existing report (relationship_classifier handles it)
  - A pending clarification was detected  (merges original prompt + answer)
"""

import logging
import re
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── English stop-words that are NOT stream/entity names ───────────────────────

_ENTITY_STOP_WORDS = frozenset({
    "the", "two", "both", "all", "these", "those", "this", "that",
    "some", "any", "each", "our", "my", "your", "their", "its",
    "me", "us", "them", "him", "her", "we", "i", "you", "they",
    "and", "but", "or", "for", "with", "from", "by", "to", "of", "in",
    "on", "at", "up", "do", "not", "can", "will", "would", "could",
    "should", "may", "might", "shall", "have", "has", "had", "been",
    "is", "are", "was", "were", "be", "get", "got", "give", "show",
    "report", "stream", "streams", "data", "numbers", "results",
})

_PERIOD_STOP_WORDS = frozenset({
    "q1", "q2", "q3", "q4", "quarter", "month", "year",
    "jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
    "oct", "nov", "dec", "january", "february", "march", "april",
    "june", "july", "august", "september", "october", "november",
    "december", "fy", "ytd",
})


# ── Patterns: truly vague queries (no identifiable KRA report intent) ─────────

_VAGUE_QUERY_PATTERNS = [
    # "What's the overall status / health?"
    re.compile(
        r"^\s*what('s|is)\s+(the\s+)?(overall\s+)?"
        r"(status|health|progress|standing|overview|picture)\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
    # "Show me the report" / "Give me the data" / "Pull up the results"
    re.compile(
        r"^\s*(show(\s+me)?|give(\s+me)?|pull\s+up|generate|provide|get(\s+me)?|fetch)"
        r"\s+(the\s+|a\s+|my\s+)?"
        r"(report|data|numbers|stats|statistics|metrics|results|summary|overview)"
        r"\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
    # "Give me the usual / standard / default report"
    re.compile(
        r"^\s*(show(\s+me)?|give(\s+me)?|generate|get(\s+me)?)"
        r"\s+(the\s+|a\s+)?"
        r"(usual|regular|default|standard|typical)\s*"
        r"(report|data|numbers|summary|results)?\s*[.!?]*\s*$",
        re.IGNORECASE,
    ),
]

# ── "How are we doing?" style KRA health checks ───────────────────────────────

_VAGUE_HEALTH_RE = re.compile(
    r"\bhow\s+(are\s+we\s+doing|do\s+we\s+(stand|compare))\b"
    r"|\bhow'?s\s+our\s+(performance|progress)\b",
    re.IGNORECASE,
)

# ── Time-based reports that require a period ──────────────────────────────────

_TIME_BASED_RE = re.compile(
    r"\b("
    r"compliance|non[\s\-]?compliance|non[\s\-]?complian[ct]"
    r"|missing\s+remarks?|no\s+remarks?"
    r"|at[\s\-]risk"
    r"|performance\s+report|performance"
    r"|feedback\s+report|feedback"
    r"|kra\s+summary"
    r")\b",
    re.IGNORECASE,
)

# ── Concrete period terms: if present, the period is already specified ────────

_PERIOD_RE = re.compile(
    r"\b("
    r"january|february|march|april|may|june|july|august"
    r"|september|october|november|december"
    r"|jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec"
    r"|q1|q2|q3|q4|quarter"
    r"|this\s+month|last\s+month|previous\s+month"
    r"|this\s+quarter|last\s+quarter|previous\s+quarter"
    r"|this\s+year|last\s+year|previous\s+year"
    r"|this\s+fy|last\s+fy"
    r"|today|this\s+week|last\s+week"
    r"|20\d\d"
    r"|ytd|year[\s\-]to[\s\-]date"
    r")\b",
    re.IGNORECASE,
)

# ── "last period" / "previous period" without a concrete period name ──────────

_LAST_PERIOD_RE = re.compile(
    r"\b(last|previous|prior)\s+period\b",
    re.IGNORECASE,
)

# ── Generic performance report without a sub-type ────────────────────────────

_PERFORMANCE_REPORT_RE = re.compile(
    r"\b(show|give|generate|pull|get|provide|display)"
    r"\s*(me\s+)?(the\s+|a\s+|my\s+)?"
    r"(performance\s+report|performance\s+data|performance\s+numbers|performance\s+summary)\b",
    re.IGNORECASE,
)

# ── "Performance report" with a clear sub-type / scope ───────────────────────

_PERFORMANCE_SUBTYPE_RE = re.compile(
    r"\b(employee|stream|team|company|individual|department|designation)\b",
    re.IGNORECASE,
)

# ── Compliance numbers / stats without scope ──────────────────────────────────

_COMPLIANCE_VAGUE_RE = re.compile(
    r"\b(compliance\s+numbers?|compliance\s+stats?|compliance\s+data"
    r"|compliance\s+figures?|pull\s+(up\s+)?the\s+compliance)\b",
    re.IGNORECASE,
)

# ── Comparison intent involving streams ───────────────────────────────────────

_COMPARE_INTENT_RE = re.compile(
    r"(?:"
    r"\b(?:compare|comparison|versus|vs\.?|between|head[\s\-]to[\s\-]head)\b"
    r".{0,80}"
    r"\bstreams?\b"
    r")"
    r"|"
    r"(?:"
    r"\bstreams?\b"
    r".{0,80}"
    r"\b(?:compare|comparison|versus|vs\.?|between|head[\s\-]to[\s\-]head)\b"
    r")",
    re.IGNORECASE,
)

# ── Two named entities joined by "and / vs / versus / &" ─────────────────────
# Captures "QA and Dev", "Development vs Design", etc.

_TWO_NAMED_ENTITIES_RE = re.compile(
    r"\b([A-Za-z][A-Za-z0-9\-]{1,30})\s+(?:and|&|vs\.?|versus)\s+([A-Za-z][A-Za-z0-9\-]{1,30})\b",
    re.IGNORECASE,
)

# ── Topic marker in comparison queries ("for compliance", "on performance") ───

_COMPARE_TOPIC_RE = re.compile(
    r"\bfor\s+(compliance|non[\s\-]?compliance|performance|goals?|remarks?|kra\s+summary)\b",
    re.IGNORECASE,
)

# ── Scope terms: reduce need for scope clarification ─────────────────────────

_SCOPE_TERMS_RE = re.compile(
    r"\b("
    r"company[\s\-]?wide|all\s+employees?|all\s+teams?|all\s+streams?"
    r"|stream[\s\-]?wise|department[\s\-]?wise|team[\s\-]?wise"
    r"|employee[\s\-]?wise|lead[\s\-]?wise|designation[\s\-]?wise"
    r"|by\s+stream|by\s+department|by\s+team|by\s+employee"
    r")\b",
    re.IGNORECASE,
)

# ── Person-name indicators ────────────────────────────────────────────────────

_PERSON_IN_QUERY_RE = re.compile(
    r"\b(for|of|by|under|team\s+of|from)\s+[A-Z][a-z]{1,}(?:\s+[A-Z][a-z]+)?\b",
)
_EMPLOYEE_ID_RE = re.compile(r"\b(EMP|emp)\d+\b", re.IGNORECASE)

# ── All-employees scope ("all employees", "everyone") ────────────────────────

_ALL_SCOPE_RE = re.compile(
    r"\ball\s+(employees?|teams?|the\s+team|staff|people|members?)\b",
    re.IGNORECASE,
)

# ── Vague team remarks ────────────────────────────────────────────────────────

_TEAM_VAGUE_RE = re.compile(
    r"\b(show|get|give|pull|display|list)\s*(me\s+)?"
    r"(all\s+)?(the\s+)?remarks?\s+(for|of)\s+the\s+team\b",
    re.IGNORECASE,
)

# ── Generic "the report" with no type specified ───────────────────────────────

_GENERIC_REPORT_RE = re.compile(r"\bthe\s+report\b", re.IGNORECASE)

_KNOWN_REPORT_TYPE_RE = re.compile(
    r"\b(compliance|non[\s\-]?compliance|performance|kra|goal|remark"
    r"|feedback|at[\s\-]risk|missing\s+remark|summary)\b",
    re.IGNORECASE,
)


# ── Option lists ──────────────────────────────────────────────────────────────

_REPORT_TYPE_OPTIONS = [
    "KRA goals report",
    "Remark compliance report",
    "Missing remarks report",
    "At-risk goals report",
    "Goal completion report",
    "Team performance report",
    "Employee performance report",
]

_PERFORMANCE_TYPE_OPTIONS = [
    "Employee performance report",
    "Stream-wise performance report",
    "Team performance report",
    "Company-wide performance report",
]

_KRA_METRIC_OPTIONS = [
    "Goal completion",
    "Remark compliance",
    "At-risk goals",
    "Missing remarks",
    "Overall KRA health",
]

_COMPLIANCE_SCOPE_OPTIONS = [
    "Company-wide compliance",
    "Stream-wise compliance",
    "Team-wise compliance",
    "Employee-wise compliance",
]

_PERIOD_OPTIONS = [
    "This month",
    "Last month",
    "Current quarter",
    "Last quarter",
    "This year",
    "Custom date range",
]


# ─────────────────────────────────────────────────────────────────────────────
# Public class
# ─────────────────────────────────────────────────────────────────────────────

class KRAClarificationDetector:
    """
    Schema-grounded clarification detector for KRA report prompts.

    Returns:
        {
            "needs_clarification":    bool,
            "reason":                 str,
            "question":               str,
            "options":                List[str],
            "missing_slots":          List[str],
            "is_clarification_answer": bool,
            "merged_prompt":          str | None,
        }
    """

    # ── Public entry point ────────────────────────────────────────────────────

    def detect(
        self,
        user_message: str,
        user_id: str = "",        # kept for API compatibility
        chat_session_id: str = "", # kept for API compatibility
    ) -> Dict[str, Any]:

        # Normalise: strip whitespace; keep original for cased-regex checks.
        original = user_message.strip()
        lower = original.lower().rstrip(".,!?")   # strip trailing punctuation for pattern matching

        # ── 1. "How are we doing?" ────────────────────────────────────────────
        if _VAGUE_HEALTH_RE.search(lower):
            return self._clarify(
                reason="missing_kra_metric",
                question="Which KRA metric do you want to review?",
                options=_KRA_METRIC_OPTIONS,
                missing_slots=["kra_metric", "period"],
            )

        # ── 2. Purely vague query ─────────────────────────────────────────────
        if self._is_vague_query(lower):
            return self._clarify(
                reason="missing_report_type",
                question="Which report would you like to generate?",
                options=_REPORT_TYPE_OPTIONS,
                missing_slots=["report_type"],
            )

        # ── 3. Generic performance report (no sub-type) ───────────────────────
        if self._is_vague_performance_report(lower, original):
            return self._clarify(
                reason="missing_performance_type",
                question="Which performance report do you want to generate?",
                options=_PERFORMANCE_TYPE_OPTIONS,
                missing_slots=["report_type", "period"],
            )

        # ── 4. Compliance numbers without scope ───────────────────────────────
        if _COMPLIANCE_VAGUE_RE.search(lower) and not _SCOPE_TERMS_RE.search(lower):
            return self._clarify(
                reason="missing_compliance_scope",
                question="Which compliance numbers do you want to view, and for which period?",
                options=_COMPLIANCE_SCOPE_OPTIONS,
                missing_slots=["scope", "period"],
            )

        # ── 5. Comparison involving streams but no specific stream names ───────
        if self._is_comparison_without_named_streams(lower, original):
            stream_options = self._get_stream_options()
            topic = self._extract_comparison_topic(lower)
            question = f"Which two streams do you want to compare{topic}?"
            return self._clarify(
                reason="missing_stream_names",
                question=question,
                options=stream_options,
                missing_slots=["stream_1", "stream_2"],
            )

        # ── 6. "Last period" / "previous period" without concrete period ──────
        # Skip this check when a concrete period (e.g. "last month") is also present —
        # that happens in merged clarification answers like "… last period … last month".
        if _LAST_PERIOD_RE.search(lower) and not _PERIOD_RE.search(lower):
            # If report type is also vague ("the report for last period"), ask both.
            if _GENERIC_REPORT_RE.search(lower) and not _KNOWN_REPORT_TYPE_RE.search(lower):
                return self._clarify(
                    reason="ambiguous_report_and_period",
                    question=(
                        "Which report do you want to generate, "
                        "and what do you mean by 'last period'?"
                    ),
                    options=_REPORT_TYPE_OPTIONS + _PERIOD_OPTIONS,
                    missing_slots=["report_type", "period"],
                )
            return self._clarify(
                reason="ambiguous_period",
                question="What do you mean by 'last period'? Which period should be used?",
                options=_PERIOD_OPTIONS,
                missing_slots=["period"],
            )

        # ── 7. Time-based report without a period and without enough scope ─────
        if _TIME_BASED_RE.search(lower) and not _PERIOD_RE.search(lower):
            if not self._is_scoped_enough(original, lower):
                report_name = self._extract_report_name(lower)
                return self._clarify(
                    reason="missing_period",
                    question=f"Which period do you want for the {report_name}?",
                    options=_PERIOD_OPTIONS,
                    missing_slots=["period"],
                )

        # ── 8. "Show remarks for the team" without a team name ────────────────
        if _TEAM_VAGUE_RE.search(lower):
            return self._clarify(
                reason="missing_team",
                question=(
                    "Which team or lead do you want to view remarks for, "
                    "and which period should be used?"
                ),
                options=[
                    "All teams",
                    "Specific team lead — please provide their name",
                ],
                missing_slots=["team_or_lead", "period"],
            )

        # No clarification needed.
        return self._no_clarification()

    # ── Pattern helpers ───────────────────────────────────────────────────────

    def _is_vague_query(self, lower: str) -> bool:
        # "How are we doing" is handled separately — never treat it as vague.
        if _VAGUE_HEALTH_RE.search(lower):
            return False
        return any(p.match(lower) for p in _VAGUE_QUERY_PATTERNS)

    def _is_vague_performance_report(self, lower: str, original: str = "") -> bool:
        if not _PERFORMANCE_REPORT_RE.search(lower):
            return False
        # Already scoped → specific enough.
        if _SCOPE_TERMS_RE.search(lower):
            return False
        # Named person → check original (cased) string because regex uses [A-Z].
        if _PERSON_IN_QUERY_RE.search(original or lower):
            return False
        # Contains a performance sub-type keyword → specific enough.
        if _PERFORMANCE_SUBTYPE_RE.search(lower):
            return False
        return True

    def _is_comparison_without_named_streams(self, lower: str, original: str) -> bool:
        """
        Returns True when the query expresses a stream comparison but does NOT
        provide two specific stream names.

        "Compare the two streams"     → True   (no names)
        "Compare the two streams for compliance" → True (no names)
        "Compare QA and Dev streams"  → False  (names present)
        """
        if not _COMPARE_INTENT_RE.search(lower):
            return False
        # If two specific named entities are detected, the user has already provided them.
        return not self._has_two_named_streams(original)

    def _has_two_named_streams(self, text: str) -> bool:
        """
        True when text contains two entity names joined by 'and / vs / versus / &'
        that are neither stop-words nor time-period indicators.
        """
        m = _TWO_NAMED_ENTITIES_RE.search(text)
        if not m:
            return False
        ent1 = m.group(1).lower().strip()
        ent2 = m.group(2).lower().strip()
        # Reject generic English words and number-words (e.g. "the two", "both and these")
        if ent1 in _ENTITY_STOP_WORDS or ent2 in _ENTITY_STOP_WORDS:
            return False
        # Reject time-period words (e.g. "Q1 and Q2")
        if ent1 in _PERIOD_STOP_WORDS or ent2 in _PERIOD_STOP_WORDS:
            return False
        return True

    def _extract_comparison_topic(self, lower: str) -> str:
        """Return ' for <topic>' when the comparison query mentions a specific topic."""
        m = _COMPARE_TOPIC_RE.search(lower)
        if m:
            return f" for {m.group(1)}"
        return ""

    def _is_scoped_enough(self, original: str, lower: str) -> bool:
        """True when the query already has enough scope to skip period clarification."""
        if _PERSON_IN_QUERY_RE.search(original):
            return True
        if _EMPLOYEE_ID_RE.search(original):
            return True
        if _ALL_SCOPE_RE.search(lower):
            return True
        # Scope qualifiers like company-wide, stream-wise, team-wise, etc.
        # indicate the report can be generated without a period filter.
        if _SCOPE_TERMS_RE.search(lower):
            return True
        # Clear performance sub-type (employee/team/stream/company) already scopes it.
        if _PERFORMANCE_SUBTYPE_RE.search(lower):
            return True
        return False

    def _extract_report_name(self, lower: str) -> str:
        if re.search(r"\bnon[\s\-]?compli", lower):
            return "non-compliance report"
        if re.search(r"\bcomplian", lower):
            return "compliance report"
        if re.search(r"\bmissing\s+remarks?\b", lower):
            return "missing remarks report"
        if re.search(r"\bat[\s\-]risk\b", lower):
            return "at-risk goals report"
        if re.search(r"\bperformance\b", lower):
            return "performance report"
        if re.search(r"\bfeedback\b", lower):
            return "feedback report"
        return "report"

    # ── DB-backed stream options ───────────────────────────────────────────────

    def _get_stream_options(self) -> List[str]:
        """
        Fetch active stream names from the tags table.
        Falls back to a static list if the DB call fails.
        Schema: tags.tag, recommended_filter: tags.is_active = 1
        """
        try:
            from app.db.connection import db_manager
            from sqlalchemy import text

            with db_manager.engine.connect() as conn:
                for query in (
                    "SELECT DISTINCT tag FROM tags WHERE is_active = 1 ORDER BY tag LIMIT 20",
                    "SELECT DISTINCT tag FROM tags ORDER BY tag LIMIT 20",
                ):
                    try:
                        rows = conn.execute(text(query))
                        options = [r[0] for r in rows if r[0]]
                        if options:
                            return options
                    except Exception:
                        continue
        except Exception as exc:
            logger.warning("[clarification] stream fetch failed: %s", exc)
        return ["QA", "Development", "DevOps", "Design", "Management"]

    # ── Pending-clarification state ───────────────────────────────────────────

    def _get_pending_clarification(
        self, user_id: str, chat_session_id: str
    ) -> Optional[Dict[str, Any]]:
        if not user_id:
            return None
        try:
            from app.services.context_manager import active_report_context_manager

            ctx = active_report_context_manager.get(user_id, chat_session_id)
            if ctx.get("pending_clarification"):
                return ctx
        except Exception as exc:
            logger.warning("[clarification] context read failed: %s", exc)
        return None

    # ── Prompt merging ────────────────────────────────────────────────────────

    def _merge_prompt(
        self,
        original_prompt: str,
        clarification_answer: str,
        missing_slots: Optional[List[str]] = None,
    ) -> str:
        """
        Combine the original vague prompt with the user's clarification answer.

        Merging rules (applied in order):
          1. Full sentence answer (≥ 5 words) → use as-is.
          2. Stream comparison slots → rebuild "Compare <answer> streams."
          3. team_or_lead / team_lead_name → replace "the team" with scope.
          4. Period-only slot → append "for <answer>".
          5. Fallback → "<original> — <answer>"
        """
        original = original_prompt.strip().rstrip(".,!?")
        answer = clarification_answer.strip().rstrip(".,!?")

        if not original:
            return answer

        # 1. Full sentence answer → use directly.
        if len(answer.split()) >= 5:
            return answer

        slots = missing_slots or []

        # 2. Comparison answer: reconstruct the comparison query with the named streams.
        if "stream_1" in slots or "stream_2" in slots:
            return f"Compare {answer} streams."

        # 3. Team/lead slot: replace "the team" in original so _TEAM_VAGUE_RE never
        #    re-fires on the merged string.
        if "team_or_lead" in slots or "team_lead_name" in slots:
            answer_lower = answer.lower()
            if re.search(r"\ball\b", answer_lower):
                replacement = "all teams"
            else:
                replacement = f"{answer}'s team"
            merged = re.sub(r"\bthe\s+team\b", replacement, original, flags=re.IGNORECASE)
            if merged != original:
                return merged
            # "the team" not found verbatim — append gracefully.
            return f"{original} for {replacement}"

        # 4. Period-only clarification → "for <period>".
        if len(slots) == 1 and slots[0] == "period":
            return f"{original} for {answer}"

        # 5. Generic fallback.
        return f"{original} — {answer}"

    # ── Return helpers ────────────────────────────────────────────────────────

    def _clarify(
        self,
        reason: str,
        question: str,
        options: List[str],
        missing_slots: List[str],
    ) -> Dict[str, Any]:
        return {
            "needs_clarification": True,
            "reason": reason,
            "question": question,
            "options": options,
            "missing_slots": missing_slots,
            "is_clarification_answer": False,
            "merged_prompt": None,
        }

    def _no_clarification(
        self,
        is_clarification_answer: bool = False,
        merged_prompt: Optional[str] = None,
    ) -> Dict[str, Any]:
        return {
            "needs_clarification": False,
            "reason": "",
            "question": "",
            "options": [],
            "missing_slots": [],
            "is_clarification_answer": is_clarification_answer,
            "merged_prompt": merged_prompt,
        }


# ── Module-level singleton ────────────────────────────────────────────────────
kra_clarification_detector = KRAClarificationDetector()
