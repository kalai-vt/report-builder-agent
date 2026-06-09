"""
Tests for the conversation context management layer.

Covers all four problems fixed in this implementation:
  Problem 1 — Incorrect follow-up detection (is_followup was based on raw SQL presence)
  Problem 2 — Memory isolated per (user_id, chat_session_id)
  Problem 3 — Intelligent relationship detection replaces regex-only continuation check
  Problem 4 — Structured active report context replaces raw memory string

Run with:  cd backend && python -m pytest tests/test_context_management.py -v
"""

import json
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pytest

from app.agents.relationship_classifier import RelationshipClassifier
from app.memory.conversation_memory import ConversationMemoryManager
from app.services.context_manager import ActiveReportContextManager


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _mock_llm_response(classifier: RelationshipClassifier, json_text: str) -> None:
    mock_llm = MagicMock()
    mock_llm.invoke.return_value = MagicMock(content=json_text)
    classifier._llm = mock_llm


def _rc_json(rel_type: str, confidence: float, clarification: str = None) -> str:
    return json.dumps({
        "relationship_type": rel_type,
        "confidence": confidence,
        "clarification_question": clarification,
        "reasoning": f"test: {rel_type}",
    })


# ─────────────────────────────────────────────────────────────────────────────
# Test 1 — Follow-up query is classified as "followup"
# Problem 1 fix: classification is LLM-based, not raw SQL presence.
# ─────────────────────────────────────────────────────────────────────────────

class TestFollowupQueryClassification:
    """Verifies queries that extend the previous report are classified as followup."""

    def setup_method(self):
        self.rc = RelationshipClassifier()

    PREVIOUS_CTX = (
        "=== Conversation History ===\n"
        "User: Show productivity report for Kalai\n"
        "Assistant: Productivity report returned 5 rows.\n"
        "[SQL used: SELECT ... FROM user_goal_mapping ...]\n"
        "=== End History ==="
    )

    @pytest.mark.parametrize("current_query", [
        "Compare with previous month",
        "Only Alex",
        "Add email column",
        "Filter by completed goals",
        "Top 10 only",
        "Exclude completed goals",
        "Department wise",
        "Last month",
    ])
    def test_continuation_phrases_are_followup(self, current_query):
        _mock_llm_response(self.rc, _rc_json("followup", 0.92))
        result = self.rc.classify(current_query, previous_context=self.PREVIOUS_CTX)
        assert result["relationship_type"] == "followup"
        assert result["confidence"] >= 0.75

    def test_followup_has_no_clarification_question(self):
        _mock_llm_response(self.rc, _rc_json("followup", 0.95))
        result = self.rc.classify("Add email column", previous_context=self.PREVIOUS_CTX)
        assert result["clarification_question"] is None

    def test_followup_confidence_is_float(self):
        _mock_llm_response(self.rc, _rc_json("followup", 0.9))
        result = self.rc.classify("Only Alex", previous_context=self.PREVIOUS_CTX)
        assert isinstance(result["confidence"], float)


# ─────────────────────────────────────────────────────────────────────────────
# Test 2 — New unrelated query is classified as "new_request"
# Problem 1 fix: different business domain → must reset context.
# ─────────────────────────────────────────────────────────────────────────────

class TestNewRequestClassification:
    """Verifies that queries about different report types are not treated as follow-ups."""

    def setup_method(self):
        self.rc = RelationshipClassifier()

    PREVIOUS_CTX = (
        "=== Conversation History ===\n"
        "User: Show productivity report for Kalai\n"
        "Assistant: Returned 5 rows.\n"
        "[SQL used: SELECT ... FROM user_goal_mapping ...]\n"
        "=== End History ==="
    )

    @pytest.mark.parametrize("current_query,prev", [
        ("Show feedback report for Alex",   "User asked about productivity for Kalai."),
        ("Show certification report",       "User asked about non-compliance goals."),
        ("List skills for all employees",   "User asked about KRA goal status."),
        ("Show RNR nominations",            "User asked about goal completion rates."),
        ("Give me the employee master list","User asked about overdue goals."),
    ])
    def test_different_domain_queries_are_new_request(self, current_query, prev):
        ctx = f"=== Conversation History ===\n{prev}\n=== End History ==="
        _mock_llm_response(self.rc, _rc_json("new_request", 0.97))
        result = self.rc.classify(current_query, previous_context=ctx)
        assert result["relationship_type"] == "new_request"

    def test_new_request_when_no_previous_context(self):
        """Empty context must always return new_request without calling the LLM."""
        result = self.rc.classify("Show goals for Alex", previous_context="")
        assert result["relationship_type"] == "new_request"
        assert result["confidence"] == 1.0

    def test_new_request_when_context_is_whitespace(self):
        result = self.rc.classify("Show certification report", previous_context="   ")
        assert result["relationship_type"] == "new_request"


