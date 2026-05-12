"""
Tests for the query caching layer.

Run with:  cd backend && python -m pytest tests/test_cache.py -v
"""

import time

import pytest

from app.cache.query_cache import QueryCache


# ── Unit tests: QueryCache ────────────────────────────────────────────────────

class TestQueryCacheKeyGeneration:
    def setup_method(self):
        self.cache = QueryCache()

    def test_same_query_same_key(self):
        k1 = self.cache.make_key("u1", "Show all KRAs")
        k2 = self.cache.make_key("u1", "Show all KRAs")
        assert k1 == k2

    def test_case_insensitive(self):
        k1 = self.cache.make_key("u1", "Show All KRAs")
        k2 = self.cache.make_key("u1", "show all kras")
        assert k1 == k2

    def test_whitespace_normalized(self):
        k1 = self.cache.make_key("u1", "  show  all  kras  ")
        k2 = self.cache.make_key("u1", "show all kras")
        assert k1 == k2

    def test_different_users_different_keys(self):
        k1 = self.cache.make_key("user_a", "show all kras")
        k2 = self.cache.make_key("user_b", "show all kras")
        assert k1 != k2

    def test_different_queries_different_keys(self):
        k1 = self.cache.make_key("u1", "show all kras")
        k2 = self.cache.make_key("u1", "show active employees")
        assert k1 != k2

    def test_key_is_hex_string(self):
        key = self.cache.make_key("u1", "test query")
        assert len(key) == 32
        int(key, 16)  # raises if not valid hex


class TestQueryCacheSetGet:
    def setup_method(self):
        self.cache = QueryCache(ttl_seconds=60, max_size=10)

    def test_miss_on_empty_cache(self):
        assert self.cache.get("nonexistent") is None

    def test_set_then_get(self):
        self.cache.set("key1", {"data": [1, 2, 3], "row_count": 3})
        result = self.cache.get("key1")
        assert result == {"data": [1, 2, 3], "row_count": 3}

    def test_overwrite_existing_key(self):
        self.cache.set("key1", {"row_count": 1})
        self.cache.set("key1", {"row_count": 99})
        assert self.cache.get("key1")["row_count"] == 99

    def test_returns_none_after_ttl_expires(self):
        cache = QueryCache(ttl_seconds=1, max_size=10)
        cache.set("key1", {"row_count": 5})
        assert cache.get("key1") is not None
        time.sleep(1.1)
        assert cache.get("key1") is None

    def test_size_after_set(self):
        self.cache.set("a", {})
        self.cache.set("b", {})
        assert self.cache.size == 2

    def test_size_decreases_after_ttl(self):
        cache = QueryCache(ttl_seconds=1, max_size=10)
        cache.set("x", {})
        time.sleep(1.1)
        cache.get("x")          # triggers lazy eviction
        assert cache.size == 0


class TestQueryCacheLRUEviction:
    def test_evicts_lru_when_max_size_reached(self):
        cache = QueryCache(ttl_seconds=3600, max_size=3)
        cache.set("a", {"v": "a"})
        cache.set("b", {"v": "b"})
        cache.set("c", {"v": "c"})
        # Access "a" so "b" becomes LRU
        cache.get("a")
        cache.get("c")
        # Adding "d" should evict "b" (LRU)
        cache.set("d", {"v": "d"})
        assert cache.size == 3
        assert cache.get("b") is None   # evicted
        assert cache.get("a") is not None
        assert cache.get("c") is not None
        assert cache.get("d") is not None


class TestQueryCacheInvalidateAndClear:
    def setup_method(self):
        self.cache = QueryCache()

    def test_invalidate_existing_key(self):
        self.cache.set("k", {"row_count": 1})
        assert self.cache.invalidate("k") is True
        assert self.cache.get("k") is None

    def test_invalidate_missing_key_returns_false(self):
        assert self.cache.invalidate("no_such_key") is False

    def test_clear_removes_all_entries(self):
        self.cache.set("a", {})
        self.cache.set("b", {})
        count = self.cache.clear()
        assert count == 2
        assert self.cache.size == 0


class TestQueryCacheStats:
    def test_initial_stats(self):
        cache = QueryCache(ttl_seconds=60, max_size=100)
        s = cache.stats()
        assert s["hits"] == 0
        assert s["misses"] == 0
        assert s["size"] == 0
        assert s["hit_rate"] == 0.0

    def test_hit_miss_counts(self):
        cache = QueryCache()
        cache.set("k1", {"v": 1})
        cache.get("k1")   # hit
        cache.get("k1")   # hit
        cache.get("k2")   # miss
        s = cache.stats()
        assert s["hits"] == 2
        assert s["misses"] == 1
        assert s["hit_rate"] == pytest.approx(2 / 3, rel=1e-3)

    def test_expired_entry_counts_as_miss(self):
        cache = QueryCache(ttl_seconds=1)
        cache.set("k", {})
        time.sleep(1.1)
        cache.get("k")   # expired → miss
        assert cache.stats()["misses"] == 1
        assert cache.stats()["hits"] == 0


