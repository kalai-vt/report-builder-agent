import json
import logging
import re
from typing import Any, Dict, Optional, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.clarification_options import clarification_options_builder
from app.services.suggestion_builder import suggestion_builder

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# System prompt template                                                        #
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT = """You are an intent classifier for a KRA (Key Result Area) AI report builder system.
Classify every user message into one of FOUR tracks and return ONLY valid JSON.

USER CONTEXT:
- User Role: {user_role}
- Clarification Round: {clarification_round}  (if >= 2 you MUST return track="clear")
- Prior Follow-up Asked: {prior_followup}

CONVERSATION HISTORY (most recent exchanges):
{memory_context}

KRA SCHEMA SUMMARY (for assessing query completeness):
{schema_summary}

=== FOUR TRACKS ===

TRACK A — "greeting":
Message is a social/conversational opener with NO data request.
Triggers: hi, hello, hey, good morning/afternoon/evening, how are you, thanks, thank you,
         bye, goodbye, ok, great, "are you there", questions about your capabilities
         ("what can you do", "who are you", "are you ChatGPT").
Special: "thanks"/"ok"/"great" AFTER a report → Track A (respond warmly, offer next actions).
Do NOT use for messages that also mention goals, KRA, performance, ratings, or employees.

Response rule: 1 warm sentence + 1 specific KRA report suggestion. Max 2 sentences. NEVER ask
open-ended "how can I help" — suggest something specific based on user role.

TRACK B — "off_topic":
Message has NO connection to KRA reports, employee goals, appraisals, ratings, performance data,
or the organisational/HR master data that supports the KRA system.
DO NOT classify as off_topic if the message mentions: goals, KRA, ratings, performance, appraisal,
employees, reports, completion, objectives, productivity, any business metric, streams, designations,
departments, grades, roles, locations, employee attributes, organisational structure, master data,
or any field that exists in the KRA/HR database — those are C or D.

FOLLOW-UP OVERRIDE (highest priority): If the Conversation History above contains a previous
KRA report query and the current message is a short modification/refinement such as "add X",
"include X", "also show X", "remove X", "filter by X", "sort by X", "group by X", "with X",
"show only X", "exclude X", "now add X", or similar — it is ALWAYS track="clear".
NEVER classify such messages as off_topic.

Response rule: polite decline in 1 sentence + suggest ONE KRA report action.
Template: "I'm only able to help with KRA report building! [Suggest one of the examples below]."

TRACK C — "incomplete":
Use ONLY when a KRA-related query CANNOT produce valid SQL because the target data is genuinely
ambiguous across multiple schema tables — NOT because a value might not exist in the database.

━━━ SQL-FIRST RULE (non-negotiable) ━━━
Before classifying as "incomplete", ask: "Can SQL be generated from the available schema?"
  • If YES → use track="clear" — even if the column value might not exist in the database.
  • If NO  → use track="incomplete" for the ONE most blocking reason only.

NEVER use Track C for any of these — they are ALWAYS track="clear":
  ✗ User provided a specific value (name, status, stream, designation, skill, etc.)
      "Show employees in Sales stream"      → WHERE stream='Sales'          (clear)
      "Show employees in Technology"        → WHERE stream='Technology'     (clear)
      "Show goals with status Completed"    → WHERE status='Completed'      (clear)
      "Show certifications for Pending"     → WHERE status='Pending'        (clear)
      "Show skills approved"                → WHERE status='approved'       (clear)
  ✗ One unambiguous table covers the request
      "Show feedback"         → FROM user_feedback          (clear)
      "Show skills"           → FROM skills                 (clear)
      "Show certifications"   → FROM certificates           (clear)
      "Show badges"           → FROM user_badges            (clear)
      "Show goals"            → FROM user_goal_mapping      (clear)
      "Show notifications"    → FROM notification_bell      (clear)
      "Show recommendations"  → FROM recommendations        (clear — show both directions)
  ✗ User already provided date/month/year/quarter in the prompt
      "April 2026 goals"      → YEAR(…)=2026 AND MONTH(…)=4  (clear)
      "Q1 2025 performance"   → YEAR(…)=2025 + quarter filter  (clear)
      "Last month report"     → date filter from current date  (clear)

Use track="incomplete" ONLY for:
  ✓ Multiple primary tables are equally valid — genuinely ambiguous which to use.
      "Show approvals" → could be certification_approvals, remark_approvals,
                         approval_history, or rnr_approval_actions  → ask which type.
  ✓ Query says "a specific manager" or "particular manager" without naming them.
      "Show goals for a specific manager" → ask which manager.
  ✓ The subject is so vague that no table can be identified.
      "Show data" → ask what type of data.

――― TIME PERIOD RULE (ABSOLUTE) ―――
NEVER set missing_field="time_period" if the user's message contains ANY of:
  date, month, year, quarter, Q1/Q2/Q3/Q4, week, weekly, overdue, due,
  today, yesterday, deadline, annual, fiscal, between, recent, trend,
  January/February/March/April/May/June/July/August/September/October/November/December,
  Jan/Feb/Mar/Apr/Jun/Jul/Aug/Sep/Oct/Nov/Dec,
  any 4-digit year (2020–2029),
  this/last/current/previous + week/month/quarter/year.

――― MANAGER SCOPE ―――
Ask ONLY when the query explicitly says "a specific manager" or "particular manager"
without naming one.
"Show goals assigned by manager" (no "specific") → track="clear", group ALL managers.
"Show goals for a specific manager" → ask which manager (missing_field="manager_scope").

――― EMPLOYEE SCOPE ―――
Ask ONLY when a manager/lead/hr role asks about someone's data with no clarity on who.
NEVER ask when role=employee.

――― STATUS FILTER ―――
Ask ONLY when status is truly blocking AND no status implied. Almost never needed.

――― METRIC / SCHEMA SCOPE ―――
Ask ONLY when the metric or module is completely unresolvable from the schema.

Priority order — ask the ONE most blocking:
  1. schema_scope   — multiple valid primary tables (e.g. bare "show approvals")
  2. manager_scope  — explicitly "specific manager" but no name
  3. employee_scope — non-employee role, scope completely unclear
  4. status_filter  — genuinely blocking
  5. time_period    — ONLY when time keyword is in query AND period is ambiguous
  6. metric         — completely vague subject

If clarification_round >= 2 → MUST return track="clear" regardless.
NEVER repeat the prior_followup question — choose the next priority field.
NEVER ask employee_scope when role=employee.

TRACK D — "clear":
Message has enough info to generate SQL, OR clarification_round >= 2 (force clear).
A message is CLEAR when: subject known + table identifiable + SQL generatable.
Time period is NOT required for listing queries — omit date filter and return all rows.
User-provided values (stream, designation, status, name) → always use in WHERE clause.
Write enriched_prompt as ONE precise sentence using KRA schema terminology.

ALWAYS track="clear" — SQL is generatable:
  - "Show employees in [stream/designation/role/value]"  → WHERE col='value'
  - "Show goals [assigned by manager / by team / all]"   → from user_goal_mapping
  - "Show feedback"                                       → FROM user_feedback
  - "Show skills [for all employees]"                    → FROM skills
  - "Show certifications [all/pending/approved]"         → FROM certificates
  - "Show badges [all/awarded]"                          → FROM user_badges
  - "Show notifications"                                 → FROM notification_bell
  - "Show recommendations"                               → FROM recommendations (all)
  - "[Report] for [month] [year]"                        → date-filtered SQL
  - "Employee productivity report"                       → Productivity metrics
  - "List employees [and their Y]"                       → All employees with Y
  - "Show KRA progress [for all/team]"                   → KRA progress for all in scope
  - "List/show X by Y"                                   → Aggregate X grouped by Y

MASTER DATA LOOKUPS are ALWAYS track="clear":
  - "What designations are available?"   → enriched_prompt: "List all designations"
  - "Show me all employee streams"       → enriched_prompt: "List all streams from user_table"
  - "What departments exist?"            → enriched_prompt: "List all departments"

=== SCHEMA-DERIVED EXAMPLES FOR THIS USER ===

{role_examples}

=== LIVE DATABASE OPTIONS (use ONLY these in follow_up_options — never invent) ===
time_period options     : {time_period_options}
employee_scope options  : {employee_scope_options}
manager_scope options   : {manager_scope_options}
status_filter options   : {status_filter_options}
metric options          : {metric_options}
schema_scope options    : {schema_scope_options}
designation options     : {designation_options}

=== OUTPUT FORMAT — return ONLY this JSON, no markdown, no text outside the braces ===
{{
  "track": "greeting|off_topic|incomplete|clear",
  "confidence": 0.95,
  "greeting_message": "string or null",
  "off_topic_reason": "general_knowledge|unrelated|personal|null",
  "polite_block_message": "string or null",
  "missing_field": "time_period|employee_scope|manager_scope|metric|status_filter|schema_scope|null",
  "follow_up_question": "string or null",
  "follow_up_options": [],
  "enriched_prompt": "string or null",
  "extracted_filters": {{}},
  "reasoning": "brief one-sentence explanation"
}}

CRITICAL RULES:
1. Return ONLY valid JSON. No text before or after the JSON object.
2. clarification_round >= 2 → track MUST be "clear", enriched_prompt MUST be set.
3. track="greeting" → greeting_message MUST be set (warm reply + one schema-based suggestion, ≤2 sentences).
4. track="off_topic" → polite_block_message MUST be set (1-sentence decline + 1 suggestion).
5. track="incomplete" → follow_up_question MUST be set.
6. track="clear" → enriched_prompt MUST be a complete, specific sentence.
7. NEVER ask employee_scope when role=employee.
8. NEVER repeat the prior_followup question.
9. NEVER invent column values — if user provided a value, use it; let DB filter.
10. MASTER DATA QUERIES are ALWAYS track="clear".
11. FOLLOW-UP REFINEMENTS are ALWAYS track="clear".
12. USER-PROVIDED VALUES in any column (stream, designation, status, skill, category, etc.)
    make the query ALWAYS track="clear" — generate SQL using that value in WHERE clause.
    Do NOT ask clarification about whether the value exists. Let the database determine this.
"""


