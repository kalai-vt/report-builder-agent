import logging
from collections import deque
from datetime import datetime
from typing import Deque, Dict, List, Optional

from sqlalchemy import text

from app.db.connection import db_manager

logger = logging.getLogger(__name__)

_MAX_FALLBACK_PER_KEY = 100   # cap in-memory entries per (user, session) to prevent unbounded growth


class ConversationMemoryManager:
    def __init__(self, max_history: int = 10):
        self.max_history = max_history
        self._fallback: Dict[str, Deque] = {}
        self._db_available: bool = True
        self._ensure_table()

    # ── Schema bootstrap ──────────────────────────────────────────────────────

    def _ensure_table(self) -> None:
        try:
            with db_manager.engine.connect() as conn:
                conn.execute(text("""
                    CREATE TABLE IF NOT EXISTS conversation_history (
                        id              INT AUTO_INCREMENT PRIMARY KEY,
                        user_id         VARCHAR(255) NOT NULL,
                        chat_session_id VARCHAR(255) NOT NULL DEFAULT '',
                        role            VARCHAR(50)  NOT NULL,
                        content         TEXT         NOT NULL,
                        sql_query       TEXT,
                        created_at      DATETIME     DEFAULT CURRENT_TIMESTAMP,
                        INDEX idx_user_session_created (user_id, chat_session_id, created_at)
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
                """))
                conn.commit()
                self._add_session_column_if_missing(conn)
        except Exception as exc:
            logger.warning("conversation_history table unavailable, using in-memory fallback: %s", exc)
            self._db_available = False

    def _add_session_column_if_missing(self, conn) -> None:
        """Add chat_session_id column to existing tables created before this migration."""
        try:
            result = conn.execute(text(
                "SELECT COUNT(*) FROM INFORMATION_SCHEMA.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() "
                "  AND TABLE_NAME = 'conversation_history' "
                "  AND COLUMN_NAME = 'chat_session_id'"
            )).fetchone()
            if result and result[0] == 0:
                conn.execute(text(
                    "ALTER TABLE conversation_history "
                    "ADD COLUMN chat_session_id VARCHAR(255) NOT NULL DEFAULT '' AFTER user_id"
                ))
                # Suppress duplicate-key error if the index already exists
                try:
                    conn.execute(text(
                        "ALTER TABLE conversation_history "
                        "ADD INDEX idx_user_session_created (user_id, chat_session_id, created_at)"
                    ))
                except Exception:
                    pass
                conn.commit()
                logger.info("[memory] added chat_session_id column to conversation_history")
        except Exception as exc:
            logger.warning("[memory] could not migrate chat_session_id column: %s", exc)

    # ── Key helpers ───────────────────────────────────────────────────────────

    @staticmethod
    def _fb_key(user_id: str, chat_session_id: str) -> str:
        """In-memory fallback dictionary key."""
        return f"{user_id}:{chat_session_id}" if chat_session_id else user_id

    # ── Write ─────────────────────────────────────────────────────────────────

    def add_interaction(
        self,
        user_id: str,
        user_message: str,
        assistant_message: str,
        sql_query: Optional[str] = None,
        chat_session_id: str = "",
    ) -> None:
        if self._db_available:
            try:
                with db_manager.engine.connect() as conn:
                    conn.execute(text(
                        "INSERT INTO conversation_history "
                        "  (user_id, chat_session_id, role, content) "
                        "VALUES (:uid, :sid, 'user', :content)"
                    ), {"uid": user_id, "sid": chat_session_id, "content": user_message})
                    conn.execute(text(
                        "INSERT INTO conversation_history "
                        "  (user_id, chat_session_id, role, content, sql_query) "
                        "VALUES (:uid, :sid, 'assistant', :content, :sql)"
                    ), {"uid": user_id, "sid": chat_session_id, "content": assistant_message, "sql": sql_query})
                    conn.commit()
                return
            except Exception as exc:
                logger.warning("DB memory write failed, using in-memory fallback: %s", exc)

        # Bounded in-memory fallback
        key = self._fb_key(user_id, chat_session_id)
        buf = self._fallback.setdefault(key, deque(maxlen=_MAX_FALLBACK_PER_KEY))
        buf.append({"role": "user",      "content": user_message,     "sql_query": None,      "timestamp": datetime.now().isoformat()})
        buf.append({"role": "assistant", "content": assistant_message, "sql_query": sql_query, "timestamp": datetime.now().isoformat()})

    # ── Read ──────────────────────────────────────────────────────────────────

    def get_history(
        self,
        user_id: str,
        limit: Optional[int] = None,
        chat_session_id: str = "",
    ) -> List[Dict]:
        n = (limit or self.max_history) * 2
        if self._db_available:
            try:
                with db_manager.engine.connect() as conn:
                    if chat_session_id:
                        rows = conn.execute(text(
                            "SELECT role, content, sql_query, created_at "
                            "FROM conversation_history "
                            "WHERE user_id = :uid AND chat_session_id = :sid "
                            "ORDER BY created_at DESC LIMIT :lim"
                        ), {"uid": user_id, "sid": chat_session_id, "lim": n}).fetchall()
                    else:
                        # Backward-compat: no session filter
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
            except Exception as exc:
                logger.warning("DB memory read failed: %s", exc)

        key = self._fb_key(user_id, chat_session_id)
        buf = self._fallback.get(key, deque())
        items = list(buf)
        return items[-n:]

    def get_context_string(self, user_id: str, chat_session_id: str = "") -> str:
        history = self.get_history(user_id, chat_session_id=chat_session_id)
        if not history:
            return ""
        lines = ["=== Conversation History ==="]
        for msg in history:
            lines.append(f"{msg['role'].capitalize()}: {msg['content']}")
            if msg["role"] == "assistant" and msg.get("sql_query"):
                lines.append(f"[SQL used: {msg['sql_query']}]")
        lines.append("=== End History ===")
        return "\n".join(lines)

    # ── Clear ─────────────────────────────────────────────────────────────────

    def clear_history(self, user_id: str, chat_session_id: str = "") -> None:
        if self._db_available:
            try:
                with db_manager.engine.connect() as conn:
                    if chat_session_id:
                        conn.execute(text(
                            "DELETE FROM conversation_history "
                            "WHERE user_id = :uid AND chat_session_id = :sid"
                        ), {"uid": user_id, "sid": chat_session_id})
                    else:
                        conn.execute(text(
                            "DELETE FROM conversation_history WHERE user_id = :uid"
                        ), {"uid": user_id})
                    conn.commit()
            except Exception as exc:
                logger.warning("DB memory clear failed: %s", exc)
        key = self._fb_key(user_id, chat_session_id)
        self._fallback.pop(key, None)


memory_manager = ConversationMemoryManager()
