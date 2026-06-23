"""
Unit tests for KRAClarificationDetector.

Run with:
    cd backend && python -m pytest tests/test_clarification_agent.py -v
"""

import pytest
from unittest.mock import MagicMock, patch

from app.agents.clarification_agent import KRAClarificationDetector


# ── Fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def det():
    """Detector with DB and context-manager calls stubbed out."""
    d = KRAClarificationDetector()
    d._get_pending_clarification = MagicMock(return_value=None)
    return d


# ── Helpers ───────────────────────────────────────────────────────────────────

def _needs(result):
    return result["needs_clarification"]

def _reason(result):
    return result["reason"]

def _question(result):
    return result["question"]

def _options(result):
    return result["options"]

def _slots(result):
    return result["missing_slots"]


# ─────────────────────────────────────────────────────────────────────────────
# 1. Comparison — missing stream names
# ─────────────────────────────────────────────────────────────────────────────

class TestComparison:

    def test_compare_two_streams_asks_which_streams(self, det):
        r = det.detect("Compare the two streams.")
        assert _needs(r) is True
        assert _reason(r) == "missing_stream_names"
        assert "stream" in _question(r).lower()
        assert "stream_1" in _slots(r) and "stream_2" in _slots(r)

    def test_compare_both_streams_asks_which_streams(self, det):
        r = det.detect("Compare both streams.")
        assert _needs(r) is True
        assert _reason(r) == "missing_stream_names"

    def test_compare_two_streams_for_compliance_includes_topic(self, det):
        r = det.detect("Compare the two streams for compliance.")
        assert _needs(r) is True
        assert _reason(r) == "missing_stream_names"
        # Question must mention the topic "compliance"
        assert "compliance" in _question(r).lower()

    def test_compare_two_streams_for_performance_includes_topic(self, det):
        r = det.detect("Compare the two streams for performance.")
        assert _needs(r) is True
        assert "performance" in _question(r).lower()

    def test_compare_named_streams_no_clarification(self, det):
        """Both stream names already provided → generate SQL directly."""
        r = det.detect("Compare QA and Dev streams.")
        assert _needs(r) is False

    def test_compare_named_streams_with_period_no_clarification(self, det):
        r = det.detect("Compare QA and Development streams for last quarter.")
        assert _needs(r) is False

    def test_compare_named_streams_versus_syntax(self, det):
        r = det.detect("QA vs DevOps stream comparison.")
        assert _needs(r) is False

    def test_compare_streams_options_from_schema_service(self, det):
        with patch(
            "app.agents.clarification_agent.schema_metadata_service.get_streams",
            return_value=["QA", "Development", "DevOps"],
        ) as mock_get:
            r = det.detect("Compare the two streams.")
            mock_get.assert_called_once()
        assert "QA" in _options(r)

    def test_stream_comparison_without_compare_keyword_no_trigger(self, det):
        """Mentions streams but no comparison intent → no clarification."""
        r = det.detect("Show me QA stream employees.")
        assert _needs(r) is False


# ─────────────────────────────────────────────────────────────────────────────
# 2. Performance report
# ─────────────────────────────────────────────────────────────────────────────

class TestPerformanceReport:

    def test_vague_performance_asks_type(self, det):
        r = det.detect("Show me the performance report.")
        assert _needs(r) is True
        assert _reason(r) == "missing_performance_type"
        assert "performance report" in _question(r).lower()

    def test_performance_options_correct(self, det):
        r = det.detect("Show me the performance report.")
        opts = _options(r)
        assert any("employee" in o.lower() for o in opts)
        assert any("stream" in o.lower() for o in opts)
        assert any("team" in o.lower() for o in opts)
        assert any("company" in o.lower() for o in opts)

    def test_performance_missing_slots_include_period(self, det):
        r = det.detect("Show me the performance report.")
        assert "period" in _slots(r)

    def test_generate_performance_report_also_vague(self, det):
        r = det.detect("Generate the performance report.")
        assert _needs(r) is True
        assert _reason(r) == "missing_performance_type"

    def test_employee_performance_report_no_clarification(self, det):
        """Sub-type 'employee' present → specific enough."""
        r = det.detect("Show employee performance report.")
        assert _needs(r) is False

    def test_team_performance_no_clarification(self, det):
        r = det.detect("Show team performance report.")
        assert _needs(r) is False

    def test_stream_wise_performance_no_clarification(self, det):
        r = det.detect("Show stream-wise performance report.")
        assert _needs(r) is False

    def test_performance_with_named_person_no_clarification(self, det):
        r = det.detect("Show performance report for John Smith.")
        assert _needs(r) is False

    def test_performance_company_wide_no_clarification(self, det):
        r = det.detect("Show company-wide performance report.")
        assert _needs(r) is False


