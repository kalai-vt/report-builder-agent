import json
import logging
import re
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings
from app.services.suggestion_builder import suggestion_builder

logger = logging.getLogger(__name__)

# --------------------------------------------------------------------------- #
# System prompt template                                                        #
# {role_examples} is injected at call-time from suggestion_builder so it       #
# always reflects the live MySQL schema.                                        #
# --------------------------------------------------------------------------- #

_SYSTEM_PROMPT = """You are an intent classifier for a KRA (Key Result Area) AI report builder system.
Classify every user message into one of FOUR tracks and return ONLY valid JSON.

USER CONTEXT:
- User Role: {user_role}
- Clarification Round: {clarification_round}  (if >= 2 you MUST return track="clear")
- Prior Follow-up Asked: {prior_followup}

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
Use the schema-derived examples below for your suggestions.

TRACK B — "off_topic":
Message has NO connection to KRA reports, employee goals, appraisals, ratings, or performance data.
Triggers: general knowledge (weather, sports, news, geography, history), coding or tech questions
         unrelated to KRA, personal questions, opinions, math, trivia, unrelated requests.
DO NOT classify as off_topic if the message mentions: goals, KRA, ratings, performance, appraisal,
employees, reports, completion, objectives, productivity, or any business metric — those are C or D.

Response rule: polite decline in 1 sentence + suggest ONE KRA report action from the examples below.
Max 2 sentences total. NEVER explain why you cannot answer. NEVER answer the question. Just redirect.
Template: "I'm only able to help with KRA report building! [Suggest one of the examples below]."

TRACK C — "incomplete":
Message IS about KRA/performance/goals BUT is missing critical information needed for SQL generation.
Missing-info categories (ask about the HIGHEST priority one first):
  1. metric       — what to measure: goals? ratings? completion %? count? summary?
  2. time_period  — no quarter, year, or date range (e.g. "show my goals" has no period)
  3. employee_scope — manager/lead with no clarity on WHOSE data (NEVER ask for role=employee)
  4. status_filter — "show goals" without all/completed/in-progress/not-started
  5. schema_scope — unclear which module
  6. comparison_base — "compare" with only one side specified

Refinements always go to Track D: "filter by", "group by", "sort by", "now show", "also show".
If clarification_round >= 2 → MUST return track="clear" regardless.
If prior_followup was about time_period, ask about the next priority missing field.
NEVER ask about employee_scope when role=employee.
Ask EXACTLY ONE question. Provide 3-5 concrete answer options.

TRACK D — "clear":
Message has enough info to generate SQL without guessing, OR clarification_round >= 2 (force).
A message is CLEAR when: subject known + metric known/inferable + time period known or not required.
Write enriched_prompt as ONE precise sentence using KRA schema terminology.
Include all extracted values (quarter, year, status, employee names, role-appropriate scope).

=== SCHEMA-DERIVED EXAMPLES FOR THIS USER (use these in greeting_message and polite_block_message) ===

{role_examples}

=== OUTPUT FORMAT — return ONLY this JSON, no markdown, no text outside the braces ===
{{
  "track": "greeting|off_topic|incomplete|clear",
  "confidence": 0.95,
  "greeting_message": "string or null",
  "off_topic_reason": "general_knowledge|unrelated|personal|null",
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
3. track="greeting" → greeting_message MUST be set (warm reply + one schema-based suggestion, ≤2 sentences).
4. track="off_topic" → polite_block_message MUST be set (1-sentence decline + 1 schema-based suggestion, NO bullet lists).
5. track="incomplete" → follow_up_question and follow_up_options MUST be set. ONE question only.
6. track="clear" → enriched_prompt MUST be a complete, specific sentence.
7. NEVER ask about employee_scope when role=employee.
8. NEVER repeat the prior_followup question — choose the next priority missing field instead.
9. For time_period options always suggest: Q1 2025, Q2 2025, Q3 2025, Q4 2024, Full year 2025.
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

        safe_schema = (schema_summary[:2000] if schema_summary else "KRA goals, ratings, appraisals, employee data")
        safe_schema = safe_schema.replace("{", "{{").replace("}", "}}")

        # Build schema-driven examples and inject into the prompt
        suggestions = suggestion_builder.get_suggestions(user_role)
        role_examples = "\n".join(f"• {s}" for s in suggestions)

        system_prompt = _SYSTEM_PROMPT.format(
            user_role=user_role,
            clarification_round=clarification_round,
            prior_followup=prior_followup or "None",
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
            logger.debug(f"[intent_agent] raw: {raw[:400]}")
            return self._parse(raw, user_message, user_role, clarification_round)
        except Exception as e:
            logger.error(f"[intent_agent] LLM call failed: {e}")
            return self._fallback(user_message)

    # ---------------------------------------------------------------------- #
    # Parsing helpers                                                          #
    # ---------------------------------------------------------------------- #

    def _parse(self, raw: str, original: str, user_role: str, clarification_round: int) -> Dict[str, Any]:
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

        track = data.get("track", "clear")

        # Ensure greeting_message is populated for greeting track
        greeting_message = data.get("greeting_message") or ""
        if track == "greeting" and not greeting_message:
            redirect = suggestion_builder.get_greeting_redirect(user_role)
            greeting_message = f"Hello! {redirect[0].upper()}{redirect[1:]}"

        # Ensure polite_block_message is populated for off_topic (no bullet list)
        polite_block_message = data.get("polite_block_message") or ""
        if track == "off_topic" and not polite_block_message:
            redirect = suggestion_builder.get_off_topic_redirect(user_role)
            polite_block_message = f"I'm only able to help with KRA report building! {redirect}"

        return {
            "track": track,
            "confidence": float(data.get("confidence", 0.8)),
            "greeting_message": greeting_message,
            "off_topic_reason": data.get("off_topic_reason"),
            "polite_block_message": polite_block_message,
            "missing_field": data.get("missing_field"),
            "follow_up_question": data.get("follow_up_question"),
            "follow_up_options": data.get("follow_up_options") or [],
            "enriched_prompt": data.get("enriched_prompt"),
            "extracted_filters": data.get("extracted_filters") or {},
            "reasoning": data.get("reasoning", ""),
        }

    def _fallback(self, original: str) -> Dict[str, Any]:
        """Used when the LLM returns unparseable output — always proceed to clear."""
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
