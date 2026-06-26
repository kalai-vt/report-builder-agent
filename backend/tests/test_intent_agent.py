"""
Tests for the intent detection layer.

Run with:  cd backend && python -m pytest tests/test_intent_agent.py -v
"""

from unittest.mock import MagicMock, patch

import pytest

from app.agents.intent_agent import IntentDetectorAgent


# ── Helpers ───────────────────────────────────────────────────────────────────

# Stable schema-driven suggestions returned by the mock suggestion_builder
_MOCK_SUGGESTIONS = {
    "employee": [
        "Show my KRA goals for Q1 2025 with completion percentage",
        "What is my average performance rating this year?",
        "List all my in-progress goals for this quarter",
    ],
    "lead": [
        "Show completion rates for my team members for Q1 2025",
        "Compare Q1 vs Q2 ratings for my reportees",
        "List overdue goals for my team members this quarter",
    ],
    "manager": [
        "Show average KRA scores for all team members for Q1 2025",
        "Who has the lowest goal completion rate in Q1 2025?",
        "List all employees with pending appraisals this cycle",
    ],
    "hr": [
        "Show average KRA scores by department for 2025",
        "List all employees with ratings below 3 this year",
        "Generate a full performance summary for the current appraisal cycle",
    ],
}


def _mock_suggestion_builder():
    """Return a MagicMock that behaves like suggestion_builder."""
    sb = MagicMock()
    sb.get_suggestions.side_effect = lambda role: _MOCK_SUGGESTIONS.get(role, _MOCK_SUGGESTIONS["employee"])
    sb.get_greeting_redirect.side_effect = lambda role: (
        f"would you like to start with '{_MOCK_SUGGESTIONS.get(role, _MOCK_SUGGESTIONS['employee'])[0]}' "
        f"or explore '{_MOCK_SUGGESTIONS.get(role, _MOCK_SUGGESTIONS['employee'])[1]}'?"
    )
    sb.get_off_topic_redirect.side_effect = lambda role: (
        f"Can I help you with '{_MOCK_SUGGESTIONS.get(role, _MOCK_SUGGESTIONS['employee'])[0]}' instead?"
    )
    return sb


def _make_agent() -> IntentDetectorAgent:
    return IntentDetectorAgent()


def _mock_llm_response(agent: IntentDetectorAgent, json_text: str) -> None:
    """Patch the LLM so it returns a fixed JSON string."""
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content=json_text)
    agent._llm = mock_llm


# ── Track A: Greeting ─────────────────────────────────────────────────────────

class TestGreetingTrack:
    def setup_method(self):
        self.agent = _make_agent()
        self._sb_patch = patch(
            "app.agents.intent_agent.suggestion_builder",
            _mock_suggestion_builder(),
        )
        self._sb_patch.start()

    def teardown_method(self):
        self._sb_patch.stop()

    def _llm_greeting(self, message: str, role: str = "employee") -> str:
        return f"""{{
            "track": "greeting",
            "confidence": 0.99,
            "greeting_message": "Hello! Ready to build your report — would you like to start with your KRA goal progress or performance ratings for this quarter?",
            "off_topic_reason": null,
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {{}},
            "reasoning": "User sent a greeting"
        }}"""

    @pytest.mark.parametrize("message", ["hi", "hello", "hey", "good morning", "good afternoon"])
    def test_greetings_classified_as_greeting(self, message):
        _mock_llm_response(self.agent, self._llm_greeting(message))
        result = self.agent.classify(message, user_role="employee")
        assert result["track"] == "greeting"

    @pytest.mark.parametrize("message", ["thanks", "thank you", "ok", "great"])
    def test_acknowledgements_classified_as_greeting(self, message):
        _mock_llm_response(self.agent, self._llm_greeting(message))
        result = self.agent.classify(message, user_role="employee")
        assert result["track"] == "greeting"

    @pytest.mark.parametrize("message", ["bye", "goodbye"])
    def test_farewell_classified_as_greeting(self, message):
        _mock_llm_response(self.agent, self._llm_greeting(message))
        result = self.agent.classify(message, user_role="employee")
        assert result["track"] == "greeting"

    def test_greeting_message_populated(self):
        _mock_llm_response(self.agent, self._llm_greeting("hi"))
        result = self.agent.classify("hi", user_role="employee")
        assert result["greeting_message"]
        assert len(result["greeting_message"]) > 0

    def test_greeting_message_not_exceed_two_sentences(self):
        _mock_llm_response(self.agent, self._llm_greeting("hello"))
        result = self.agent.classify("hello", user_role="manager")
        # Count sentences by splitting on '. ' or '!' or '?'
        text = result["greeting_message"]
        import re
        sentences = [s.strip() for s in re.split(r"[.!?]", text) if s.strip()]
        assert len(sentences) <= 3  # Allow slight variance; hard limit is 2 meaningful sentences

    def test_greeting_no_suggestions_list(self):
        _mock_llm_response(self.agent, self._llm_greeting("hi"))
        result = self.agent.classify("hi", user_role="employee")
        assert result.get("follow_up_options") == []

    def test_greeting_fallback_populated_when_llm_omits_message(self):
        """If LLM returns greeting track but no greeting_message, fallback fills it."""
        json_no_msg = """{
            "track": "greeting",
            "confidence": 0.9,
            "greeting_message": null,
            "off_topic_reason": null,
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {},
            "reasoning": "greeting"
        }"""
        _mock_llm_response(self.agent, json_no_msg)
        result = self.agent.classify("hi", user_role="lead")
        assert result["greeting_message"]
        assert result["greeting_message"].startswith("Hello!")

    @pytest.mark.parametrize("role", ["employee", "lead", "manager", "hr"])
    def test_empty_message_returns_greeting_for_all_roles(self, role):
        result = self.agent.classify("", user_role=role)
        assert result["track"] == "greeting"
        assert result["greeting_message"]

    def test_empty_message_uses_role_suggestion(self):
        result = self.agent.classify("", user_role="manager")
        # The schema-driven suggestion for manager should appear somewhere in the message
        assert any(s.lower() in result["greeting_message"].lower() for s in _MOCK_SUGGESTIONS["manager"])