# ─────────────────────────────────────────────────────────────────────────────
# 3. KRA health check ("How are we doing?")
# ─────────────────────────────────────────────────────────────────────────────

class TestHealthCheck:

    def test_how_are_we_doing_asks_metric(self, det):
        r = det.detect("How are we doing?")
        assert _needs(r) is True
        assert _reason(r) == "missing_kra_metric"

    def test_health_question_text(self, det):
        r = det.detect("How are we doing?")
        q = _question(r).lower()
        assert "kra" in q or "metric" in q

    def test_health_options_correct(self, det):
        r = det.detect("How are we doing?")
        opts = _options(r)
        assert any("goal" in o.lower() for o in opts)
        assert any("compliance" in o.lower() for o in opts)

    def test_health_missing_slots_include_period(self, det):
        r = det.detect("How are we doing?")
        assert "period" in _slots(r)

    def test_hows_our_performance_triggers_metric(self, det):
        r = det.detect("How's our performance?")
        assert _needs(r) is True
        assert _reason(r) == "missing_kra_metric"

    def test_how_do_we_stand_triggers_metric(self, det):
        r = det.detect("How do we stand?")
        assert _needs(r) is True
        assert _reason(r) == "missing_kra_metric"

    def test_health_not_confused_with_vague_report(self, det):
        """'How are we doing?' must map to missing_kra_metric, not missing_report_type."""
        r = det.detect("How are we doing?")
        assert _reason(r) != "missing_report_type"


# ─────────────────────────────────────────────────────────────────────────────
# 4. Compliance numbers
# ─────────────────────────────────────────────────────────────────────────────

class TestComplianceNumbers:

    def test_compliance_numbers_asks_scope(self, det):
        r = det.detect("Pull up the compliance numbers.")
        assert _needs(r) is True
        assert _reason(r) == "missing_compliance_scope"

    def test_compliance_question_text(self, det):
        r = det.detect("Pull up the compliance numbers.")
        assert "compliance" in _question(r).lower()

    def test_compliance_missing_slots_include_period(self, det):
        r = det.detect("Pull up the compliance numbers.")
        assert "period" in _slots(r)

    def test_compliance_options_correct(self, det):
        r = det.detect("Pull up the compliance numbers.")
        opts = _options(r)
        assert any("company" in o.lower() for o in opts)
        assert any("stream" in o.lower() for o in opts)
        assert any("team" in o.lower() for o in opts)
        assert any("employee" in o.lower() for o in opts)

    def test_compliance_stats_also_vague(self, det):
        r = det.detect("Show me the compliance stats.")
        assert _needs(r) is True
        assert _reason(r) == "missing_compliance_scope"

    def test_company_wide_compliance_no_clarification(self, det):
        r = det.detect("Show company-wide compliance numbers.")
        assert _needs(r) is False

    def test_stream_wise_compliance_no_clarification(self, det):
        r = det.detect("Show stream-wise compliance numbers.")
        assert _needs(r) is False


# ─────────────────────────────────────────────────────────────────────────────
# 5. "Last period" / ambiguous period
# ─────────────────────────────────────────────────────────────────────────────

