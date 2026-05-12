import json
import logging
import re
from typing import Any, Dict, List, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# System prompt template                                                        #
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT = """You are an intent classifier for a KRA (Key Result Area) AI report builder system.
Classify every user message into one of three tracks and return ONLY valid JSON.

USER CONTEXT:
- User Role: {user_role}
- Clarification Round: {clarification_round}  (if >= 2 you MUST return track="clear")
- Prior Follow-up Asked: {prior_followup}

KRA SCHEMA SUMMARY (for assessing query completeness):
{schema_summary}

=== THREE TRACKS ===

TRACK A — "off_topic":
Message has NO connection to KRA reports, employee goals, appraisals, ratings, or performance data.
Triggers: greetings ("hi", "hello", "thanks", "good morning"), general knowledge (weather, dates,
math, geography), questions about you as an AI ("who are you", "what can you do", "are you ChatGPT"),
empty messages, jokes, unrelated requests.
DO NOT classify as off_topic if the message mentions: goals, KRA, ratings, performance, appraisal,
employees, reports, completion, objectives, productivity, or any business metric — those are B or C.
Special: "thanks" / "ok" / "great" AFTER a report → Track A (respond warmly, offer next actions).

TRACK B — "incomplete":
Message IS about KRA/performance/goals BUT is missing critical information needed for SQL generation.
Missing-info categories (ask about the HIGHEST priority one first):
  1. metric       — what to measure: goals? ratings? completion %? count? summary?
  2. time_period  — no quarter, year, or date range (e.g. "show my goals" has no period)
  3. employee_scope — manager/lead with no clarity on WHOSE data (NEVER ask for role=employee)
  4. status_filter — "show goals" without all/completed/in-progress/not-started
  5. schema_scope — unclear which module
  6. comparison_base — "compare" with only one side specified

Refinements always go to Track C: "filter by", "group by", "sort by", "now show", "also show".
If clarification_round >= 2 → MUST return track="clear" regardless.
If prior_followup was about time_period, ask about the next priority missing field.
NEVER ask about employee_scope when role=employee.
Ask EXACTLY ONE question. Provide 3-5 concrete answer options.

TRACK C — "clear":
Message has enough info to generate SQL without guessing, OR clarification_round >= 2 (force).
A message is CLEAR when: subject known + metric known/inferable + time period known or not required.
Write enriched_prompt as ONE precise sentence using KRA schema terminology.
Include all extracted values (quarter, year, status, employee names, role-appropriate scope).

=== ROLE-APPROPRIATE EXAMPLES (use EXACTLY these in polite_block_message for Track A) ===

role=employee:
• Show my KRA goals for Q1 2025 with completion percentage
• What is my average performance rating this year?
• List all my in-progress goals for this quarter

role=lead:
• Show completion rates for Alex and Kalai in Q2 2025
• List overdue goals for my team members this quarter
• Compare Q1 vs Q2 ratings for my reportees

role=manager:
• Show average KRA scores for all team members this quarter
• Who has the lowest goal completion rate in Q1 2025?
• List all employees with pending appraisals this cycle

role=hr:
• Show average KRA scores by department for 2025
• List all employees with ratings below 3 this year
• Generate a full performance summary for the current appraisal cycle

=== OUTPUT FORMAT — return ONLY this JSON, no markdown, no text outside the braces ===
{{
  "track": "off_topic|incomplete|clear",
  "confidence": 0.95,
  "off_topic_reason": "greeting|general_knowledge|unrelated|personal|null",
  "polite_block_message": "string or null",
  "missing_field": "time_period|employee_scope|metric|status_filter|schema_scope|comparison_base|null",
  "follow_up_question": "string or null",
  "follow_up_options": ["Q1 2025", "Q2 2025", "Q3 2025", "Q4 2024", "Full year 2025"],
  "enriched_prompt": "string or null",
  "extracted_filters": {{}},
  "reasoning": "brief one-sentence explanation"
}}

CRITICAL RULES:
1. Return ONLY valid JSON. Absolutely no text before or after the JSON object.
2. clarification_round >= 2 → track MUST be "clear", enriched_prompt MUST be set.
3. track="off_topic" → polite_block_message MUST follow this format exactly:
   "I don't have information on [topic]. I'm built to generate reports from the KRA system.
   Here are some things you can ask me:\\n\\n• [role example 1]\\n• [role example 2]\\n• [role example 3]"
4. track="incomplete" → follow_up_question and follow_up_options MUST be set. ONE question only.
5. track="clear" → enriched_prompt MUST be a complete, specific sentence.
6. NEVER ask about employee_scope when role=employee.
7. NEVER repeat the prior_followup question — choose the next priority missing field instead.
8. For time_period options always suggest: Q1 2025, Q2 2025, Q3 2025, Q4 2024, Full year 2025.
"""