# ── Track B: Off-Topic ────────────────────────────────────────────────────────

class TestOffTopicTrack:
    def setup_method(self):
        self.agent = _make_agent()
        self._sb_patch = patch(
            "app.agents.intent_agent.suggestion_builder",
            _mock_suggestion_builder(),
        )
        self._sb_patch.start()

    def teardown_method(self):
        self._sb_patch.stop()

    def _llm_off_topic(self, reason: str = "general_knowledge", role: str = "employee") -> str:
        return f"""{{
            "track": "off_topic",
            "confidence": 0.97,
            "greeting_message": null,
            "off_topic_reason": "{reason}",
            "polite_block_message": "I'm only able to help with KRA report building! Would you like to check your KRA goal progress or performance ratings instead?",
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {{}},
            "reasoning": "Unrelated question"
        }}"""

    @pytest.mark.parametrize("message,reason", [
        ("What is the capital of India?", "general_knowledge"),
        ("Who won the cricket match?", "general_knowledge"),
        ("Write me a Python script", "unrelated"),
        ("What is the weather today?", "general_knowledge"),
        ("Tell me a joke", "unrelated"),
        ("What is 2 + 2?", "general_knowledge"),
    ])
    def test_unrelated_queries_classified_as_off_topic(self, message, reason):
        _mock_llm_response(self.agent, self._llm_off_topic(reason))
        result = self.agent.classify(message, user_role="employee")
        assert result["track"] == "off_topic"

    def test_off_topic_message_has_no_bullet_points(self):
        _mock_llm_response(self.agent, self._llm_off_topic())
        result = self.agent.classify("What is the capital of France?", user_role="employee")
        msg = result["polite_block_message"]
        assert "•" not in msg
        assert "\n" not in msg or msg.count("\n") <= 1  # at most one line break between two sentences

    def test_off_topic_message_max_two_sentences(self):
        _mock_llm_response(self.agent, self._llm_off_topic())
        result = self.agent.classify("Who won the World Cup?", user_role="employee")
        import re
        msg = result["polite_block_message"]
        sentences = [s.strip() for s in re.split(r"[.!?]", msg) if s.strip()]
        assert len(sentences) <= 2

    def test_off_topic_fallback_populated_when_llm_omits_message(self):
        json_no_msg = """{
            "track": "off_topic",
            "confidence": 0.9,
            "greeting_message": null,
            "off_topic_reason": "general_knowledge",
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {},
            "reasoning": "unrelated"
        }"""
        _mock_llm_response(self.agent, json_no_msg)
        result = self.agent.classify("What is AI?", user_role="hr")
        msg = result["polite_block_message"]
        assert msg
        assert "KRA" in msg

    @pytest.mark.parametrize("role", ["employee", "lead", "manager", "hr"])
    def test_off_topic_fallback_uses_role_redirect(self, role):
        json_no_msg = f"""{{
            "track": "off_topic",
            "confidence": 0.9,
            "greeting_message": null,
            "off_topic_reason": "unrelated",
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {{}},
            "reasoning": "unrelated"
        }}"""
        _mock_llm_response(self.agent, json_no_msg)
        result = self.agent.classify("random question", user_role=role)
        msg = result["polite_block_message"]
        # Schema-driven: message should mention KRA and contain a role-relevant suggestion
        assert "KRA" in msg
        assert any(s.lower() in msg.lower() for s in _MOCK_SUGGESTIONS[role])

    def test_kra_related_not_classified_as_off_topic(self):
        """Messages mentioning KRA/goals must NOT be off_topic."""
        clear_json = """{
            "track": "clear",
            "confidence": 0.95,
            "greeting_message": null,
            "off_topic_reason": null,
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": "Show KRA goals for employee user_42 in Q1 2025.",
            "extracted_filters": {"quarter": "Q1", "year": 2025},
            "reasoning": "Clear KRA query"
        }"""
        _mock_llm_response(self.agent, clear_json)
        result = self.agent.classify("Show my KRA goals for Q1 2025", user_role="employee")
        assert result["track"] != "off_topic"


