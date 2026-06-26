import logging
import re

from app.services.knowledge_loader import knowledge_loader

logger = logging.getLogger(__name__)

# ─── Query-classification regexes ─────────────────────────────────────────────
# These drive which query-hint keys are injected at the end of each prompt.
# All prompt TEXT lives in prompt_knowledge_base.yaml — not here.

# Non-compliance without a grouping keyword → employee-list (Example 1)
_NON_COMPLIANCE_RE = re.compile(r'\bnon[\s\-]?compli', re.IGNORECASE)
_GROUPING_RE = re.compile(
    r'\b(department[\s\-]?wise|by[\s\-]?department|categorized|grouped|summary|count[\s\-]?per)\b',
    re.IGNORECASE,
)

# "how many employees/users/people …" → scalar COUNT
_HOW_MANY_EMPLOYEE_RE = re.compile(
    r'\bhow\s+many\s+(employees?|users?|people|members?|staff)\b',
    re.IGNORECASE,
)

# "goals not filled / missing remarks / unfilled goals"
_GOALS_NOT_FILLED_RE = re.compile(
    r'\b(no\.?\s*of\s+goals?\s+(not\s+(filled|completed|submitted)|missing|unfilled)'
    r'|goals?\s+(not\s+(filled|completed|submitted)|missing|unfilled)'
    r'|missing\s+goals?'
    r'|unfilled\s+goals?'
    r'|goals?\s+not\s+filled)\b',
    re.IGNORECASE,
)

# Explicit "> 0" / "only missing" filter variant
_GOALS_NOT_FILLED_GT0_RE = re.compile(
    r'\b(missing\s*[>＞]\s*0'
    r'|goals?\s+not\s+filled\s*[>＞]\s*0'
    r'|only\s+(employees?\s+with\s+)?(missing|unfilled|not\s+filled)\s+goals?'
    r'|employees?\s+with\s+(missing|unfilled)\s+goals?'
    r'|who\s+have\s+not\s+filled\s+(any\s+)?goals?)\b',
    re.IGNORECASE,
)

# Remark-compliance / completion-rate queries
_COMPLIANCE_METRICS_RE = re.compile(
    r'(?:'
    r'compliance\s*[%﹪％]'
    r'|compliance\s+(?:percent|rate|pct|score)'
    r'|remark\s+(?:completion|compliance)\s*(?:rate|[%﹪％])?'
    r'|filled\s+vs\.?\s*(?:required|total|missing)'
    r'|required\s+(?:goals?\s+)?(?:vs\.?|and)\s+(?:filled|remarks?)'
    r'|compliance\s+gap'
    r'|\d+\s*[%﹪％]\s+compliance'
    r'|streams?\s+(?:below|above|ranked\s+by|with\s+lowest|with\s+highest)\s+.{0,30}compliance'
    r'|executive\s+(?:compliance|kra)\s+(?:summary|report)'
    r'|executive\s+summary\s+by\s+stream'
    r'|remark\s+completion\s+(?:rate|report)'
    r'|missing\s+remarks?\s+per\s+(?:stream|designation|team)'
    r'|compliance\s+by\s+(?:stream|designation|team|department)'
    r'|cross[\s\-]stream\s+compliance'
    r'|overall\s+(?:compliance|kra\s+health)'
    r'|kra\s+health\s+summary'
    r')',
    re.IGNORECASE,
)

# Monthly / trend time-series queries
_TREND_MONTHLY_RE = re.compile(
    r'\b(month[\s\-]?by[\s\-]?month'
    r'|over\s+(the\s+)?(last|past)\s+\d+\s*months?'
    r'|monthly\s+(trend|report|breakdown|completion|compliance|data)'
    r'|trend\s+(over|for|since|from)'
    r'|completion\s+rate\s+(by|per|each)\s+month'
    r'|filled\s+vs\.?\s*missing\s+(over|each|per)\s+month'
    r'|quarter[\s\-]?over[\s\-]?quarter'
    r'|q\d\s+(to|vs\.?)\s+q\d'
    r'|since\s+(jan|january)\s+\d{4}'
    r'|improving|declining'
    r'|month\s+with\s+(highest|lowest|best|worst)\s+.{0,30}(completion|compliance|remark))\b',
    re.IGNORECASE,
)

