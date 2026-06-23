"""
Tests for SchemaMetadataService.

DB access is mocked — these tests verify fallback behaviour, TTL caching,
and the static option lists that are never fetched from the DB.
"""
import time
from unittest.mock import MagicMock, patch

import pytest

from app.services.schema_metadata_service import (
    SchemaMetadataService,
    _FALLBACK_STREAMS,
    _FALLBACK_STATUSES,
    _FALLBACK_DESIGNATIONS,
    _FALLBACK_CATEGORIES,
    _REPORT_TYPES,
    _KRA_METRIC_OPTIONS,
    _PERFORMANCE_TYPE_OPTIONS,
    _PERIOD_OPTIONS,
    _COMPLIANCE_SCOPE_OPTIONS,
)


@pytest.fixture()
def svc():
    """Return a fresh service instance (no shared cache state)."""
    return SchemaMetadataService()


# ── Static lists are returned as copies ──────────────────────────────────────

def test_get_report_types_returns_all(svc):
    result = svc.get_report_types()
    assert result == list(_REPORT_TYPES)


def test_get_kra_metric_options_returns_all(svc):
    result = svc.get_kra_metric_options()
    assert result == list(_KRA_METRIC_OPTIONS)


def test_get_performance_type_options_returns_all(svc):
    result = svc.get_performance_type_options()
    assert result == list(_PERFORMANCE_TYPE_OPTIONS)


def test_get_period_options_returns_all(svc):
    result = svc.get_period_options()
    assert result == list(_PERIOD_OPTIONS)


def test_get_compliance_scope_options_returns_all(svc):
    result = svc.get_compliance_scope_options()
    assert result == list(_COMPLIANCE_SCOPE_OPTIONS)


def test_static_lists_return_copies(svc):
    """Mutating the returned list must not affect subsequent calls."""
    a = svc.get_report_types()
    a.clear()
    b = svc.get_report_types()
    assert len(b) > 0


# ── Fallback behaviour when DB is unreachable ─────────────────────────────────

def _make_query_raiser(exc_msg="connection refused"):
    """Return a _query method that always raises."""
    def _fail(*_args, **_kwargs):
        raise OSError(exc_msg)
    return _fail


def test_get_streams_falls_back_on_db_error(svc):
    svc._query = _make_query_raiser()
    result = svc.get_streams()
    assert result == _FALLBACK_STREAMS


def test_get_statuses_falls_back_on_db_error(svc):
    svc._query = _make_query_raiser()
    result = svc.get_statuses()
    assert result == _FALLBACK_STATUSES


def test_get_designations_falls_back_on_db_error(svc):
    svc._query = _make_query_raiser()
    result = svc.get_designations()
    assert result == _FALLBACK_DESIGNATIONS


def test_get_categories_falls_back_on_db_error(svc):
    svc._query = _make_query_raiser()
    result = svc.get_categories()
    assert result == _FALLBACK_CATEGORIES


def test_fallback_also_cached(svc):
    """Fallback values must be cached so a second call doesn't retry the DB."""
    call_count = 0

    def _fail(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        raise OSError("down")

    svc._query = _fail
    svc.get_streams()
    svc.get_streams()
    assert call_count == 1, "DB should only be called once — second call should use cache"


# ── Live DB fetch ─────────────────────────────────────────────────────────────

def test_get_streams_uses_db_values(svc):
    svc._query = lambda sql, col=0: ["Backend", "QA", "Frontend"]
    result = svc.get_streams()
    assert result == ["Backend", "QA", "Frontend"]


def test_get_streams_empty_result_falls_back(svc):
    """When DB returns empty list, fallback should be used instead."""
    call_count = 0

    def _empty(*_args, **_kwargs):
        nonlocal call_count
        call_count += 1
        return []

    svc._query = _empty
    result = svc.get_streams()
    # Both active and non-active queries returned empty — expect fallback
    assert result == _FALLBACK_STREAMS


# ── TTL cache behaviour ───────────────────────────────────────────────────────

def test_cached_result_returned_on_second_call(svc):
    call_count = 0

    def _counting(sql, col=0):
        nonlocal call_count
        call_count += 1
        return ["QA", "Dev"]

    svc._query = _counting
    svc.get_streams()
    svc.get_streams()
    assert call_count == 1


def test_expired_cache_triggers_refetch(svc):
    call_count = 0

    def _counting(sql, col=0):
        nonlocal call_count
        call_count += 1
        return ["QA"]

    svc._query = _counting
    svc.get_streams()

    # Force cache expiry by back-dating the timestamp
    svc._cache["streams"] = (svc._cache["streams"][0], time.time() - 400)
    svc.get_streams()
    assert call_count == 2


# ── invalidate() ─────────────────────────────────────────────────────────────

def test_invalidate_specific_key_clears_only_that_key(svc):
    svc._query = lambda sql, col=0: ["QA"]
    svc.get_streams()
    svc.get_statuses()
    assert "streams" in svc._cache
    assert "statuses" in svc._cache

    svc.invalidate("streams")
    assert "streams" not in svc._cache
    assert "statuses" in svc._cache


def test_invalidate_none_clears_all(svc):
    svc._query = lambda sql, col=0: ["QA"]
    svc.get_streams()
    svc.get_statuses()
    svc.invalidate()
    assert svc._cache == {}


# ── cache_info() ─────────────────────────────────────────────────────────────

def test_cache_info_returns_ages(svc):
    svc._query = lambda sql, col=0: ["QA"]
    svc.get_streams()
    info = svc.cache_info()
    assert "streams" in info
    assert isinstance(info["streams"], float)
    assert info["streams"] >= 0