# ─────────────────────────────────────────────────────────────────────────────
# Test 3 — Session isolation
# Problem 2 fix: memory[user_id][session_id] — different sessions don't bleed.
# ─────────────────────────────────────────────────────────────────────────────

class TestSessionIsolation:
    """Verifies memory is properly isolated per (user_id, chat_session_id)."""

    def setup_method(self):
        self.mgr = ConversationMemoryManager.__new__(ConversationMemoryManager)
        self.mgr.max_history = 10
        self.mgr._fallback = {}
        self.mgr._db_available = False  # force in-memory path

    def test_session_a_and_b_are_independent(self):
        self.mgr.add_interaction(
            "user1", "Goals for Kalai", "Report ready.", sql_query="SELECT ...",
            chat_session_id="session_A",
        )
        self.mgr.add_interaction(
            "user1", "Certifications for Alex", "Report ready.", sql_query="SELECT cert ...",
            chat_session_id="session_B",
        )
        ctx_a = self.mgr.get_context_string("user1", chat_session_id="session_A")
        ctx_b = self.mgr.get_context_string("user1", chat_session_id="session_B")

        assert "Goals for Kalai" in ctx_a
        assert "Certifications for Alex" not in ctx_a

        assert "Certifications for Alex" in ctx_b
        assert "Goals for Kalai" not in ctx_b

    def test_new_session_starts_empty(self):
        self.mgr.add_interaction(
            "user1", "Old query", "Old answer.", sql_query="SELECT old ...",
            chat_session_id="session_old",
        )
        ctx_new = self.mgr.get_context_string("user1", chat_session_id="session_new")
        assert ctx_new == ""

    def test_same_user_different_sessions_dont_mix(self):
        self.mgr.add_interaction(
            "user1", "Q for session 1", "A1", sql_query="SQL1", chat_session_id="s1",
        )
        self.mgr.add_interaction(
            "user1", "Q for session 2", "A2", sql_query="SQL2", chat_session_id="s2",
        )
        history_s1 = self.mgr.get_history("user1", chat_session_id="s1")
        history_s2 = self.mgr.get_history("user1", chat_session_id="s2")

        queries_s1 = [h["content"] for h in history_s1]
        queries_s2 = [h["content"] for h in history_s2]

        assert "Q for session 1" in queries_s1
        assert "Q for session 2" not in queries_s1

        assert "Q for session 2" in queries_s2
        assert "Q for session 1" not in queries_s2

    def test_backward_compat_no_session_uses_user_only_key(self):
        self.mgr.add_interaction("user1", "legacy query", "legacy answer.",
                                 sql_query="SELECT ...", chat_session_id="")
        ctx = self.mgr.get_context_string("user1", chat_session_id="")
        assert "legacy query" in ctx


# ─────────────────────────────────────────────────────────────────────────────
# Test 4 — Confidence threshold triggers clarification
# Problem 3 fix: uncertain result → ask user instead of guessing.
# ─────────────────────────────────────────────────────────────────────────────

