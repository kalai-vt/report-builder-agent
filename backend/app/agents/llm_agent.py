import json
import logging
import re
from typing import Dict, Optional

from langchain_core.messages import HumanMessage
from langchain_openai import ChatOpenAI

from app.config import settings

logger = logging.getLogger(__name__)

_REQUIRED_KEYS = {"sql_query", "explanation", "columns", "filters"}


class LLMAgent:
    def __init__(self):
        self._llm: Optional[ChatOpenAI] = None

    def _get_llm(self) -> ChatOpenAI:
        if self._llm is None:
            if not settings.OPENAI_API_KEY:
                raise ValueError("OPENAI_API_KEY is not set. Add it to your .env file.")
            self._llm = ChatOpenAI(
                model=settings.LLM_MODEL,
                temperature=settings.LLM_TEMPERATURE,
                openai_api_key=settings.OPENAI_API_KEY,
                max_tokens=settings.LLM_MAX_TOKENS,
                request_timeout=60,
            )
        return self._llm

    def generate_sql(self, prompt: str) -> Dict:
        try:
            llm = self._get_llm()
            response = llm.invoke([HumanMessage(content=prompt)])
            raw = response.content.strip()
            logger.debug(f"LLM raw response: {raw[:300]}")
            return self._parse_response(raw)
        except Exception as e:
            logger.error(f"LLM call failed: {e}")
            return self._error_response(str(e))

    def _parse_response(self, raw: str) -> Dict:
        json_match = re.search(r"\{[\s\S]*\}", raw)
        if not json_match:
            logger.error("No JSON object found in LLM response")
            return self._error_response("LLM did not return a JSON object")

        try:
            data = json.loads(json_match.group(0))
        except json.JSONDecodeError as e:
            logger.error(f"JSON parse error: {e}")
            return self._error_response(f"JSON parse error: {e}")

        missing = _REQUIRED_KEYS - set(data.keys())
        if missing:
            logger.warning(f"LLM response missing keys: {missing}")

        return {
            "sql_query": data.get("sql_query", ""),
            "explanation": data.get("explanation", ""),
            "columns": data.get("columns", []),
            "filters": data.get("filters", []),
        }

    def _error_response(self, message: str) -> Dict:
        return {
            "sql_query": "",
            "explanation": "",
            "columns": [],
            "filters": [],
            "error": message,
        }

    def is_configured(self) -> bool:
        return bool(settings.OPENAI_API_KEY)


llm_agent = LLMAgent()
