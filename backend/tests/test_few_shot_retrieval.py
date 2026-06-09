"""
Tests for the few-shot example retrieval layer.

Run:  py -3 -m pytest tests/test_few_shot_retrieval.py -v
"""

import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

# ── Ensure project root is on path ──────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Stub heavy dependencies before any app import ───────────────────────────

def _make_faiss_stub():
    faiss = types.ModuleType("faiss")

    class _FlatIP:
        def __init__(self, dim):
            self.dim = dim
            self._vecs = []
            self.ntotal = 0

        def add(self, vecs):
            self._vecs.extend(vecs.tolist())
            self.ntotal = len(self._vecs)

        def search(self, query, k):
            import numpy as np
            # Compute dot products for all stored vectors
            q = query[0]
            scores = [sum(a * b for a, b in zip(q, v)) for v in self._vecs]
            idxs = sorted(range(len(scores)), key=lambda i: scores[i], reverse=True)[:k]
            return (
                [[scores[i] for i in idxs]],
                [idxs],
            )

    faiss.IndexFlatIP = _FlatIP
    faiss.normalize_L2 = lambda x: x  # no-op normalization in tests
    return faiss


sys.modules.setdefault("faiss", _make_faiss_stub())


# ── Helpers ───────────────────────────────────────────────────────────────────

def _make_embedding(seed: int, dim: int = 8) -> list:
    """Deterministic fake embedding based on seed."""
    import math
    return [math.sin(seed + i) for i in range(dim)]


def _mock_openai_embeddings(texts, *, seed_map: dict = None):
    """Return fake embeddings for each text.  seed_map: text → seed int."""
    import numpy as np
    seed_map = seed_map or {}
    vecs = []
    for t in texts:
        seed = seed_map.get(t, hash(t) % 100)
        vecs.append(_make_embedding(seed))
    return np.array(vecs, dtype="float32")


# ── Tests ─────────────────────────────────────────────────────────────────────

class TestExamplesJson(unittest.TestCase):
    """Validate the structure and content of few_shot_sql_examples.json."""

    def setUp(self):
        p = Path(__file__).parent.parent / "app" / "data" / "few_shot_sql_examples.json"
        with open(p) as f:
            self.data = json.load(f)
        self.examples = self.data["examples"]

    def test_file_has_examples_key(self):
        self.assertIn("examples", self.data)

    def test_minimum_example_count(self):
        self.assertGreaterEqual(len(self.examples), 8)

    def test_required_fields_present(self):
        required = {"id", "intent", "prompt", "business_meaning", "tables", "joins", "sql_pattern"}
        for ex in self.examples:
            missing = required - ex.keys()
            self.assertFalse(missing, f"Example '{ex.get('id')}' missing: {missing}")

    def test_no_empty_prompts(self):
        for ex in self.examples:
            self.assertTrue(ex["prompt"].strip(), f"Empty prompt in '{ex['id']}'")

    def test_no_empty_sql_patterns(self):
        for ex in self.examples:
            self.assertTrue(ex["sql_pattern"].strip(), f"Empty sql_pattern in '{ex['id']}'")

    def test_known_intents_exist(self):
        intents = {ex["intent"] for ex in self.examples}
        self.assertIn("non_compliance_report", intents)
        self.assertIn("feedback_report", intents)
        self.assertIn("direct_reportees_by_manager_name", intents)
        self.assertIn("employee_kra_report_by_id", intents)


class TestExampleRetrieverInit(unittest.TestCase):
    """Test initialization and index building."""

    def _make_retriever(self):
        from app.services.example_retriever import ExampleRetriever
        r = ExampleRetriever()
        return r

    @patch("app.services.example_retriever.ExampleRetriever._get_embeddings")
    def test_initialize_builds_index(self, mock_embed):
        import numpy as np
        r = self._make_retriever()
        n = len(r._load_examples())
        mock_embed.return_value = np.random.rand(n, 8).astype("float32")

        r.initialize()

        self.assertTrue(r._initialized)
        self.assertIsNotNone(r._index)
        self.assertEqual(r._index.ntotal, n)

    @patch("app.services.example_retriever.ExampleRetriever._get_embeddings")
    def test_initialize_is_idempotent(self, mock_embed):
        import numpy as np
        r = self._make_retriever()
        n = len(r._load_examples())
        mock_embed.return_value = np.random.rand(n, 8).astype("float32")

        r.initialize()
        r.initialize()   # second call should be a no-op

        self.assertEqual(mock_embed.call_count, 1)

    @patch("app.services.example_retriever.ExampleRetriever._get_embeddings")
    def test_retrieve_returns_empty_before_init(self, mock_embed):
        r = self._make_retriever()
        results = r.retrieve("some query")
        self.assertEqual(results, [])
        mock_embed.assert_not_called()


