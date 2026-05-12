import logging
from typing import Any, Dict

from app.graph.workflow import run_report_agent

logger = logging.getLogger(__name__)


class ReportService:
    async def generate(
        self,
        user_id: str,
        query: str,
        debug: bool = False,
        session_id: str = "",
    ) -> Dict[str, Any]:
        logger.info(f"[report_service] user={user_id} query={query[:80]!r}")
        return await run_report_agent(
            user_id=user_id,
            query=query,
            debug=debug,
            session_id=session_id,
        )


report_service = ReportService()