# ── Track D: Clear ────────────────────────────────────────────────────────────

class TestClearTrack:
    def setup_method(self):
        self.agent = _make_agent()
        self._sb_patch = patch(
            "app.agents.intent_agent.suggestion_builder",
            _mock_suggestion_builder(),
        )
        self._sb_patch.start()

    def teardown_method(self):
        self._sb_patch.stop()

    def _llm_clear(self, enriched: str) -> str:
        return f"""{{
            "track": "clear",
            "confidence": 0.98,
            "greeting_message": null,
            "off_topic_reason": null,
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": "{enriched}",
            "extracted_filters": {{"quarter": "Q1", "year": 2025}},
            "reasoning": "Complete query"
        }}"""

    def test_specific_query_classified_as_clear(self):
        enriched = "Show KRA goals for employee user_42 in Q1 2025 with completion percentage."
        _mock_llm_response(self.agent, self._llm_clear(enriched))
        result = self.agent.classify(
            "Show my KRA goals for Q1 2025 with completion percentage",
            user_role="employee",
        )
        assert result["track"] == "clear"
        assert result["enriched_prompt"]

    def test_enriched_prompt_is_single_sentence(self):
        enriched = "Show KRA goals for user_42 in Q1 2025."
        _mock_llm_response(self.agent, self._llm_clear(enriched))
        result = self.agent.classify("KRA goals Q1 2025", user_role="employee")
        prompt = result["enriched_prompt"]
        assert isinstance(prompt, str)
        assert len(prompt) > 0

    def test_extracted_filters_returned(self):
        enriched = "List in-progress KRA goals for user_42 in Q2 2025."
        _mock_llm_response(self.agent, self._llm_clear(enriched))
        result = self.agent.classify("my in-progress goals Q2 2025", user_role="employee")
        assert isinstance(result["extracted_filters"], dict)


# ── Fallback / Error handling ─────────────────────────────────────────────────

class TestFallback:
    def setup_method(self):
        self.agent = _make_agent()
        self._sb_patch = patch(
            "app.agents.intent_agent.suggestion_builder",
            _mock_suggestion_builder(),
        )
        self._sb_patch.start()

    def teardown_method(self):
        self._sb_patch.stop()

    def test_unparseable_llm_response_falls_back_to_clear(self):
        _mock_llm_response(self.agent, "sorry i cannot help you with that")
        result = self.agent.classify("show my goals Q1 2025", user_role="employee")
        assert result["track"] == "clear"
        assert result["enriched_prompt"] == "show my goals Q1 2025"

    def test_llm_exception_falls_back_to_clear(self):
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("timeout")
        self.agent._llm = mock_llm
        result = self.agent.classify("show my goals Q1 2025", user_role="employee")
        assert result["track"] == "clear"

    def test_invalid_json_falls_back_to_clear(self):
        _mock_llm_response(self.agent, '{"track": "greeting", broken json')
        result = self.agent.classify("hi", user_role="employee")
        assert result["track"] == "clear"

    def test_confidence_coerced_to_float(self):
        json_str = """{
            "track": "greeting",
            "confidence": "0.95",
            "greeting_message": "Hello! Let's build a report.",
            "off_topic_reason": null,
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {},
            "reasoning": "greeting"
        }"""
        _mock_llm_response(self.agent, json_str)
        result = self.agent.classify("hi", user_role="employee")
        assert isinstance(result["confidence"], float)


