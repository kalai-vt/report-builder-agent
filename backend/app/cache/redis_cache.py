import json
import logging
import time
from datetime import datetime, timezone
from typing import Dict, Optional

import redis as redis_lib

from app.config import settings

logger = logging.getLogger(__name__)


class SQLRedisCache:
    """
    Redis-backed store for post-validation, RBAC-secured SQL.

    Key format : sql_cache:{session_id}
    Payload    : {session_id, user_id, secured_sql, created_at}
    TTL        : settings.STREAM_CACHE_TTL_SECONDS

    When Redis is unavailable the cache automatically falls back to an
    in-memory dict so that refresh/stream still works in development
    without a Redis instance.  The fallback is cleared on process restart.
    """

    def __init__(self) -> None:
        self._client: Optional[redis_lib.Redis] = None
        self._redis_ok: bool = True          # flipped to False on first failure
        # in-memory fallback: {session_id: (payload_dict, expires_at)}
        self._fallback: Dict[str, tuple] = {}

    # ── Connection ────────────────────────────────────────────────────────────

    def _get_client(self) -> redis_lib.Redis:
        if self._client is None:
            if not settings.REDIS_URL:
                raise ConnectionError("REDIS_URL is not configured in .env")
            self._client = redis_lib.from_url(
                settings.REDIS_URL,
                decode_responses=True,
                socket_timeout=5,
                socket_connect_timeout=5,
            )
        return self._client

    @staticmethod
    def _key(session_id: str) -> str:
        return f"sql_cache:{session_id}"

    # ── Fallback helpers ──────────────────────────────────────────────────────

    def _fallback_store(self, session_id: str, payload: dict) -> None:
        expires_at = time.time() + settings.STREAM_CACHE_TTL_SECONDS
        self._fallback[session_id] = (payload, expires_at)

    def _fallback_get(self, session_id: str) -> Optional[dict]:
        entry = self._fallback.get(session_id)
        if entry is None:
            return None
        payload, expires_at = entry
        if time.time() > expires_at:
            del self._fallback[session_id]
            return None
        return payload

    def _fallback_delete(self, session_id: str) -> None:
        self._fallback.pop(session_id, None)

    # ── Public API ────────────────────────────────────────────────────────────

    def store(self, session_id: str, user_id: str, secured_sql: str) -> bool:
        """Persist the RBAC-secured SQL for later refresh calls."""
        payload = {
            "session_id": session_id,
            "user_id": user_id,
            "secured_sql": secured_sql,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        if self._redis_ok:
            try:
                self._get_client().setex(
                    self._key(session_id),
                    settings.STREAM_CACHE_TTL_SECONDS,
                    json.dumps(payload),
                )
                logger.info(f"[sql_cache] redis stored session={session_id[:16]} user={user_id}")
                return True
            except Exception as exc:
                logger.warning(f"[sql_cache] redis store failed, using in-memory fallback: {exc}")
                self._redis_ok = False

        # Fallback path
        self._fallback_store(session_id, payload)
        logger.info(f"[sql_cache] memory stored session={session_id[:16]} user={user_id}")
        return True

    def get(self, session_id: str) -> Optional[dict]:
        """Return cached payload or None if missing / expired."""
        if self._redis_ok:
            try:
                raw = self._get_client().get(self._key(session_id))
                if raw is None:
                    return None
                return json.loads(raw)
            except Exception as exc:
                logger.warning(f"[sql_cache] redis get failed, using in-memory fallback: {exc}")
                self._redis_ok = False

        # Fallback path
        return self._fallback_get(session_id)

    def delete(self, session_id: str) -> bool:
        """Remove stale cache entry (called on SCHEMA_CHANGED)."""
        if self._redis_ok:
            try:
                self._get_client().delete(self._key(session_id))
                logger.info(f"[sql_cache] redis deleted session={session_id[:16]}")
                return True
            except Exception as exc:
                logger.warning(f"[sql_cache] redis delete failed: {exc}")
                self._redis_ok = False

        self._fallback_delete(session_id)
        return True

    def health_check(self) -> bool:
        try:
            self._get_client().ping()
            self._redis_ok = True
            return True
        except Exception:
            self._redis_ok = False
            return False


sql_cache = SQLRedisCache()