class TestConfidenceThreshold:
    """Verifies that low-confidence classifications become 'uncertain'."""

    def setup_method(self):
        self.rc = RelationshipClassifier()

    PREVIOUS_CTX = "=== Conversation History ===\nUser: Show KRA report\n=== End History ==="

    def test_low_confidence_forced_to_uncertain(self):
        """LLM says 'followup' with 0.60 confidence → classifier must return 'uncertain'."""
        _mock_llm_response(self.rc, _rc_json("followup", 0.60))
        result = self.rc.classify("Q2", previous_context=self.PREVIOUS_CTX)
        assert result["relationship_type"] == "uncertain"

    def test_uncertain_has_clarification_question(self):
        _mock_llm_response(self.rc, _rc_json("uncertain", 0.62,
                                              "Are you referring to the previous report?"))
        result = self.rc.classify("Q2", previous_context=self.PREVIOUS_CTX)
        assert result["relationship_type"] == "uncertain"
        assert result["clarification_question"]
        assert "?" in result["clarification_question"]

    def test_fallback_clarification_question_when_llm_omits_it(self):
        """uncertain without clarification_question → default question injected."""
        _mock_llm_response(self.rc, _rc_json("uncertain", 0.65, None))
        result = self.rc.classify("Q2", previous_context=self.PREVIOUS_CTX)
        assert result["relationship_type"] == "uncertain"
        assert result["clarification_question"]

    def test_above_threshold_not_uncertain(self):
        _mock_llm_response(self.rc, _rc_json("followup", 0.80))
        result = self.rc.classify("Add email column", previous_context=self.PREVIOUS_CTX)
        assert result["relationship_type"] == "followup"
        assert result["clarification_question"] is None

    def test_llm_error_defaults_to_new_request(self):
        """LLM failure must not raise — safe default is new_request."""
        mock_llm = MagicMock()
        mock_llm.invoke.side_effect = RuntimeError("timeout")
        self.rc._llm = mock_llm
        result = self.rc.classify("some query", previous_context=self.PREVIOUS_CTX)
        assert result["relationship_type"] == "new_request"


# ─────────────────────────────────────────────────────────────────────────────
# Test 5 — Active report context replaced on new_request
# Problem 4 fix: structured context reset when switching to unrelated report.
# ─────────────────────────────────────────────────────────────────────────────

class TestActiveReportReplacement:
    """Verifies the context manager resets context when relationship_type == new_request."""

    def setup_method(self):
        self.cm = ActiveReportContextManager()

    def test_reset_clears_previous_sql(self):
        self.cm.update("user1", "sess1",
                       generated_sql="SELECT * FROM user_goal_mapping",
                       last_query="Show KRA for Kalai",
                       report_type="kra_goals")
        self.cm.reset("user1", "sess1")
        ctx = self.cm.get("user1", "sess1")
        assert ctx["generated_sql"] == ""
        assert ctx["last_query"] == ""
        assert ctx["report_type"] == ""

    def test_reset_does_not_affect_other_sessions(self):
        self.cm.update("user1", "sess1", generated_sql="SQL_A", last_query="Query A")
        self.cm.update("user1", "sess2", generated_sql="SQL_B", last_query="Query B")
        self.cm.reset("user1", "sess1")
        ctx_b = self.cm.get("user1", "sess2")
        assert ctx_b["generated_sql"] == "SQL_B"

    def test_update_merges_filters(self):
        self.cm.update("user1", "sess1", filters={"status": "In Progress"})
        self.cm.update("user1", "sess1", filters={"department": "Engineering"})
        ctx = self.cm.get("user1", "sess1")
        assert ctx["filters"].get("status") == "In Progress"
        assert ctx["filters"].get("department") == "Engineering"

    def test_get_returns_empty_context_when_not_set(self):
        ctx = self.cm.get("brand_new_user", "brand_new_session")
        assert ctx["generated_sql"] == ""
        assert ctx["report_type"] == ""
        assert isinstance(ctx["filters"], dict)


# ─────────────────────────────────────────────────────────────────────────────
# Test 6 — Context reset behaviour
# Problem 1+4 fix: new_request path in context_manager always clears active context.
# ─────────────────────────────────────────────────────────────────────────────