# Multi-stream filter (Dev or Dev-Data / X and Y streams)
_MULTI_STREAM_RE = re.compile(
    r'\b([\w][\w\-]*\s+or\s+[\w][\w\-]*\s+streams?'
    r'|streams?\s+[\w\-]+\s+(?:and|or)\s+[\w\-]+'
    r'|in\s+(?:both|either)\s+.{0,40}streams?'
    r'|in\s+the\s+[\w\-]+\s+(?:or|and)\s+[\w\-]+\s+streams?'
    r'|streams?\s+(?:include|like|such\s+as)\s+[\w\-]+\s+(?:and|or)\s+[\w\-]+)\b',
    re.IGNORECASE,
)

# Hierarchy / indirect-reports → WITH RECURSIVE required
_HIERARCHY_RE = re.compile(
    r'(?:'
    r'directly\s+(?:or\s+)?indirectly'
    r'|indirect(?:ly)?\s+report'
    r'|reporting\s+(?:tree|hierarchy|chain)'
    r'|all\s+reportees?'
    r'|(?:entire|whole|full)\s+(?:team|hierarchy|org)\s+under'
    r'|org(?:anization(?:al)?)?\s+(?:hierarchy|structure|chart)'
    r'|team\s+structure\s+under'
    r'|who\s+falls?\s+under'
    r'|downline'
    r'|employees?\s+under\s+\w'
    r'|all\s+employees?\s+(?:under|below)\s+\w'
    r'|reportees?\s+(?:under|of|below)\s+\w'
    r')',
    re.IGNORECASE,
)

# Compliance AND completion combined (head-to-head / both metrics)
_DUAL_METRICS_RE = re.compile(
    r'(?:'
    r'head[\s\-]to[\s\-]head'
    r'|compliance\s+and\s+completion'
    r'|completion\s+and\s+compliance'
    r'|both\s+(?:compliance\s+and\s+completion|metrics?)'
    r'|(?:compliance|completion)\s*[&+]\s*(?:completion|compliance)'
    r'|compare\s+.{0,50}\s+on\s+(?:both\s+)?(?:compliance\s+and\s+completion|completion\s+and\s+compliance)'
    r')',
    re.IGNORECASE,
)

# Goal-completion trend (status-based, not remark-based)
_COMPLETION_TREND_RE = re.compile(
    r'(?:'
    r'how\s+.{0,40}completion\s+has\s+changed'
    r'|company[\s\-]wide\s+completion'
    r'|(?:goal\s+)?completion\s+(?:trend|changed?|progress|improvement)'
    r'|(?:goal\s+)?completion\s+(?:since|from)\s+\w'
    r'|(?:goal\s+)?completion\s+(?:rate\s+)?(?:this\s+year|year[\s\-]to[\s\-]date)'
    r'|(?:goal\s+)?completion\s+rate\s+(?:by|per|each)\s+month'
    r'|monthly\s+(?:goal\s+)?completion\s+(?:trend|rate|data|percentage)'
    r'|(?:goals?\s+)?completed\s+(?:per|by|each|over)\s+month'
    r'|(?:completion|goals?\s+completed)\s+(?:jan|january|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)'
    r')',
    re.IGNORECASE,
)

# Quarter-over-quarter stream compliance
_QOQ_STREAM_RE = re.compile(
    r'(?:'
    r'quarter[\s\-]?over[\s\-]?quarter'
    r'|q[\s\-]?o[\s\-]?q'
    r'|quarterly\s+.{0,40}(?:by|per|for|across)\s+.{0,20}streams?'
    r'|quarterly\s+.{0,30}streams?\s+.{0,30}compliance'
    r'|streams?\s+.{0,30}quarterly\s+.{0,30}compliance'
    r')',
    re.IGNORECASE,
)

# ─── Helpers ──────────────────────────────────────────────────────────────────

_UNKNOWN_COLUMN_RE = re.compile(r"Unknown column '([^']+)'", re.IGNORECASE)
_SQL_USED_RE = re.compile(r"\[SQL used:\s*(.*?)\]", re.DOTALL)


