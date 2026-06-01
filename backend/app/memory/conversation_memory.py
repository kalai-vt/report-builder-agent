import logging
from collections import deque
from datetime import datetime
from typing import Dict, Deque, List, Optional

from sqlalchemy import text

from app.db.connection import db_manager

logger = logging.getLogger(__name__)

_MAX_FALLBACK_PER_USER = 100   # cap in-memory entries per user to prevent unbounded growth


class ConversationMemoryManager:
    def __init__(self, max_history: int = 10):
        self.max_history = max_history
        self._fallback: Dict[str, Deque] = {}
        self._db_available: bool = True
        self._ensure_table()

    def _ensure_table(self):
        try:
            with db_manager.engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS conversation_history (
                        id         INT AUTO_INCREMENT PRIMARY KEY,
                        user_id    VARCHAR(255) NOT NULL,
                        role       VARCHAR(50)  NOT NULL,
                        content    TEXT         NOT NULL,
                        sql_query  TEXT,
                        created_at DATETIME     DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_user_created (user_id, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """))
                conn.commit()
        except Exception as e:
            logger.warning(f"conversation_history table unavailable, using in-memory fallback: {e}")
            self._db_available = False

    def add_interaction(
        self,
        user_id: str,
        user_message: str,
        assistant_message: str,
        sql_query: Optional[str] = None,
    ):
        if self._db_available:
            try:
                with db_manager.engine.connect() as conn:
                    conn.execute(text(
                        "INSERT INTO conversation_history (user_id, role, content) "
                        "VALUES (:uid, 'user', :content)"
                    ), {"uid": user_id, "content": user_message})
                    conn.execute(text(
                        "INSERT INTO conversation_history (user_id, role, content, sql_query) "
                        "VALUES (:uid, 'assistant', :content, :sql)"
                    ), {"uid": user_id, "content": assistant_message, "sql": sql_query})
                    conn.commit()
                return
            except Exception as e:
                logger.warning(f"DB memory write failed, using in-memory fallback: {e}")

        # Bounded in-memory fallback
        buf = self._fallback.setdefault(user_id, deque(maxlen=_MAX_FALLBACK_PER_USER))
        buf.append({"role": "user",      "content": user_message,    "sql_query": None,      "timestamp": datetime.now().isoformat()})
        buf.append({"role": "assistant", "content": assistant_message,"sql_query": sql_query, "timestamp": datetime.now().isoformat()})

    def get_history(self, user_id: str, limit: Optional[int] = None) -> List[Dict]:
        n = (limit or self.max_history) * 2
        if self._db_available:
            try:
                with db_manager.engine.connect() as conn:
                    rows = conn.execute(text(
                        "SELECT role, content, sql_query, created_at "
                        "FROM conversation_history "
                        "WHERE user_id = :uid "
                        "ORDER BY created_at DESC LIMIT :lim"
                    ), {"uid": user_id, "lim": n}).fetchall()
                return [
                    {"role": r[0], "content": r[1], "sql_query": r[2], "timestamp": str(r[3])}
                    for r in reversed(rows)
                ]
            except Exception as e:
                logger.warning(f"DB memory read failed: {e}")

        buf = self._fallback.get(user_id, deque())
        items = list(buf)
        return items[-n:]

    def get_context_string(self, user_id: str) -> str:
        history = self.get_history(user_id)
        if not history:
            return ""
        lines = ["=== Conversation History ==="]
        for msg in history:
            lines.append(f"{msg['role'].capitalize()}: {msg['content']}")
            if msg["role"] == "assistant" and msg.get("sql_query"):
                lines.append(f"[SQL used: {msg['sql_query']}]")
        lines.append("=== End History ===")
        return "\n".join(lines)

    def clear_history(self, user_id: str):
        if self._db_available:
            try:
                with db_manager.engine.connect() as conn:
                    conn.execute(text(
                        "DELETE FROM conversation_history WHERE user_id = :uid"
                    ), {"uid": user_id})
                    conn.commit()
            except Exception as e:
                logger.warning(f"DB memory clear failed: {e}")
        self._fallback.pop(user_id, None)


memory_manager = ConversationMemoryManager()