class TestLastPeriod:

    def test_generic_report_last_period_asks_both(self, det):
        r = det.detect("Generate the report for last period.")
        assert _needs(r) is True
        assert _reason(r) == "ambiguous_report_and_period"
        assert "report" in _question(r).lower()
        assert "period" in _question(r).lower()
        assert "report_type" in _slots(r)
        assert "period" in _slots(r)

    def test_compliance_last_period_asks_period_only(self, det):
        r = det.detect("Show the compliance report for last period.")
        assert _needs(r) is True
        assert _reason(r) == "ambiguous_period"
        assert "report_type" not in _slots(r)

    def test_period_options_shown(self, det):
        r = det.detect("Generate the report for last period.")
        opts = _options(r)
        assert any("month" in o.lower() for o in opts)
        assert any("quarter" in o.lower() for o in opts)

    def test_report_type_options_also_shown_for_ambiguous_report_and_period(self, det):
        """Options for ambiguous_report_and_period must include report types, not just periods."""
        r = det.detect("Generate the report for last period.")
        opts = _options(r)
        # Should include at least one known report type
        all_opts_lower = " ".join(o.lower() for o in opts)
        assert "compliance" in all_opts_lower or "kra" in all_opts_lower or "goals" in all_opts_lower

    def test_merged_last_period_with_concrete_period_no_clarification(self, det):
        """After merge, 'last period' + 'last month' in same string must not re-trigger step 6."""
        # Simulate merged answer: original "last period" + answer "Compliance report, last month"
        merged = "Generate the report for last period. — Compliance report, last month"
        r = det.detect(merged)
        # Concrete period "last month" is present → step 6 must NOT fire
        assert _reason(r) != "ambiguous_period"
        assert _reason(r) != "ambiguous_report_and_period"

    def test_previous_period_also_triggers(self, det):
        r = det.detect("Show previous period numbers.")
        assert _needs(r) is True
        assert "period" in _slots(r)

    def test_last_month_no_clarification(self, det):
        """'Last month' is a concrete period — no clarification needed."""
        r = det.detect("Show compliance report for last month.")
        # Should not trigger ambiguous_period; may still ask scope/period for compliance
        # but reason must NOT be ambiguous_period
        assert _reason(r) != "ambiguous_period"

    def test_last_quarter_no_clarification(self, det):
        r = det.detect("Show KRA summary for last quarter.")
        assert _needs(r) is False


# ─────────────────────────────────────────────────────────────────────────────
# 6. "Give me the usual report"
# ─────────────────────────────────────────────────────────────────────────────

class TestUsualReport:

    def test_usual_report_asks_which_report(self, det):
        r = det.detect("Give me the usual report.")
        assert _needs(r) is True
        assert _reason(r) == "missing_report_type"

    def test_standard_report_asks_which_report(self, det):
        r = det.detect("Show me the standard report.")
        assert _needs(r) is True
        assert _reason(r) == "missing_report_type"

    def test_default_report_asks_which_report(self, det):
        r = det.detect("Get me the default report.")
        assert _needs(r) is True
        assert _reason(r) == "missing_report_type"

    def test_report_type_options_shown(self, det):
        r = det.detect("Give me the usual report.")
        opts = _options(r)
        assert any("compliance" in o.lower() or "kra" in o.lower() for o in opts)

    def test_show_me_report_no_qualifiers_asks_type(self, det):
        r = det.detect("Show me the report.")
        assert _needs(r) is True
        assert _reason(r) == "missing_report_type"


# ─────────────────────────────────────────────────────────────────────────────
# 7. "Show remarks for the team"
# ─────────────────────────────────────────────────────────────────────────────

class TestTeamRemarks:

    def test_vague_team_remarks_asks_team_and_period(self, det):
        r = det.detect("Show remarks for the team.")
        assert _needs(r) is True
        assert _reason(r) == "missing_team"

    def test_team_remarks_question_mentions_team_and_period(self, det):
        r = det.detect("Show remarks for the team.")
        q = _question(r).lower()
        assert "team" in q or "lead" in q
        assert "period" in q

    def test_team_remarks_missing_slots(self, det):
        r = det.detect("Show remarks for the team.")
        assert "team_or_lead" in _slots(r)
        assert "period" in _slots(r)

    def test_get_remarks_for_team_also_triggers(self, det):
        r = det.detect("Get the remarks for the team.")
        assert _needs(r) is True
        assert _reason(r) == "missing_team"

    def test_show_all_remarks_for_team_triggers(self, det):
        r = det.detect("Show all remarks for the team.")
        assert _needs(r) is True
        assert _reason(r) == "missing_team"


# ─────────────────────────────────────────────────────────────────────────────
# 8. Specific queries — no clarification needed
# ─────────────────────────────────────────────────────────────────────────────