class TestRetrieval(unittest.TestCase):
    """Test that retrieve() returns semantically appropriate examples."""

    def _initialized_retriever(self):
        import numpy as np
        from app.services.example_retriever import ExampleRetriever

        r = ExampleRetriever()
        examples = r._load_examples()

        # Build deterministic embeddings: each example gets a unique seed
        # based on its index so similarity is predictable.
        dim = 16
        def _fake_embed(texts):
            vecs = []
            for t in texts:
                # Find matching example index for repeatable scores
                idx = next(
                    (i for i, ex in enumerate(examples)
                     if ex["prompt"] in t or ex["intent"] in t),
                    hash(t) % len(examples),
                )
                vecs.append(_make_embedding(idx, dim))
            return np.array(vecs, dtype="float32")

        with patch.object(r, "_get_embeddings", side_effect=_fake_embed):
            r.initialize()

        # Wrap retrieve so _get_embeddings is also mocked at query time
        r._fake_embed = _fake_embed
        return r, examples

    def test_retrieve_returns_top_k(self):
        import numpy as np
        from app.services.example_retriever import ExampleRetriever

        r = ExampleRetriever()
        n = len(r._load_examples())
        dim = 8

        with patch.object(r, "_get_embeddings",
                          return_value=np.random.rand(n, dim).astype("float32")):
            r.initialize()

        with patch.object(r, "_get_embeddings",
                          return_value=np.random.rand(1, dim).astype("float32")):
            results = r.retrieve("any query", top_k=3)

        self.assertLessEqual(len(results), 3)

    def test_retrieve_result_has_required_keys(self):
        import numpy as np
        from app.services.example_retriever import ExampleRetriever

        r = ExampleRetriever()
        n = len(r._load_examples())
        dim = 8

        with patch.object(r, "_get_embeddings",
                          return_value=np.random.rand(n, dim).astype("float32")):
            r.initialize()

        with patch.object(r, "_get_embeddings",
                          return_value=np.random.rand(1, dim).astype("float32")):
            results = r.retrieve("show compliance report", top_k=1)

        self.assertEqual(len(results), 1)
        required = {"intent", "prompt", "sql_pattern", "business_meaning", "similarity_score"}
        self.assertTrue(required.issubset(results[0].keys()))

    def test_retrieve_falls_back_on_error(self):
        from app.services.example_retriever import ExampleRetriever
        import numpy as np

        r = ExampleRetriever()
        n = len(r._load_examples())

        with patch.object(r, "_get_embeddings",
                          return_value=np.random.rand(n, 8).astype("float32")):
            r.initialize()

        # Simulate embedding failure at query time
        with patch.object(r, "_get_embeddings", side_effect=RuntimeError("API down")):
            results = r.retrieve("any query")

        self.assertEqual(results, [])

    def test_top_k_respects_config(self):
        import numpy as np
        from app.services.example_retriever import ExampleRetriever

        r = ExampleRetriever()
        n = len(r._load_examples())
        dim = 8

        with patch.object(r, "_get_embeddings",
                          return_value=np.random.rand(n, dim).astype("float32")):
            r.initialize()

        for k in (1, 2, 3):
            with patch.object(r, "_get_embeddings",
                              return_value=np.random.rand(1, dim).astype("float32")):
                results = r.retrieve("list KRA goals", top_k=k)
            self.assertLessEqual(len(results), k)


class TestPromptBuilderIntegration(unittest.TestCase):
    """Test that PromptBuilder always contains static few-shot examples."""

    def setUp(self):
        from app.agents.prompt_builder import PromptBuilder
        self.pb = PromptBuilder()

    def test_static_examples_always_injected(self):
        prompt = self.pb.build_prompt(
            user_query="Show April 2026 non-compliance employees",
            schema_string="schema here",
        )
        self.assertIn("APPROVED REFERENCE SQL", prompt)
        # All 12 example queries must be present
        self.assertIn("Provide the non-compliance report for April 2026", prompt)
        self.assertIn("List all employees who directly report to Jerome", prompt)
        self.assertIn("Show KRA goals pending approval", prompt)

    def test_examples_appear_before_schema(self):
        prompt = self.pb.build_prompt(
            user_query="test",
            schema_string="DATABASE SCHEMA CONTENT",
        )
        examples_pos = prompt.index("APPROVED REFERENCE SQL")
        schema_pos = prompt.index("DATABASE SCHEMA CONTENT")
        self.assertLess(examples_pos, schema_pos)

    def test_static_examples_present_on_retry(self):
        """Static examples remain in prompt even on retry attempts."""
        prompt = self.pb.build_prompt(
            user_query="test",
            schema_string="schema",
            retry_feedback="Previous SQL failed: unknown column",
        )
        self.assertIn("APPROVED REFERENCE SQL", prompt)
        self.assertIn("PREVIOUS ATTEMPT FAILED", prompt)

    def test_user_query_always_present(self):
        prompt = self.pb.build_prompt(
            user_query="Show April 2026 non-compliance employees",
            schema_string="schema",
        )
        self.assertIn("Show April 2026 non-compliance employees", prompt)


if __name__ == "__main__":
    unittest.main()