def extract_column_hint(error: str) -> str:
    """Return a targeted hint when the error names an unknown column."""
    m = _UNKNOWN_COLUMN_RE.search(error)
    if not m:
        return ""
    bad_ref = m.group(1)
    bad_col = bad_ref.split(".")[-1]
    return (
        f"\n⚠ Column name fix: '{bad_ref}' does not exist."
        f"\n  Look up '{bad_col}' in the DATABASE SCHEMA section above and use the exact name shown there."
        f"\n  Common corrections: email → e_mail | first_name → firstname | last_name → lastname"
        f"\n  Do NOT remove the column — correct its name."
    )


def _extract_last_sql(memory_context: str) -> str:
    matches = _SQL_USED_RE.findall(memory_context)
    return matches[-1].strip() if matches else ""


def _safe(value: str) -> str:
    """Escape curly braces so a value is safe to pass to str.format()."""
    return value.replace("{", "{{").replace("}", "}}")


# ─── PromptBuilder ────────────────────────────────────────────────────────────


class PromptBuilder:

    def build_prompt(
        self,
        user_query: str,
        schema_string: str,
        memory_context: str = "",
        retry_feedback: str = "",
        relationship_type: str = "new_request",
        active_report_context: dict = None,
    ) -> str:
        memory_section = memory_context if memory_context else "No prior conversation."
        is_followup = (relationship_type == "followup")

        # ── System prompt (few-shot + schema + business rules + memory) ──────
        system = knowledge_loader.build_system_prompt(
            few_shot=knowledge_loader.get_few_shot_text(),
            schema=schema_string,
            memory_context=memory_section,
        )

        # ── Follow-up section ────────────────────────────────────────────────
        followup_section = ""
        if is_followup:
            ctx = active_report_context or {}
            base_sql = ""
            if ctx.get("generated_sql") or ctx.get("last_query"):
                followup_section = knowledge_loader.get_template("active_context").format(
                    report_type=ctx.get("report_type", "previous report"),
                    last_query=_safe(ctx.get("last_query", "")),
                    generated_sql=_safe(ctx.get("generated_sql", "")),
                )
                base_sql = ctx.get("generated_sql", "")
            else:
                base_sql = _extract_last_sql(memory_section)

            if base_sql:
                followup_section += knowledge_loader.get_template(
                    "followup_instruction"
                ).format(base_sql=_safe(base_sql))
            else:
                followup_section += knowledge_loader.get_template("followup_fallback")

        # ── Retry section ────────────────────────────────────────────────────
        retry_section = ""
        if retry_feedback:
            retry_section = knowledge_loader.get_template("retry_suffix").format(
                retry_feedback=_safe(retry_feedback)
            )

        # ── Last-line override hints (loaded from knowledge base by key) ─────
        query_hint = ""

        if _NON_COMPLIANCE_RE.search(user_query) and not _GROUPING_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("non_compliance_list")

        if _GOALS_NOT_FILLED_RE.search(user_query) or _GOALS_NOT_FILLED_GT0_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("goals_not_filled")

        if _DUAL_METRICS_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("dual_metrics")

        if _COMPLIANCE_METRICS_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("compliance_metrics")

        if _COMPLETION_TREND_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("completion_trend")

        if _TREND_MONTHLY_RE.search(user_query):
            if _QOQ_STREAM_RE.search(user_query):
                pass  # qoq_stream hint below already covers this branch
            elif _COMPLETION_TREND_RE.search(user_query):
                query_hint += knowledge_loader.get_query_hint("trend_monthly_completion_reminder")
            else:
                query_hint += knowledge_loader.get_query_hint("trend_monthly_general")

        if _QOQ_STREAM_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("qoq_stream")

        if _HOW_MANY_EMPLOYEE_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("how_many_employees")

        if _HIERARCHY_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("hierarchy")

        if _MULTI_STREAM_RE.search(user_query):
            query_hint += knowledge_loader.get_query_hint("multi_stream")

        prompt = f"{system}{followup_section}\n{retry_section}{query_hint}\nUSER QUERY: {user_query}"
        logger.debug(
            "Built prompt (%d chars) relationship=%s followup=%s retry=%s",
            len(prompt), relationship_type, is_followup, bool(retry_feedback),
        )
        return prompt


prompt_builder = PromptBuilder()