# Role-appropriate examples used in the empty-message fallback
_ROLE_EXAMPLES: Dict[str, List[str]] = {
    "employee": [
        "Show my KRA goals for Q1 2025 with completion percentage",
        "What is my average performance rating this year?",
        "List all my in-progress goals for this quarter",
    ],
    "lead": [
        "Show completion rates for Alex and Kalai in Q2 2025",
        "List overdue goals for my team members this quarter",
        "Compare Q1 vs Q2 ratings for my reportees",
    ],
    "manager": [
        "Show average KRA scores for all team members this quarter",
        "Who has the lowest goal completion rate in Q1 2025?",
        "List all employees with pending appraisals this cycle",
    ],
    "hr": [
        "Show average KRA scores by department for 2025",
        "List all employees with ratings below 3 this year",
        "Generate a full performance summary for the current appraisal cycle",
    ],
}


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

    def classify(
        self,
        user_message: str,
        user_role: str = "employee",
        clarification_round: int = 0,
        prior_followup: str = "",
        schema_summary: str = "",
    ) -> Dict[str, Any]:
        if not user_message.strip():
            return self._empty_message_response(user_role)

        # Escape braces in the schema before inserting into the format string.
        safe_schema = (schema_summary[:2000] if schema_summary else "KRA goals, ratings, appraisals, employee data")
        safe_schema = safe_schema.replace("{", "{{").replace("}", "}}")

        system_prompt = _SYSTEM_PROMPT.format(
            user_role=user_role,
            clarification_round=clarification_round,
            prior_followup=prior_followup or "None",
            schema_summary=safe_schema,
        )

        try:
            llm = self._get_llm()
            response = llm.invoke([
                SystemMessage(content=system_prompt),
                HumanMessage(content=f"Classify this message: {user_message}"),
            ])
            raw = response.content.strip()
            logger.debug(f"[intent_agent] raw: {raw[:400]}")
            return self._parse(raw, user_message, clarification_round)
        except Exception as e:
            logger.error(f"[intent_agent] LLM call failed: {e}")
            return self._fallback(user_message)

    # ---------------------------------------------------------------------- #
    # Parsing helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _parse(self, raw: str, original: str, clarification_round: int) -> Dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.error("[intent_agent] no JSON found in response")
            return self._fallback(original)

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.error(f"[intent_agent] JSON parse error: {exc}")
            return self._fallback(original)

        # Hard-enforce the clarification_round >= 2 rule
        if clarification_round >= 2:
            data["track"] = "clear"
            if not data.get("enriched_prompt"):
                data["enriched_prompt"] = original

        return {
            "track": data.get("track", "clear"),
            "confidence": float(data.get("confidence", 0.8)),
            "off_topic_reason": data.get("off_topic_reason"),
            "polite_block_message": data.get("polite_block_message"),
            "missing_field": data.get("missing_field"),
            "follow_up_question": data.get("follow_up_question"),
            "follow_up_options": data.get("follow_up_options") or [],
            "enriched_prompt": data.get("enriched_prompt"),
            "extracted_filters": data.get("extracted_filters") or {},
            "reasoning": data.get("reasoning", ""),
        }

    def _fallback(self, original: str) -> Dict[str, Any]:
        """Used when the LLM returns unparseable output — always proceed to Track C."""
        return {
            "track": "clear",
            "confidence": 0.5,
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
        examples = _ROLE_EXAMPLES.get(user_role, _ROLE_EXAMPLES["employee"])
        bullets = "\n".join(f"• {e}" for e in examples)
        message = (
            "It looks like your message was empty. "
            "I'm here to generate KRA reports. What would you like to see?\n\n"
            + bullets
        )
        return {
            "track": "off_topic",
            "confidence": 1.0,
            "off_topic_reason": "general_knowledge",
            "polite_block_message": message,
            "missing_field": None,
            "follow_up_question": None,
            "follow_up_options": [],
            "enriched_prompt": None,
            "extracted_filters": {},
            "reasoning": "Empty message",
        }


intent_agent = IntentDetectorAgent()