class TestContextResetBehaviour:
    """Verifies reset vs load behaviour based on relationship_type."""

    def setup_method(self):
        self.cm = ActiveReportContextManager()

    def test_get_after_reset_returns_empty_generated_sql(self):
        self.cm.update("u1", "s1", generated_sql="SELECT ...", report_type="kra")
        self.cm.reset("u1", "s1")
        assert self.cm.get("u1", "s1")["generated_sql"] == ""

    def test_update_after_reset_starts_fresh(self):
        self.cm.update("u1", "s1", generated_sql="OLD SQL", report_type="kra")
        self.cm.reset("u1", "s1")
        self.cm.update("u1", "s1", generated_sql="NEW SQL", report_type="certifications")
        ctx = self.cm.get("u1", "s1")
        assert ctx["generated_sql"] == "NEW SQL"
        assert ctx["report_type"] == "certifications"

    def test_filters_cleared_after_reset(self):
        self.cm.update("u1", "s1", filters={"employee": "Kalai", "status": "Completed"})
        self.cm.reset("u1", "s1")
        ctx = self.cm.get("u1", "s1")
        assert ctx["filters"] == {}

    def test_reset_without_prior_update_does_not_raise(self):
        try:
            self.cm.reset("u_fresh", "s_fresh")
            ctx = self.cm.get("u_fresh", "s_fresh")
            assert ctx["generated_sql"] == ""
        except Exception as exc:
            pytest.fail(f"reset raised unexpectedly: {exc}")


# ─────────────────────────────────────────────────────────────────────────────
# Test 7 — Multi-turn report refinement
# Verifies the full follow-up chain: context preserved across multiple refinements.
# ─────────────────────────────────────────────────────────────────────────────

class TestMultiTurnRefinement:
    """Simulates a multi-turn follow-up chain and verifies context accumulates."""

    def setup_method(self):
        self.cm = ActiveReportContextManager()
        self.mem = ConversationMemoryManager.__new__(ConversationMemoryManager)
        self.mem.max_history = 10
        self.mem._fallback = {}
        self.mem._db_available = False

    def test_three_turn_refinement_context_accumulates(self):
        # Turn 1: initial request
        self.cm.update("user1", "sess1",
                       generated_sql="SELECT emp, goal FROM ugm",
                       last_query="Show KRA for Kalai",
                       report_type="kra_goals")

        # Turn 2: add filter
        self.cm.update("user1", "sess1",
                       generated_sql="SELECT emp, goal FROM ugm WHERE status='Completed'",
                       last_query="Show only completed goals",
                       filters={"status": "Completed"})

        # Turn 3: add another column
        self.cm.update("user1", "sess1",
                       generated_sql="SELECT emp, goal, email FROM ugm WHERE status='Completed'",
                       last_query="Add email column",
                       dimensions=["employee", "goal", "email"])

        ctx = self.cm.get("user1", "sess1")
        assert "email" in ctx["generated_sql"]
        assert ctx["filters"].get("status") == "Completed"
        assert "email" in ctx["dimensions"]
        assert ctx["report_type"] == "kra_goals"  # preserved from turn 1

    def test_memory_accumulates_across_turns(self):
        for i in range(3):
            self.mem.add_interaction(
                "user1", f"Query {i}", f"Answer {i}",
                sql_query=f"SELECT * FROM t{i}",
                chat_session_id="sess1",
            )
        ctx_str = self.mem.get_context_string("user1", chat_session_id="sess1")
        assert "Query 0" in ctx_str
        assert "Query 1" in ctx_str
        assert "Query 2" in ctx_str
        assert ctx_str.count("[SQL used:") == 3

    def test_new_request_after_followup_chain_clears_context(self):
        # Build up context across 2 follow-ups
        self.cm.update("user1", "sess1", generated_sql="SQL_STEP2", last_query="Follow-up 2",
                       filters={"status": "Completed"})

        # Then user asks a completely new question
        self.cm.reset("user1", "sess1")
        ctx = self.cm.get("user1", "sess1")

        assert ctx["generated_sql"] == ""
        assert ctx["filters"] == {}

    def test_relationship_classifier_follows_up_when_history_present(self):
        rc = RelationshipClassifier()
        # Simulate memory with prior SQL
        previous = (
            "=== Conversation History ===\n"
            "User: List all goals for Baskar\n"
            "Assistant: Found 8 rows.\n"
            "[SQL used: SELECT * FROM user_goal_mapping WHERE firstname LIKE '%Baskar%']\n"
            "=== End History ==="
        )
        _mock_llm_response(rc, _rc_json("followup", 0.91))
        result = rc.classify("Add the target date column", previous_context=previous)
        assert result["relationship_type"] == "followup"
        assert result["confidence"] >= 0.75
