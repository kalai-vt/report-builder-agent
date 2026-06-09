"""
Few-shot SQL example retrieval using FAISS + OpenAI embeddings.

The index is built once at startup and cached in memory.
Each request retrieves the top-K most similar approved examples
which are then injected into the prompt before LLM SQL generation.
"""

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any, Dict, List, Optional

import numpy as np

logger = logging.getLogger(__name__)

_EXAMPLES_PATH = Path(__file__).parent.parent / "data" / "few_shot_sql_examples.json"


class ExampleRetriever:
    """
    Singleton service — loads examples once, builds FAISS index once,
    then retrieves top-K similar examples per user query.
    """

    def __init__(self) -> None:
        self._examples: List[Dict[str, Any]] = []
        self._index = None          # faiss.Index
        self._text_store: List[str] = []   # parallel list of embedding source texts
        self._initialized = False
        self._lock = threading.Lock()

    # ── Initialization ─────────────────────────────────────────────────────────

    def _load_examples(self) -> List[Dict[str, Any]]:
        with open(_EXAMPLES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        examples = data.get("examples", [])
        logger.info(f"[example_retriever] loaded {len(examples)} examples from {_EXAMPLES_PATH.name}")
        return examples

    def _build_embedding_text(self, ex: Dict[str, Any]) -> str:
        """Combine prompt + business_meaning + intent for richer embedding similarity."""
        parts = [
            ex.get("prompt", ""),
            ex.get("business_meaning", ""),
            ex.get("intent", ""),
        ]
        return " | ".join(p for p in parts if p)

    def _get_embeddings(self, texts: List[str]) -> np.ndarray:
        """Batch-embed texts using OpenAI text-embedding-3-small."""
        import openai
        from app.config import settings

        client = openai.OpenAI(api_key=settings.OPENAI_API_KEY)
        response = client.embeddings.create(
            model="text-embedding-3-small",
            input=texts,
        )
        vectors = np.array(
            [item.embedding for item in response.data],
            dtype=np.float32,
        )
        return vectors

    def _normalize(self, vectors: np.ndarray) -> np.ndarray:
        """L2-normalize so inner product == cosine similarity."""
        import faiss
        faiss.normalize_L2(vectors)
        return vectors

    def _build_index(self, vectors: np.ndarray):
        """Build a flat inner-product FAISS index from normalized vectors."""
        import faiss
        dim = vectors.shape[1]
        index = faiss.IndexFlatIP(dim)
        index.add(vectors)
        logger.info(f"[example_retriever] FAISS index built: dim={dim} n={index.ntotal}")
        return index

    def initialize(self) -> None:
        """
        Build the in-memory FAISS index.  Called once at application startup.
        Thread-safe; subsequent calls are no-ops.
        """
        with self._lock:
            if self._initialized:
                return
            try:
                self._examples = self._load_examples()
                if not self._examples:
                    logger.warning("[example_retriever] No examples found — retrieval disabled")
                    self._initialized = True
                    return

                texts = [self._build_embedding_text(ex) for ex in self._examples]
                self._text_store = texts

                logger.info("[example_retriever] generating embeddings for %d examples…", len(texts))
                vectors = self._get_embeddings(texts)
                vectors = self._normalize(vectors)
                self._index = self._build_index(vectors)
                self._initialized = True
                logger.info("[example_retriever] ready")
            except Exception as exc:
                logger.error("[example_retriever] initialization failed: %s", exc, exc_info=True)
                # Leave _initialized = False so callers can detect failure
                self._initialized = True   # avoid retry loops; retrieval will return []

    # ── Retrieval ──────────────────────────────────────────────────────────────

    def retrieve(self, query: str, top_k: int = 3) -> List[Dict[str, Any]]:
        """
        Return up to top_k approved examples most similar to query.
        Falls back to empty list on any error so the pipeline is never blocked.
        """
        if not self._initialized or self._index is None or not self._examples:
            return []

        try:
            vec = self._get_embeddings([query])         # shape (1, dim)
            vec = self._normalize(vec)
            k = min(top_k, len(self._examples))
            scores, indices = self._index.search(vec, k)

            results = []
            for score, idx in zip(scores[0], indices[0]):
                if idx < 0:
                    continue
                ex = self._examples[idx]
                results.append({
                    "intent":           ex.get("intent", ""),
                    "prompt":           ex.get("prompt", ""),
                    "sql_pattern":      ex.get("sql_pattern", ""),
                    "business_meaning": ex.get("business_meaning", ""),
                    "tables":           ex.get("tables", []),
                    "joins":            ex.get("joins", []),
                    "filters":          ex.get("filters", {}),
                    "similarity_score": round(float(score), 4),
                })

            logger.info(
                "[example_retriever] query=%r retrieved=%d intents=%s scores=%s",
                query[:60],
                len(results),
                [r["intent"] for r in results],
                [r["similarity_score"] for r in results],
            )
            return results

        except Exception as exc:
            logger.warning("[example_retriever] retrieval failed: %s", exc)
            return []


# Singleton — initialized once at startup via lifespan or first use
example_retriever = ExampleRetriever()
