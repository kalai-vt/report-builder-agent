import json
import logging
import re
from typing import Any, Dict, Optional, List

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.suggestion_builder import suggestion_builder

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# System prompt template                                                        #
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT = """You are an intent classifier for a KRA (Key Result Area) AI report builder system.
Classify every user message into one of THREE tracks and return ONLY valid JSON.

USER CONTEXT:
- User Role: {user_role}

CONVERSATION HISTORY (most recent exchanges):
{memory_context}

KRA SCHEMA SUMMARY (for assessing query completeness):
{schema_summary}

=== THREE TRACKS ===

TRACK A — "greeting":
Message is a social/conversational opener with NO data request.
Triggers: hi, hello, hey, good morning/afternoon/evening, how are you, thanks, thank you,
         bye, goodbye, ok, great, "are you there", questions about your capabilities
         ("what can you do", "who are you", "are you ChatGPT").
Special: "thanks"/"ok"/"great" AFTER a report → Track A (respond warmly, offer next actions).
Do NOT use for messages that also mention goals, KRA, performance, ratings, or employees.
CRITICAL: "How are we doing?", "How do we stand?", "How's our performance?" are KRA metric
         queries → ALWAYS track="clear". They are NOT greetings.

Response rule: 1 warm sentence + 1 specific KRA report suggestion. Max 2 sentences. NEVER ask
open-ended "how can I help" — suggest something specific based on user role.

TRACK B — "off_topic":
Message has NO connection to KRA reports, employee goals, appraisals, ratings, performance data,
or the organisational/HR master data that supports the KRA system.
DO NOT classify as off_topic if the message mentions: goals, KRA, ratings, performance, appraisal,
employees, reports, completion, objectives, productivity, any business metric, streams, designations,
departments, grades, roles, locations, employee attributes, organisational structure, master data,
or any field that exists in the KRA/HR database — those are Track C.

FOLLOW-UP OVERRIDE (highest priority): If the Conversation History above contains a previous
KRA report query and the current message is a short modification/refinement such as "add X",
"include X", "also show X", "remove X", "filter by X", "sort by X", "group by X", "with X",
"show only X", "exclude X", "now add X", or similar — it is ALWAYS track="clear".
NEVER classify such messages as off_topic.

Response rule: polite decline in 1 sentence + suggest ONE KRA report action.
Template: "I'm only able to help with KRA report building! [Suggest one of the examples below]."

TRACK C — "clear":
Message has enough info to generate SQL. Use this for ALL KRA-related queries — even if the
query is ambiguous, generate the best possible SQL rather than asking for clarification.
Time period is NOT required for listing queries — omit date filter and return all rows.
User-provided values (stream, designation, status, name) → always use in WHERE clause.
Write enriched_prompt as ONE precise sentence using KRA schema terminology.

ALWAYS track="clear":
  - "How are we doing?" / "How do we stand?" / "How's our performance?" → KRA health metric query
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
  - "Show approvals"                                     → FROM approval_history (all types)
  - Master data lookups (designations, streams, departments, etc.)

=== SCHEMA-DERIVED EXAMPLES FOR THIS USER ===

{role_examples}

=== OUTPUT FORMAT — return ONLY this JSON, no markdown, no text outside the braces ===
{{
  "track": "greeting|off_topic|clear",
  "confidence": 0.95,
  "greeting_message": "string or null",
  "off_topic_reason": "general_knowledge|unrelated|personal|null",
  "polite_block_message": "string or null",
  "enriched_prompt": "string or null",
  "extracted_filters": {{}},
  "reasoning": "brief one-sentence explanation"
}}

CRITICAL RULES:
1. Return ONLY valid JSON. No text before or after the JSON object.
2. track="greeting" → greeting_message MUST be set (warm reply + one schema-based suggestion, ≤2 sentences).
3. track="off_topic" → polite_block_message MUST be set (1-sentence decline + 1 suggestion).
4. track="clear" → enriched_prompt MUST be a complete, specific sentence.
5. NEVER ask the user for clarification — always generate SQL with best-effort interpretation.
6. NEVER invent column values — if user provided a value, use it; let DB filter.
7. MASTER DATA QUERIES are ALWAYS track="clear".
8. FOLLOW-UP REFINEMENTS are ALWAYS track="clear".
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
                max_tokens=400,
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
        r"show\s+(?:me\s+)?(?:data|results?|report)\s+only|"
        r"show\s+only\s+for|data\s+only\s+for|results?\s+only\s+for|"
        r"show\s+(?:me\s+)?(?:data|results?|info(?:rmation)?)\s+(?:only\s+)?for\s+\w|"
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
            "enriched_prompt": message,
            "extracted_filters": {},
            "reasoning": "Master data lookup — forced to clear without LLM classification",
        }

    # ── KRA report keyword detection — always clear ───────────────────────────
    _KRA_REPORT_RE = re.compile(
        r"\b("
        r"kra|goal|goals|compliance|non[\s\-]?compliance|non[\s\-]?complian[ct]"
        r"|appraisal|appraisals|performance|feedback|report|reports"
        r"|rating|ratings|designation|designation[\s\-]wise"
        r"|employee(s)?|reportee(s)?|direct\s+report(s|ee(s)?)?"
        r"|pending|approval|approved|submitted|assigned"
        r"|target|target\s+date|completion|incomplete|completed|in\s+progress"
        r"|department[\s\-]wise|stream[\s\-]wise|skill(s)?|certification(s)?|badge(s)?"
        r"|productivity|objective(s)?|okr|review(s)?|reviewed"
        r")\b",
        re.IGNORECASE,
    )

    def _is_kra_report_query(self, message: str) -> bool:
        return bool(self._KRA_REPORT_RE.search(message))

    def _kra_report_response(self, message: str) -> Dict[str, Any]:
        return {
            "track": "clear",
            "confidence": 1.0,
            "greeting_message": "",
            "off_topic_reason": None,
            "polite_block_message": None,
            "enriched_prompt": message,
            "extracted_filters": {},
            "reasoning": "KRA report keyword matched — forced clear without LLM classification",
        }

    # ── Named-person detection ────────────────────────────────────────────────
    _NAMED_PERSON_RE = re.compile(
        r"\b(?:"
        r"report(?:ing|s)?\s+to|directly\s+report(?:ing|s)?\s+to|"
        r"under\s+(?:the\s+)?(?:management\s+of\s+)?|"
        r"managed\s+by|assigned\s+by|subordinates?\s+of|"
        r"reportees?\s+of|team\s+of|"
        r"(?:kra|goal|report|feedback|skill|certifi(?:cate|cation)|badge|"
        r"compliance|performance|appraisal|productivity)\s+(?:\w+\s+){0,3}for\s+|"
        r"for\s+employee\s+"
        r")\s*(\w+)",
        re.IGNORECASE,
    )
    _NON_PERSON_WORDS: frozenset = frozenset({
        "all", "any", "the", "a", "an", "some", "specific", "particular",
        "manager", "managers", "lead", "leads", "employee", "employees",
        "him", "her", "them", "everyone", "anyone", "someone", "nobody",
        "senior", "junior", "team", "group", "department", "every", "each",
        "other", "another", "certain", "given", "past", "last", "this",
        "current", "previous", "next", "recent", "whole", "full", "entire",
    })
    _DATE_WORDS: frozenset = frozenset({
        "q1", "q2", "q3", "q4", "january", "february", "march", "april",
        "may", "june", "july", "august", "september", "october", "november",
        "december", "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep",
        "sept", "oct", "nov", "dec", "year", "month", "week", "quarter",
        "today", "yesterday", "annual", "fiscal", "period",
    })

    def _is_named_person_query(self, message: str) -> bool:
        m = self._NAMED_PERSON_RE.search(message)
        if not m:
            return False
        token = m.group(1).lower()
        if not token or len(token) < 2:
            return False
        if token in self._NON_PERSON_WORDS:
            return False
        if token in self._DATE_WORDS:
            return False
        return True

    # ── classify ─────────────────────────────────────────────────────────────

    def classify(
        self,
        user_message: str,
        user_role: str = "employee",
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

        # ── Shortcut 3: KRA report keyword → always clear (before LLM) ───────
        if self._is_kra_report_query(user_message):
            logger.info("[intent_agent] kra-keyword shortcut: %s", user_message[:80])
            return self._kra_report_response(user_message)

        # ── Shortcut 4: named person already in query → always clear ──────────
        if self._is_named_person_query(user_message):
            logger.info("[intent_agent] named-person shortcut: %s", user_message[:80])
            return {
                "track": "clear",
                "confidence": 0.97,
                "greeting_message": "",
                "off_topic_reason": None,
                "polite_block_message": None,
                "enriched_prompt": user_message,
                "extracted_filters": {},
                "reasoning": "Specific person already named — no scope clarification needed.",
            }

        # ── LLM classification ────────────────────────────────────────────────
        safe_schema = (schema_summary[:2000] if schema_summary else "KRA goals, ratings, appraisals, employee data")
        safe_schema = safe_schema.replace("{", "{{").replace("}", "}}")
        safe_memory = (memory_context[:1500] if memory_context else "No prior conversation.")
        safe_memory = safe_memory.replace("{", "{{").replace("}", "}}")

        suggestions = suggestion_builder.get_suggestions(user_role)
        role_examples = "\n".join(f"• {s}" for s in suggestions)

        system_prompt = _SYSTEM_PROMPT.format(
            user_role=user_role,
            memory_context=safe_memory,
            schema_summary=safe_schema,
            role_examples=role_examples,
        )

        try:
            llm = self._get_llm()
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Classify this message: {user_message}"),
            ])
            raw = response.content.strip()
            logger.debug("[intent_agent] raw LLM: %s", raw[:400])
            return self._parse(raw, user_message, user_role)
        except Exception as e:
            logger.error("[intent_agent] LLM call failed: %s", e)
            return self._fallback(user_message)

    # ── Parsing helpers ───────────────────────────────────────────────────────

    def _parse(self, raw: str, original: str, user_role: str) -> Dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.error("[intent_agent] no JSON found in LLM response")
            return self._fallback(original)

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.error("[intent_agent] JSON parse error: %s", exc)
            return self._fallback(original)

        track = data.get("track", "clear")

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

        # Any unexpected track value (e.g. "incomplete") defaults to clear
        if track not in ("greeting", "off_topic", "clear"):
            logger.info("[intent_agent] unexpected track=%s — forcing clear", track)
            track = "clear"

        enriched_prompt = data.get("enriched_prompt") or ""
        if track == "clear" and not enriched_prompt:
            enriched_prompt = original

        logger.info(
            "[intent_agent] classified | track=%s confidence=%.2f reasoning=%s",
            track,
            float(data.get("confidence", 0.8)),
            data.get("reasoning", "")[:100],
        )

        return {
            "track": track,
            "confidence": float(data.get("confidence", 0.8)),
            "greeting_message": greeting_message,
            "off_topic_reason": data.get("off_topic_reason"),
            "polite_block_message": polite_block_message,
            "enriched_prompt": enriched_prompt,
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
            "enriched_prompt": None,
            "extracted_filters": {},
            "reasoning": "Empty message treated as greeting",
        }


intent_agent = IntentDetectorAgent()
