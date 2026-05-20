import logging
import math
from contextlib import contextmanager
from typing import List, Tuple

from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import QueuePool

from app.config import settings

logger = logging.getLogger(__name__)


class DatabaseManager:
    def __init__(self):
        self.engine = create_engine(
            settings.DATABASE_URL,
            poolclass=QueuePool,
            pool_size=settings.DB_POOL_SIZE,
            max_overflow=settings.DB_MAX_OVERFLOW,
            pool_timeout=30,
            pool_recycle=3600,
            pool_pre_ping=True,
            echo=settings.DB_ECHO,
        )
        self.SessionLocal = sessionmaker(
            autocommit=False, autoflush=False, bind=self.engine
        )
        logger.info("Database engine initialised")

    @contextmanager
    def get_session(self):
        session = self.SessionLocal()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def _set_timeout(self, conn) -> None:
        try:
            conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={settings.MAX_QUERY_TIMEOUT * 1000}"))
        except Exception as e:
            logger.warning(f"Could not set query timeout: {e}")

    def test_execute(self, sql: str, params: dict | None = None) -> Tuple[bool, str]:
        """LIMIT 0 dry-run — validates SQL syntax and schema without fetching rows.

        Returns (success, error_message).  Called before every full execution
        so bad SQL is caught and self-corrected before real rows are read.
        """
        test_sql = f"SELECT * FROM ({sql}) AS _test_run LIMIT 0"
        try:
            with self.engine.connect() as conn:
                self._set_timeout(conn)
                conn.execute(text(test_sql), params or {})
            logger.debug("[test_execute] PASSED")
            return True, ""
        except Exception as exc:
            logger.warning(f"[test_execute] FAILED: {exc}")
            return False, str(exc)

    def execute_query(
        self,
        sql: str,
        params: dict | None = None,
    ) -> Tuple[List[dict], List[str]]:
        bound = params or {}
        with self.engine.connect() as conn:
            self._set_timeout(conn)
            result  = conn.execute(text(sql), bound)
            columns = list(result.keys())
            rows    = [dict(zip(columns, row)) for row in result.fetchall()]
            return rows, columns

    def execute_paginated(
        self,
        sql: str,
        page: int = 1,
        page_size: int = 1000,
        params: dict | None = None,
    ) -> Tuple[List[dict], List[str], int, int]:
        """Execute a SELECT query with offset-based pagination.

        Returns (rows, columns, total_rows, total_pages).
        Bound parameters (e.g. {"employee_id": "E001"}) are passed through
        to both the COUNT subquery and the paginated data query so named
        placeholders like :employee_id are resolved safely by the DB driver.
        """
        page      = max(1, page)
        page_size = min(max(1, page_size), settings.MAX_PAGE_SIZE)
        offset    = (page - 1) * page_size
        bound     = params or {}

        with self.engine.connect() as conn:
            self._set_timeout(conn)

            # Total-row count via wrapping subquery (params forwarded so
            # named placeholders inside the base SQL resolve correctly)
            count_sql  = f"SELECT COUNT(*) FROM ({sql}) AS _pag_count"
            total_rows = conn.execute(text(count_sql), bound).scalar() or 0

            # Paginated data
            paged_sql  = f"{sql} LIMIT {page_size} OFFSET {offset}"
            result     = conn.execute(text(paged_sql), bound)
            columns    = list(result.keys())
            rows       = [dict(zip(columns, row)) for row in result.fetchall()]

        total_pages = max(1, math.ceil(total_rows / page_size)) if total_rows else 1
        logger.debug(
            f"[execute_paginated] page={page}/{total_pages} "
            f"rows={len(rows)}/{total_rows} page_size={page_size}"
        )
        return rows, columns, total_rows, total_pages

    def health_check(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


db_manager = DatabaseManager()
