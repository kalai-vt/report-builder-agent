import time
import uuid
from typing import Any, Dict, Optional


class SessionStore:
    """In-memory session store for clarification state across /generate and /clarify calls."""

    def __init__(self, ttl: int = 1800) -> None:
        self._store: Dict[str, Dict[str, Any]] = {}
        self._ttl = ttl

    def create(self, data: Dict[str, Any]) -> str:
        session_id = f"sess_{uuid.uuid4().hex[:16]}"
        self._store[session_id] = {"data": data, "created": time.time()}
        return session_id

    def get(self, session_id: str) -> Optional[Dict[str, Any]]:
        entry = self._store.get(session_id)
        if not entry:
            return None
        if time.time() - entry["created"] > self._ttl:
            del self._store[session_id]
            return None
        return entry["data"]

    def update(self, session_id: str, updates: Dict[str, Any]) -> bool:
        if session_id not in self._store:
            return False
        self._store[session_id]["data"].update(updates)
        return True

    def delete(self, session_id: str) -> None:
        self._store.pop(session_id, None)


session_store = SessionStore()