# ── Integration test: cache short-circuits LLM in the workflow ────────────────

class TestCacheIntegrationWithWorkflow:
    """
    Verifies that a second identical query is served from cache without
    calling the LLM.  We mock the LLM agent so the test runs offline.
    """

    def test_second_call_is_cache_hit(self, monkeypatch):
        import asyncio
        from app.cache.query_cache import QueryCache
        from app.graph import nodes as nodes_module

        # Replace the shared cache with a fresh one so tests are isolated
        fresh_cache = QueryCache(ttl_seconds=3600, max_size=100)
        monkeypatch.setattr(nodes_module, "query_cache", fresh_cache)

        call_count = {"n": 0}

        original_generate = nodes_module.llm_agent.generate_sql

        def mock_generate_sql(prompt: str):
            call_count["n"] += 1
            return {
                "sql_query": "SELECT 1",
                "explanation": "Mock result",
                "error": None,
            }

        monkeypatch.setattr(nodes_module.llm_agent, "generate_sql", mock_generate_sql)

        # Also mock DB execution so we don't need a real DB
        monkeypatch.setattr(
            nodes_module.db_manager,
            "execute_query",
            lambda sql: ([{"id": 1}], ["id"]),
        )

        from app.graph.workflow import run_report_agent

        query = "List all employees"
        r1 = asyncio.get_event_loop().run_until_complete(
            run_report_agent("demo_user", query)
        )
        r2 = asyncio.get_event_loop().run_until_complete(
            run_report_agent("demo_user", query)
        )

        assert call_count["n"] == 1, (
            f"LLM was called {call_count['n']} times — expected 1 (second call should be cached)"
        )
        assert r1.get("cache_hit") is False, "First call must NOT be a cache hit"
        assert r2.get("cache_hit") is True,  "Second call MUST be a cache hit"
        assert r1["row_count"] == r2["row_count"]

    def test_different_queries_both_call_llm(self, monkeypatch):
        import asyncio
        from app.cache.query_cache import QueryCache
        from app.graph import nodes as nodes_module

        fresh_cache = QueryCache(ttl_seconds=3600, max_size=100)
        monkeypatch.setattr(nodes_module, "query_cache", fresh_cache)

        call_count = {"n": 0}

        def mock_generate_sql(prompt: str):
            call_count["n"] += 1
            return {"sql_query": "SELECT 1", "explanation": "Mock", "error": None}

        monkeypatch.setattr(nodes_module.llm_agent, "generate_sql", mock_generate_sql)
        monkeypatch.setattr(
            nodes_module.db_manager,
            "execute_query",
            lambda sql: ([], []),
        )

        from app.graph.workflow import run_report_agent

        asyncio.get_event_loop().run_until_complete(
            run_report_agent("demo_user", "query one")
        )
        asyncio.get_event_loop().run_until_complete(
            run_report_agent("demo_user", "query two")
        )

        assert call_count["n"] == 2, "Two different queries must each call the LLM once"

    def test_cache_hit_respects_ttl(self, monkeypatch):
        import asyncio
        from app.cache.query_cache import QueryCache
        from app.graph import nodes as nodes_module

        fresh_cache = QueryCache(ttl_seconds=1, max_size=100)
        monkeypatch.setattr(nodes_module, "query_cache", fresh_cache)

        call_count = {"n": 0}

        def mock_generate_sql(prompt: str):
            call_count["n"] += 1
            return {"sql_query": "SELECT 1", "explanation": "Mock", "error": None}

        monkeypatch.setattr(nodes_module.llm_agent, "generate_sql", mock_generate_sql)
        monkeypatch.setattr(
            nodes_module.db_manager,
            "execute_query",
            lambda sql: ([], []),
        )

        from app.graph.workflow import run_report_agent

        asyncio.get_event_loop().run_until_complete(
            run_report_agent("demo_user", "ttl test query")
        )
        time.sleep(1.2)  # let TTL expire
        asyncio.get_event_loop().run_until_complete(
            run_report_agent("demo_user", "ttl test query")
        )

        assert call_count["n"] == 2, "After TTL expires, LLM must be called again"
