import logging
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_CONTEXT_TTL_SECONDS = 7200  # 2 hours

_EMPTY: Dict[str, Any] = {
    "report_type": "",
    "primary_table": "",
    "dimensions": [],
    "metrics": [],
    "filters": {},
    "date_range": "",
    "generated_sql": "",
    "last_query": "",
}


def _empty_context() -> Dict[str, Any]:
    return {
        "report_type": "",
        "primary_table": "",
        "dimensions": [],
        "metrics": [],
        "filters": {},
        "date_range": "",
        "generated_sql": "",
        "last_query": "",
    }


class ActiveReportContextManager:
    """Maintains structured state for the currently active report.

    Keyed by (user_id, chat_session_id).  When chat_session_id is empty
    the key degrades to user_id alone (backward-compat mode).
    """

    def __init__(self) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}
        self._timestamps: Dict[str, float] = {}

    # ── Key helpers ──────────────────────────────────────────────────────────

    def _key(self, user_id: str, chat_session_id: str = "") -> str:
        return f"{user_id}:{chat_session_id}" if chat_session_id else user_id

    # ── Public API ────────────────────────────────────────────────────────────

    def get(self, user_id: str, chat_session_id: str = "") -> Dict[str, Any]:
        """Return a copy of the current active report context."""
        self._evict_expired()
        key = self._key(user_id, chat_session_id)
        return dict(self._store.get(key, _empty_context()))

    def reset(self, user_id: str, chat_session_id: str = "") -> None:
        """Discard the previous report context for a new request."""
        key = self._key(user_id, chat_session_id)
        self._store[key] = _empty_context()
        self._timestamps[key] = time.time()
        logger.debug("[context_manager] reset user=%s session=%s", user_id, chat_session_id[:16] if chat_session_id else "")

    def update(
        self,
        user_id: str,
        chat_session_id: str = "",
        *,
        generated_sql: str = "",
        last_query: str = "",
        report_type: str = "",
        primary_table: str = "",
        filters: Optional[Dict[str, Any]] = None,
        dimensions: Optional[List[str]] = None,
        metrics: Optional[List[str]] = None,
        date_range: str = "",
    ) -> Dict[str, Any]:
        """Merge new data into the active report context and return the updated copy."""
        key = self._key(user_id, chat_session_id)
        ctx = self._store.get(key, _empty_context())

        if generated_sql:
            ctx["generated_sql"] = generated_sql
        if last_query:
            ctx["last_query"] = last_query
        if report_type:
            ctx["report_type"] = report_type
        if primary_table:
            ctx["primary_table"] = primary_table
        if filters:
            ctx["filters"] = {**ctx.get("filters", {}), **filters}
        if dimensions is not None:
            ctx["dimensions"] = dimensions
        if metrics is not None:
            ctx["metrics"] = metrics
        if date_range:
            ctx["date_range"] = date_range

        self._store[key] = ctx
        self._timestamps[key] = time.time()
        logger.debug("[context_manager] updated user=%s session=%s sql_len=%d",
                     user_id, chat_session_id[:16] if chat_session_id else "", len(generated_sql))
        return dict(ctx)

    # ── Housekeeping ──────────────────────────────────────────────────────────

    def _evict_expired(self) -> None:
        now = time.time()
        expired = [k for k, ts in self._timestamps.items() if now - ts > _CONTEXT_TTL_SECONDS]
        for k in expired:
            self._store.pop(k, None)
            self._timestamps.pop(k, None)

    def __len__(self) -> int:
        return len(self._store)


active_report_context_manager = ActiveReportContextManager()
