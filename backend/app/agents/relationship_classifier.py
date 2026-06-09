import json
import logging
import re
from typing import Any, Dict, Optional

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_SYSTEM_PROMPT = """\
You are a query relationship classifier for a KRA analytics system.

Determine whether the CURRENT QUERY is a follow-up refinement of the PREVIOUS REPORT
or a completely new, unrelated report request.

PREVIOUS CONTEXT:
{previous_context}

════════════════════════════════════════
 CLASSIFICATION RULES
════════════════════════════════════════

Return "followup" when the current query:
- Modifies, filters, or extends the same report shown above
- Refers to the same employee / team / department as the previous query
- Uses continuation language: "also", "too", "as well", "same", "them", "their"
- Is a short phrase that only makes sense in the context of a prior result
  ("Only Alex", "Last month", "Department wise", "Top 10", "Exclude completed")
- Adds, removes, or changes a column, filter, sort, or grouping on the previous SQL
- Asks for a time-period comparison of the SAME report ("Compare with previous month")

Return "new_request" when the current query:
- Asks for a different report type (e.g., was goals → now certifications or skills)
- Names a different employee with a different report context
  (e.g., was "KRA for Kalai" → "Feedback report for Alex" — different type + different person)
- Changes the primary business domain (goals vs feedback vs compliance vs RNR)
- Could be answered completely independently without knowing what was shown before
- Explicitly signals a fresh start ("Show me ...", "Give me ...", "Generate a report for ...")

Return "uncertain" ONLY when it is genuinely ambiguous — the query is so short or
context-free that you cannot reliably assign followup or new_request with confidence ≥ 0.75.

════════════════════════════════════════
 CONFIDENCE SCORING
════════════════════════════════════════
0.90-1.00  Clear-cut
0.75-0.89  Reasonably confident — assign followup or new_request
< 0.75     Uncertain — set relationship_type to "uncertain"

════════════════════════════════════════
 EXAMPLES
════════════════════════════════════════
Prev: "Show productivity report for Kalai"   Curr: "Compare with previous month"   → followup  0.95
Prev: "Show productivity report for Kalai"   Curr: "Only Alex"                      → followup  0.88
Prev: "Show productivity report for Kalai"   Curr: "Show feedback report for Alex"  → new_request 0.97
Prev: "Show non-compliance report"           Curr: "Show certification report"       → new_request 0.95
Prev: "List goals for Baskar"                Curr: "Add email column"                → followup  0.99
Prev: "List goals for Baskar"                Curr: "Show skills report"              → new_request 0.95
Prev: "List goals for Baskar"                Curr: "For Alex too"                   → followup  0.85
Prev: "KRA report for Q1 2026 department wise" Curr: "Q2"                           → uncertain 0.65

════════════════════════════════════════
 OUTPUT (JSON ONLY — no markdown, no text outside the braces)
════════════════════════════════════════
{{
  "relationship_type": "followup|new_request|uncertain",
  "confidence": 0.95,
  "clarification_question": "string or null",
  "reasoning": "one-sentence explanation"
}}

Rules:
- For "uncertain" → clarification_question MUST be non-null
- For "followup" / "new_request" → clarification_question is null
- Return ONLY valid JSON — no prose before or after
"""


class RelationshipClassifier:

    def __init__(self) -> None:
        self._llm: Optional[ChatOpenAI] = None

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY is not set.")
            self._llm = ChatOpenAI(
                model=settings.LLM_MODEL,
                temperature=0.0,
                openai_api_key=settings.OPENAI_API_KEY,
                max_tokens=200,
                request_timeout=15,
            )
        return self._llm

    def classify(
        self,
        current_query: str,
        previous_context: str = "",
    ) -> Dict[str, Any]:
        """Classify current_query as followup, new_request, or uncertain.

        Returns a dict with keys:
          relationship_type: str
          confidence: float
          clarification_question: str | None
          reasoning: str
        """
        # No prior history → always a fresh request
        if not previous_context or not previous_context.strip():
            return self._make("new_request", 1.0, None, "No previous context")

        safe_ctx = previous_context[:1500].replace("{", "{{").replace("}", "}}")
        system = _SYSTEM_PROMPT.format(previous_context=safe_ctx)

        try:
            llm = self._get_llm()
            response = llm.invoke([
                SystemMessage(content=system),
                HumanMessage(content=f"CURRENT QUERY: {current_query}"),
            ])
            raw = response.content.strip()
            logger.debug("[relationship_classifier] raw: %s", raw[:300])
            return self._parse(raw)
        except Exception as exc:
            logger.error("[relationship_classifier] LLM failed: %s", exc)
            # Safe default: treat as new_request to avoid wrong follow-up injection
            return self._make("new_request", 1.0, None, f"LLM error — defaulting to new_request")

    def _parse(self, raw: str) -> Dict[str, Any]:
        match = re.search(r"\{[\s\S]*\}", raw)
        if not match:
            logger.error("[relationship_classifier] no JSON in response: %s", raw[:100])
            return self._make("new_request", 1.0, None, "parse error")

        try:
            data = json.loads(match.group(0))
        except json.JSONDecodeError as exc:
            logger.error("[relationship_classifier] JSON decode error: %s", exc)
            return self._make("new_request", 1.0, None, "json error")

        rel_type = data.get("relationship_type", "new_request")
        if rel_type not in ("followup", "new_request", "uncertain"):
            rel_type = "new_request"

        confidence = float(data.get("confidence", 0.8))

        # Enforce threshold — low confidence forces uncertain
        if confidence < 0.75 and rel_type != "uncertain":
            rel_type = "uncertain"

        clarification_q = data.get("clarification_question") if rel_type == "uncertain" else None
        if rel_type == "uncertain" and not clarification_q:
            clarification_q = (
                "Are you referring to the previous report, or would you like to start a new report?"
            )

        result = self._make(
            rel_type,
            confidence,
            clarification_q,
            data.get("reasoning", ""),
        )
        logger.info(
            "[relationship_classifier] type=%s confidence=%.2f | %s",
            rel_type, confidence, result["reasoning"][:80],
        )
        return result

    @staticmethod
    def _make(
        rel_type: str,
        confidence: float,
        clarification_question: Optional[str],
        reasoning: str,
    ) -> Dict[str, Any]:
        return {
            "relationship_type": rel_type,
            "confidence": confidence,
            "clarification_question": clarification_question,
            "reasoning": reasoning,
        }


relationship_classifier = RelationshipClassifier()
