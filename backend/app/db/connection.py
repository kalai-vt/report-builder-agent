import logging
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

    def execute_query(self, sql: str) -> Tuple[List[dict], List[str]]:
        timeout_ms = settings.MAX_QUERY_TIMEOUT * 1000
        with self.engine.connect() as conn:
            try:
                conn.execute(text(f"SET SESSION MAX_EXECUTION_TIME={timeout_ms}"))
            except Exception as e:
                logger.warning(f"Could not set query timeout: {e}")
            result  = conn.execute(text(sql))
            columns = list(result.keys())
            rows    = [dict(zip(columns, row)) for row in result.fetchall()]
            return rows, columns

    def health_check(self) -> bool:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            return True
        except Exception:
            return False


db_manager = DatabaseManager()