class IntentDetectorAgent:
    def __init__(self) -> None:
        self._llm: Optional[ChatOpenAI] = None

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY is not set. Add it to your .env file.")
            self._llm = ChatOpenAI(
                model=settings.LLM_MODEL,
                temperature=0.0,
                openai_api_key=settings.OPENAI_API_KEY,
                max_tokens=600,
                request_timeout=30,
            )
        return self._llm

    # ── Continuation / follow-up detection ───────────────────────────────────
    _CONTINUATION_RE = re.compile(
        r"^\s*("
        r"add|include|also\s+show|also\s+include|also\s+add|also\s+display|"
        r"remove|exclude|don'?t\s+show|hide|"
        r"filter\s+(by|for|on|to)|filter\s+(?!redirect)"
        r"|sort\s+by|order\s+by|group\s+by|"
        r"only\s+show|show\s+only|just\s+show|"
        r"with\s+\w|and\s+also|now\s+(show|add|include|filter|sort|group)|"
        r"update\s+(the\s+)?(report|query|result)|change\s+(the\s+)?(report|query)|"
        r"narrow\s+(down|to|by)|limit\s+to|break\s+down\s+by|"
        r"summarize\s+by|count\s+by|what\s+about\s+adding"
        r")",
        re.IGNORECASE,
    )

    def _is_continuation(self, message: str) -> bool:
        return bool(self._CONTINUATION_RE.match(message))

    def _continuation_response(self, user_message: str) -> Dict[str, Any]:
        return {
            "track": "clear",
            "confidence": 0.98,
            "greeting_message": "",
            "off_topic_reason": None,
            "polite_block_message": None,
            "missing_field": None,
            "follow_up_question": None,
            "follow_up_options": [],
            "enriched_prompt": user_message,
            "extracted_filters": {},
            "reasoning": "Continuation/refinement of previous report — bypassed LLM classification",
        }

    # ── Master-data lookup detection ──────────────────────────────────────────
    _MASTER_DATA_KEYWORDS: List[str] = [
        "designation", "designations",
        "stream", "streams",
        "department", "departments",
        "grade", "grades",
        "role", "roles",
        "location", "locations",
        "employee type", "employee types",
    ]
    _MASTER_DATA_VERBS = re.compile(
        r"\b(list|show|get|fetch|display|what|which|available|exist|are there)\b",
        re.IGNORECASE,
    )

    def _is_master_data_query(self, message: str) -> bool:
        lower = message.lower()
        has_keyword = any(kw in lower for kw in self._MASTER_DATA_KEYWORDS)
        has_verb = bool(self._MASTER_DATA_VERBS.search(message))
        return has_keyword and has_verb

    def _master_data_response(self, message: str) -> Dict[str, Any]:
        return {
            "track": "clear",
            "confidence": 1.0,
            "greeting_message": "",
            "off_topic_reason": None,
            "polite_block_message": None,
            "missing_field": None,
            "follow_up_question": None,
            "follow_up_options": [],
            "enriched_prompt": message,
            "extracted_filters": {},
            "reasoning": "Master data lookup — forced to clear without LLM classification",
        }

    # ── Date-context detection ────────────────────────────────────────────────
    # time_period clarification is ONLY valid when the query contains NONE of these.
    # If ANY match → the user already specified the time period → do not ask.
    _DATE_KEYWORDS_RE = re.compile(
        r"\b("
        # Generic date/period words
        r"date|month|year|quarter|q[1-4]|weekly|week|overdue|due|"
        r"target\s+date|assigned\s+date|completed\s+date|between|recent|"
        r"this\s+(week|month|quarter|year)|last\s+(week|month|quarter|year)|"
        r"previous|trend|period|annual|fiscal|today|yesterday|deadline|"
        r"days?\s+ago|current\s+(month|quarter|year)|prior\s+(month|quarter|year)|"
        # Full month names
        r"january|february|march|april|may|june|july|august|"
        r"september|october|november|december|"
        # Abbreviated month names
        r"jan|feb|mar|apr|jun|jul|aug|sep|sept|oct|nov|dec|"
        # 4-digit year patterns (2020–2029)
        r"20[2-9][0-9]"
        r")\b",
        re.IGNORECASE,
    )

    # ── Self-reference detection ──────────────────────────────────────────────
    # Queries that reference the logged-in user → skip entity-scope clarification.
    _SELF_REFERENCE_RE = re.compile(
        r"\b(my|mine|i\s+want|i\s+need|for\s+me|myself)\b",
        re.IGNORECASE,
    )

    # ── Named-person detection ────────────────────────────────────────────────
    # "report to Baskar", "managed by Sarah" → specific person already named → clear.
    _NAMED_PERSON_RE = re.compile(
        r"\b(?:"
        r"report(?:ing|s)?\s+to|directly\s+report(?:ing|s)?\s+to|"
        r"under\s+(?:the\s+)?(?:management\s+of\s+)?|"
        r"managed\s+by|assigned\s+by|subordinates?\s+of|"
        r"reportees?\s+of|team\s+of"
        r")\s+(\w+)",
        re.IGNORECASE,
    )
    _NON_PERSON_WORDS: frozenset = frozenset({
        "all", "any", "the", "a", "an", "some", "specific", "particular",
        "manager", "managers", "lead", "leads", "employee", "employees",
        "him", "her", "them", "everyone", "anyone", "someone", "nobody",
        "senior", "junior", "team", "group", "department", "every", "each",
        "other", "another", "certain", "given",
    })

    # ── Schema-grounded entity-scope clarification patterns ───────────────────
    # ONLY patterns where SQL genuinely cannot be generated (multiple tables /
    # fundamentally ambiguous scope). Checked BEFORE the LLM.
    # Single-table queries (feedback, skills, certs, badges, goals) are NOT here —
    # they can generate SQL directly and must route to track="clear".
    _ENTITY_SCOPE_CLARIFICATIONS: List[Dict] = [
        # Bare "show approvals" — 4 different approval tables, truly ambiguous
        {
            "patterns": [
                r"^\s*(show|list|get|display|view|give\s+me)?\s*(all\s+)?approval(s)?\s*$",
            ],
            "exclude_patterns": [],
            "roles": ["lead", "manager", "hr", "employee"],
            "question": "Do you want certification approvals, remark approvals, badge approvals, or RnR approvals?",
            "options": ["Certification Approvals", "Remark Approvals", "Badge Approvals", "RnR Approvals"],
            "missing_field": "schema_scope",
            "tables": ["certification_approvals", "remark_approvals", "approval_history", "rnr_approval_actions"],
            "columns": [],
            "log_reason": "Bare 'approvals' — 4 different tables, SQL cannot be generated without disambiguation.",
        },
        # Goals by/for manager — valid ambiguity: all managers vs specific manager
        {
            "patterns": [
                r"\bgoal(s)?\b.{0,60}\bmanager(s)?\b",
                r"\bmanager(s)?\b.{0,60}\bgoal(s)?\b",
                r"\bkra(s)?\b.{0,60}\bmanager(s)?\b",
            ],
            "exclude_patterns": [r"\bspecific\s+manager\b", r"\bparticular\s+manager\b"],
            "roles": ["lead", "manager", "hr", "employee"],
            "question": "Do you want to view goals assigned by all managers or a specific manager?",
            "options": ["All Managers", "Specific Manager"],
            "missing_field": "manager_scope",
            "tables": ["user_goal_mapping", "user_table"],
            "columns": ["assigned_by", "reporting_manager"],
            "log_reason": "Goals+manager — scope ambiguous: all managers vs specific manager.",
        },
    ]

    # ── Named-person helpers ──────────────────────────────────────────────────

    def _has_date_context(self, message: str) -> bool:
        return bool(self._DATE_KEYWORDS_RE.search(message))

    def _is_named_person_query(self, message: str) -> bool:
        """
        Return True when a specific person is already named after a relational
        verb — no clarification about manager/employee scope is needed.
        "Employees who report to Baskar"  → True  (Baskar is named)
        "Reporting to all managers"       → False (generic role word)
        """
        m = self._NAMED_PERSON_RE.search(message)
        if not m:
            return False
        name_token = m.group(1).lower()
        return bool(name_token) and name_token not in self._NON_PERSON_WORDS

    def _is_similar_question(self, q1: str, q2: str) -> bool:
        """Return True if two clarification questions are substantially the same."""
        _STOPWORDS = frozenset({
            "a", "an", "the", "do", "you", "want", "to", "or", "and",
            "is", "for", "of", "in", "that", "this", "with", "all", "see",
        })

        def _kw(text: str) -> set:
            return {
                w for w in re.sub(r"[^\w\s]", "", text.lower()).split()
                if w not in _STOPWORDS
            }

        kw1, kw2 = _kw(q1), _kw(q2)
        if not kw1 or not kw2:
            return False
        return len(kw1 & kw2) / min(len(kw1), len(kw2)) >= 0.5

    # ── Schema-grounded response ──────────────────────────────────────────────

    def _schema_grounded_response(
        self, message: str, user_role: str
    ) -> Optional[Dict[str, Any]]:
        """
        Check if the query matches a deterministic entity-scope clarification
        pattern. Returns a direct clarification bypassing the LLM, or None to
        fall through to the LLM.

        Only covers cases where SQL genuinely cannot be generated without
        disambiguation (multiple tables / fundamentally ambiguous scope).
        Single-table queries (feedback, skills, certs, badges, goals) must NOT
        be in this list — they route to SQL generation directly.
        """
        if self._SELF_REFERENCE_RE.search(message):
            return None

        msg_lower = message.lower()
        for entry in self._ENTITY_SCOPE_CLARIFICATIONS:
            if user_role not in entry["roles"]:
                continue
            excluded = any(
                re.search(excl, msg_lower, re.IGNORECASE)
                for excl in entry.get("exclude_patterns", [])
            )
            if excluded:
                continue
            for pattern in entry["patterns"]:
                if re.search(pattern, msg_lower, re.IGNORECASE):
                    logger.info(
                        "[intent_agent] schema-grounded clarification | "
                        "reason=%s tables=%s",
                        entry.get("log_reason", ""),
                        entry["tables"],
                    )
                    return {
                        "track": "incomplete",
                        "confidence": 0.95,
                        "greeting_message": "",
                        "off_topic_reason": None,
                        "polite_block_message": None,
                        "missing_field": entry["missing_field"],
                        "follow_up_question": entry["question"],
                        "follow_up_options": entry["options"],
                        "enriched_prompt": None,
                        "extracted_filters": {},
                        "reasoning": entry.get("log_reason", "Schema-grounded clarification."),
                    }
        return None

    # ── classify ─────────────────────────────────────────────────────────────

    def classify(
        self,
        user_message: str,
        user_role: str = "employee",
        clarification_round: int = 0,
        prior_followup: str = "",
        schema_summary: str = "",
        memory_context: str = "",
    ) -> Dict[str, Any]:
        if not user_message.strip():
            return self._empty_message_response(user_role)

        # ── Shortcut 1: master-data lookup → always clear ─────────────────────
        if self._is_master_data_query(user_message):
            logger.info("[intent_agent] master-data shortcut: %s", user_message[:80])
            return self._master_data_response(user_message)

        # ── Shortcut 2: follow-up refinement → always clear ───────────────────
        if memory_context and self._is_continuation(user_message):
            logger.info("[intent_agent] continuation shortcut: %s", user_message[:80])
            return self._continuation_response(user_message)

        # ── Shortcut 3: named person already in query → always clear ──────────
        if self._is_named_person_query(user_message):
            logger.info(
                "[intent_agent] named-person shortcut (specific target stated): %s",
                user_message[:80],
            )
            return {
                "track": "clear",
                "confidence": 0.97,
                "greeting_message": "",
                "off_topic_reason": None,
                "polite_block_message": None,
                "missing_field": None,
                "follow_up_question": None,
                "follow_up_options": [],
                "enriched_prompt": user_message,
                "extracted_filters": {},
                "reasoning": "Specific person already named — no scope clarification needed.",
            }

        # ── Shortcut 4: schema-grounded entity-scope clarification ────────────
        # Only on round 0, only for genuinely multi-table ambiguous queries.
        if clarification_round == 0:
            grounded = self._schema_grounded_response(user_message, user_role)
            if grounded is not None:
                return grounded

        # ── LLM classification ────────────────────────────────────────────────
        safe_schema = (schema_summary[:2000] if schema_summary else "KRA goals, ratings, appraisals, employee data")
        safe_schema = safe_schema.replace("{", "{{").replace("}", "}}")
        safe_memory = (memory_context[:1500] if memory_context else "No prior conversation.")
        safe_memory = safe_memory.replace("{", "{{").replace("}", "}}")

        suggestions = suggestion_builder.get_suggestions(user_role)
        role_examples = "\n".join(f"• {s}" for s in suggestions)
        db_options = clarification_options_builder.get_all_options()

        def _fmt(opts: list) -> str:
            return ", ".join(f'"{o}"' for o in opts) if opts else "—"

        system_prompt = _SYSTEM_PROMPT.format(
            user_role=user_role,
            clarification_round=clarification_round,
            prior_followup=prior_followup or "None",
            memory_context=safe_memory,
            schema_summary=safe_schema,
            role_examples=role_examples,
            time_period_options=_fmt(db_options.get("time_period", [])),
            employee_scope_options=_fmt(db_options.get("employee_scope", [])),
            manager_scope_options=_fmt(db_options.get("manager_scope", [])),
            status_filter_options=_fmt(db_options.get("status_filter", [])),
            metric_options=_fmt(db_options.get("metric", [])),
            schema_scope_options=_fmt(db_options.get("schema_scope", [])),
            designation_options=_fmt(db_options.get("designation", [])),
        )

        try:
            llm = self._get_llm()
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Classify this message: {user_message}"),
            ])
            raw = response.content.strip()
            logger.debug("[intent_agent] raw LLM: %s", raw[:400])
            result = self._parse(raw, user_message, user_role, clarification_round)

            # ── Post-LLM: block repeat clarification ──────────────────────────
            # If the LLM re-asks the exact same question from the previous round,
            # force track="clear" so the report generates instead of looping.
            if (
                result.get("track") == "incomplete"
                and clarification_round >= 1
                and prior_followup
                and result.get("follow_up_question")
                and self._is_similar_question(result["follow_up_question"], prior_followup)
            ):
                logger.info(
                    "[intent_agent] Blocked repeat clarification — forcing clear. "
                    "prior=%r re-asked=%r",
                    prior_followup[:60], result["follow_up_question"][:60],
                )
                result["track"] = "clear"
                result["follow_up_question"] = None
                result["follow_up_options"] = []
                result["missing_field"] = None
                if not result.get("enriched_prompt"):
                    result["enriched_prompt"] = user_message

            return result
        except Exception as e:
            logger.error("[intent_agent] LLM call failed: %s", e)
            return self._fallback(user_message)

    # ── Parsing helpers ───────────────────────────────────────────────────────

    def _parse(self, raw: str, original: str, user_role: str, clarification_round: int) -> Dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.error("[intent_agent] no JSON found in LLM response")
            return self._fallback(original)

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.error("[intent_agent] JSON parse error: %s", exc)
            return self._fallback(original)

        # Hard-enforce clarification_round >= 2 → always clear
        if clarification_round >= 2:
            data["track"] = "clear"
            if not data.get("enriched_prompt"):
                data["enriched_prompt"] = original

        track = data.get("track", "clear")
        missing_field = data.get("missing_field")

        # ── Guard: block time_period when query has no date/time context ──────
        # The LLM occasionally violates this rule; this post-processes the output.
        if track == "incomplete" and missing_field == "time_period":
            if not self._has_date_context(original):
                logger.info(
                    "[intent_agent] Blocked spurious time_period clarification "
                    "(no date context in %r). Forcing clear.", original[:80],
                )
                track = "clear"
                missing_field = None
                data["track"] = "clear"
                if not data.get("enriched_prompt"):
                    data["enriched_prompt"] = original

        # Ensure greeting_message is populated for greeting track
        greeting_message = data.get("greeting_message") or ""
        if track == "greeting" and not greeting_message:
            redirect = suggestion_builder.get_greeting_redirect(user_role)
            greeting_message = f"Hello! {redirect[0].upper()}{redirect[1:]}"

        # Ensure polite_block_message is populated for off_topic
        polite_block_message = data.get("polite_block_message") or ""
        if track == "off_topic" and not polite_block_message:
            redirect = suggestion_builder.get_off_topic_redirect(user_role)
            polite_block_message = f"I'm only able to help with KRA report building! {redirect}"

        # Override follow_up_options with live DB data for LLM-generated clarifications.
        # Prevents hallucinated names/values in options.
        # (Schema-grounded responses bypass _parse() entirely, so their options are kept.)
        if track == "incomplete" and missing_field:
            follow_up_options = clarification_options_builder.get_options(missing_field)
            logger.debug(
                "[intent_agent] overriding follow_up_options field=%s count=%d",
                missing_field, len(follow_up_options),
            )
        else:
            follow_up_options = data.get("follow_up_options") or []

        logger.info(
            "[intent_agent] classified | track=%s missing_field=%s "
            "confidence=%.2f reasoning=%s",
            track, missing_field,
            float(data.get("confidence", 0.8)),
            data.get("reasoning", "")[:100],
        )

        return {
            "track": track,
            "confidence": float(data.get("confidence", 0.8)),
            "greeting_message": greeting_message,
            "off_topic_reason": data.get("off_topic_reason"),
            "polite_block_message": polite_block_message,
            "missing_field": missing_field,
            "follow_up_question": data.get("follow_up_question"),
            "follow_up_options": follow_up_options,
            "enriched_prompt": data.get("enriched_prompt"),
            "extracted_filters": data.get("extracted_filters") or {},
            "reasoning": data.get("reasoning", ""),
        }

    def _fallback(self, original: str) -> Dict[str, Any]:
        return {
            "track": "clear",
            "confidence": 0.5,
            "greeting_message": "",
            "off_topic_reason": None,
            "polite_block_message": None,
            "missing_field": None,
            "follow_up_question": None,
            "follow_up_options": [],
            "enriched_prompt": original,
            "extracted_filters": {},
            "reasoning": "Fallback: LLM response could not be parsed",
        }

    def _empty_message_response(self, user_role: str) -> Dict[str, Any]:
        redirect = suggestion_builder.get_greeting_redirect(user_role)
        message = f"It looks like your message was empty. {redirect[0].upper()}{redirect[1:]}"
        return {
            "track": "greeting",
            "confidence": 1.0,
            "greeting_message": message,
            "off_topic_reason": None,
            "polite_block_message": None,
            "missing_field": None,
            "follow_up_question": None,
            "follow_up_options": [],
            "enriched_prompt": None,
            "extracted_filters": {},
            "reasoning": "Empty message treated as greeting",
        }


intent_agent = IntentDetectorAgent()