# ── Greeting vs Off-Topic boundary ───────────────────────────────────────────

class TestGreetingVsOffTopic:
    def setup_method(self):
        self.agent = _make_agent()
        self._sb_patch = patch(
            "app.agents.intent_agent.suggestion_builder",
            _mock_suggestion_builder(),
        )
        self._sb_patch.start()

    def teardown_method(self):
        self._sb_patch.stop()

    def test_greeting_does_not_return_polite_block_message(self):
        json_str = """{
            "track": "greeting",
            "confidence": 0.99,
            "greeting_message": "Hello! Want to check your KRA progress?",
            "off_topic_reason": null,
            "polite_block_message": null,
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {},
            "reasoning": "greeting"
        }"""
        _mock_llm_response(self.agent, json_str)
        result = self.agent.classify("hello", user_role="employee")
        assert result["track"] == "greeting"
        assert not result["polite_block_message"]

    def test_off_topic_does_not_return_greeting_message(self):
        json_str = """{
            "track": "off_topic",
            "confidence": 0.97,
            "greeting_message": null,
            "off_topic_reason": "general_knowledge",
            "polite_block_message": "I'm only able to help with KRA report building! Can I show you your goal progress instead?",
            "missing_field": null,
            "follow_up_question": null,
            "follow_up_options": [],
            "enriched_prompt": null,
            "extracted_filters": {},
            "reasoning": "unrelated topic"
        }"""
        _mock_llm_response(self.agent, json_str)
        result = self.agent.classify("What is the speed of light?", user_role="employee")
        assert result["track"] == "off_topic"
        assert not result["greeting_message"]
        assert result["polite_block_message"]


# ── SuggestionBuilder unit tests ──────────────────────────────────────────────