class TestNoClarificationNeeded:

    def test_specific_employee_and_period(self, det):
        r = det.detect("Show goals for John Smith in Q3 2025.")
        assert _needs(r) is False

    def test_specific_compliance_with_scope_and_period(self, det):
        r = det.detect("Show compliance report for QA stream in January 2025.")
        assert _needs(r) is False

    def test_specific_team_performance_with_period(self, det):
        r = det.detect("Show team performance report for last quarter.")
        assert _needs(r) is False

    def test_employee_id_query(self, det):
        r = det.detect("Show KRA goals for EMP00123.")
        assert _needs(r) is False

    def test_specific_period_monthly_report(self, det):
        r = det.detect("Show KRA summary for Q2 2025.")
        assert _needs(r) is False

    def test_named_employee_in_query(self, det):
        r = det.detect("Show compliance for Ranjith Kumar in this quarter.")
        assert _needs(r) is False

    def test_at_risk_goals_with_period(self, det):
        r = det.detect("Show at-risk goals for this quarter.")
        assert _needs(r) is False

    def test_missing_remarks_with_period(self, det):
        r = det.detect("Show missing remarks for last month.")
        assert _needs(r) is False

    def test_all_employees_compliance(self, det):
        r = det.detect("Show compliance for all employees.")
        assert _needs(r) is False


# ─────────────────────────────────────────────────────────────────────────────
# 9. Prompt merging (_merge_prompt used by routes.py clarify endpoint)
# ─────────────────────────────────────────────────────────────────────────────

class TestMergePrompt:

    @pytest.fixture
    def d(self):
        return KRAClarificationDetector()

    def test_stream_comparison_answer_rebuilt(self, d):
        merged = d._merge_prompt(
            "Compare the two streams.",
            "QA and Development",
            ["stream_1", "stream_2"],
        )
        assert "QA" in merged
        assert "Development" in merged
        assert "Compare" in merged

    def test_full_sentence_answer_used_as_is(self, d):
        merged = d._merge_prompt(
            "Show me the performance report.",
            "Show employee performance report for last quarter",
            ["report_type"],
        )
        # ≥5 words → answer used directly
        assert "employee" in merged.lower()
        assert "quarter" in merged.lower()

    def test_short_answer_appended_to_original(self, d):
        merged = d._merge_prompt(
            "How are we doing?",
            "Goal completion",
            ["kra_metric", "period"],
        )
        # Short answer (<5 words) → "original — answer"
        assert "How are we doing" in merged
        assert "Goal completion" in merged

    def test_short_compliance_answer_appended(self, d):
        merged = d._merge_prompt(
            "Pull up the compliance numbers.",
            "Stream-wise compliance",
            ["scope", "period"],
        )
        assert "compliance" in merged.lower()

    def test_team_all_teams_answer_replaces_the_team(self, d):
        """'All teams' answer must replace 'the team' so _TEAM_VAGUE_RE cannot re-fire."""
        merged = d._merge_prompt(
            "Show remarks for the team.",
            "All teams",
            ["team_or_lead", "period"],
        )
        assert "all teams" in merged.lower()
        # "for the team" must NOT remain — that phrase re-triggers clarification
        assert "for the team" not in merged.lower()

    def test_team_specific_name_replaces_the_team(self, d):
        """Named lead answer must substitute 'the team' with lead's team."""
        merged = d._merge_prompt(
            "Show remarks for the team.",
            "Baskar",
            ["team_lead_name"],
        )
        assert "baskar" in merged.lower()
        assert "for the team" not in merged.lower()

    def test_merged_all_teams_does_not_retrigger_team_clarification(self, d):
        """'Show remarks for all teams' must NOT need further clarification."""
        d._get_pending_clarification = MagicMock(return_value=None)
        merged = d._merge_prompt("Show remarks for the team.", "All teams", ["team_or_lead", "period"])
        r = d.detect(merged)
        assert r["reason"] != "missing_team", f"merged={merged!r} still asks for team"

    def test_fresh_vague_query_still_asks_clarification(self, det):
        """Sending the same vague query again must NOT be treated as an answer."""
        r = det.detect("Compare the two streams.")
        assert r["needs_clarification"] is True
        assert r["reason"] == "missing_stream_names"
