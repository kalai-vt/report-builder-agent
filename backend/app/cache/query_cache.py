import hashlib
import logging
import time
from collections import OrderedDict
from typing import Any, Optional

logger = logging.getLogger(__name__)


class QueryCache:
    """
    In-memory LRU cache with per-entry TTL.

    Eviction policy: when max_size is exceeded the least-recently-used
    entry is dropped.  Expired entries are evicted lazily on access.
    """

    def __init__(self, ttl_seconds: int = 3600, max_size: int = 500):
        self._store: OrderedDict[str, tuple[Any, float]] = OrderedDict()
        self._ttl = ttl_seconds
        self._max_size = max_size
        self._hits = 0
        self._misses = 0

    # ── Public API ────────────────────────────────────────────────────────────

    def make_key(self, user_id: str, query: str) -> str:
        """Stable hash key: collapse whitespace + lowercase before hashing."""
        normalized = " ".join(query.lower().split())
        return hashlib.md5(f"{user_id}:{normalized}".encode()).hexdigest()

    def get(self, key: str) -> Optional[Any]:
        if key not in self._store:
            self._misses += 1
            return None
        value, expires_at = self._store[key]
        if time.time() > expires_at:
            del self._store[key]
            self._misses += 1
            logger.debug(f"[cache] EXPIRED key={key[:8]}")
            return None
        self._store.move_to_end(key)
        self._hits += 1
        logger.info(f"[cache] HIT key={key[:8]}")
        return value

    def set(self, key: str, value: Any) -> None:
        if key in self._store:
            self._store.move_to_end(key)
        self._store[key] = (value, time.time() + self._ttl)
        if len(self._store) > self._max_size:
            self._store.popitem(last=False)
            logger.debug("[cache] EVICTED oldest entry (max_size reached)")
        logger.info(f"[cache] SET key={key[:8]} ttl={self._ttl}s")

    def invalidate(self, key: str) -> bool:
        if key in self._store:
            del self._store[key]
            logger.info(f"[cache] INVALIDATED key={key[:8]}")
            return True
        return False

    def clear(self) -> int:
        count = len(self._store)
        self._store.clear()
        logger.info(f"[cache] CLEARED {count} entries")
        return count

    # ── Stats ─────────────────────────────────────────────────────────────────

    @property
    def size(self) -> int:
        return len(self._store)

    @property
    def hits(self) -> int:
        return self._hits

    @property
    def misses(self) -> int:
        return self._misses

    @property
    def hit_rate(self) -> float:
        total = self._hits + self._misses
        return round(self._hits / total, 4) if total else 0.0

    def stats(self) -> dict:
        return {
            "size": self.size,
            "max_size": self._max_size,
            "ttl_seconds": self._ttl,
            "hits": self._hits,
            "misses": self._misses,
            "hit_rate": self.hit_rate,
        }