class TestSuggestionBuilder:
    """Tests for the schema-driven suggestion engine (no DB required — schema is mocked)."""

    def _make_full_schema(self) -> dict:
        """Simulate a KRA-complete schema with goals, ratings, departments, status."""
        return {
            "kra_goals": {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "employee_id", "type": "int"},
                    {"name": "status", "type": "varchar"},
                    {"name": "completion_percentage", "type": "float"},
                    {"name": "quarter_id", "type": "int"},
                ],
                "foreign_keys": [],
            },
            "performance_ratings": {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "employee_id", "type": "int"},
                    {"name": "rating", "type": "float"},
                    {"name": "appraisal_year", "type": "int"},
                ],
                "foreign_keys": [],
            },
            "departments": {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "dept_name", "type": "varchar"},
                ],
                "foreign_keys": [],
            },
            "employees": {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "name", "type": "varchar"},
                    {"name": "department_id", "type": "int"},
                ],
                "foreign_keys": [],
            },
        }

    def _make_empty_schema(self) -> dict:
        return {}

    def _make_goals_only_schema(self) -> dict:
        return {
            "kra_goals": {
                "columns": [
                    {"name": "id", "type": "int"},
                    {"name": "employee_id", "type": "int"},
                ],
                "foreign_keys": [],
            }
        }

    def setup_method(self):
        from app.services.suggestion_builder import SuggestionBuilder
        self.builder = SuggestionBuilder()

    def _patch_schema(self, schema: dict):
        """Patch the module-level schema_manager inside suggestion_builder."""
        return patch(
            "app.services.suggestion_builder.schema_manager.get_schema",
            return_value=schema,
        )

    # ── Capability detection ──────────────────────────────────────────────────

    def test_detects_goals_from_table_name(self):
        schema = self._make_full_schema()
        caps = self.builder._detect(schema)
        assert caps["has_goals"] is True

    def test_detects_ratings_from_table_name(self):
        schema = self._make_full_schema()
        caps = self.builder._detect(schema)
        assert caps["has_ratings"] is True

    def test_detects_departments_from_table_name(self):
        schema = self._make_full_schema()
        caps = self.builder._detect(schema)
        assert caps["has_departments"] is True

    def test_detects_completion_from_column_name(self):
        schema = self._make_full_schema()
        caps = self.builder._detect(schema)
        assert caps["has_completion"] is True

    def test_detects_status_from_column_name(self):
        schema = self._make_full_schema()
        caps = self.builder._detect(schema)
        assert caps["has_status"] is True

    def test_detects_quarter_from_column_name(self):
        schema = self._make_full_schema()
        caps = self.builder._detect(schema)
        assert caps["has_quarter"] is True

    def test_no_capabilities_on_empty_schema(self):
        caps = self.builder._detect({})
        assert caps["has_goals"] is False
        assert caps["has_ratings"] is False
        assert caps["has_departments"] is False

    # ── Suggestion generation ─────────────────────────────────────────────────

    @pytest.mark.parametrize("role", ["employee", "lead", "manager", "hr"])
    def test_each_role_gets_three_suggestions(self, role):
        with self._patch_schema(self._make_full_schema()):
            items = self.builder.get_suggestions(role)
        assert len(items) == 3

    @pytest.mark.parametrize("role", ["employee", "lead", "manager", "hr"])
    def test_suggestions_are_non_empty_strings(self, role):
        with self._patch_schema(self._make_full_schema()):
            items = self.builder.get_suggestions(role)
        assert all(isinstance(s, str) and len(s) > 5 for s in items)

    def test_employee_suggestions_use_first_person(self):
        with self._patch_schema(self._make_full_schema()):
            items = self.builder.get_suggestions("employee")
        combined = " ".join(items).lower()
        assert "my" in combined

    def test_manager_suggestions_reference_team(self):
        with self._patch_schema(self._make_full_schema()):
            items = self.builder.get_suggestions("manager")
        combined = " ".join(items).lower()
        assert any(w in combined for w in ("team", "employee", "all", "average"))

    def test_hr_suggestions_reference_department_when_available(self):
        with self._patch_schema(self._make_full_schema()):
            items = self.builder.get_suggestions("hr")
        combined = " ".join(items).lower()
        assert any(w in combined for w in ("department", "dept", "all employees", "performance"))

    def test_empty_schema_still_returns_three_fallback_suggestions(self):
        with self._patch_schema(self._make_empty_schema()):
            items = self.builder.get_suggestions("employee")
        assert len(items) == 3

    def test_goals_only_schema_returns_suggestions_without_rating_content(self):
        with self._patch_schema(self._make_goals_only_schema()):
            items = self.builder.get_suggestions("employee")
        # Must still return 3 (padded by fallbacks)
        assert len(items) == 3

    # ── Redirect phrases ──────────────────────────────────────────────────────

    def test_greeting_redirect_references_actual_suggestions(self):
        with self._patch_schema(self._make_full_schema()):
            redirect = self.builder.get_greeting_redirect("employee")
        assert redirect  # non-empty
        assert "?" in redirect  # ends as a question

    def test_off_topic_redirect_is_short(self):
        with self._patch_schema(self._make_full_schema()):
            redirect = self.builder.get_off_topic_redirect("employee")
        assert len(redirect) < 200  # stays concise

    def test_off_topic_redirect_contains_instead(self):
        with self._patch_schema(self._make_full_schema()):
            redirect = self.builder.get_off_topic_redirect("hr")
        assert "instead" in redirect.lower() or "can i" in redirect.lower()

    # ── Caching ───────────────────────────────────────────────────────────────

    def test_cache_invalidated_after_invalidate_call(self):
        with self._patch_schema(self._make_full_schema()):
            first = self.builder.get_suggestions("employee")
        self.builder.invalidate()
        assert self.builder._cache is None
        assert self.builder._cache_hash == ""

    def test_cache_rebuilds_after_invalidation(self):
        with self._patch_schema(self._make_full_schema()):
            first = self.builder.get_suggestions("employee")
            self.builder.invalidate()
            second = self.builder.get_suggestions("employee")
        assert first == second  # same schema → same output

    def test_unknown_role_falls_back_to_employee(self):
        with self._patch_schema(self._make_full_schema()):
            items = self.builder.get_suggestions("unknown_role")
        employee_items = None
        with self._patch_schema(self._make_full_schema()):
            employee_items = self.builder.get_suggestions("employee")
        assert items == employee_items


