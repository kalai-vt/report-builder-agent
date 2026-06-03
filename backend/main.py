import logging
from contextlib import asynccontextmanager

from dotenv import load_dotenv
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

load_dotenv()

from app.config import settings

logging.basicConfig(
    level=getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO),
    format="%(asctime)s  %(levelname)-8s  %(name)s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(application):
    logger.info("KRA Report Builder Agent starting up...")
    from pathlib import Path

    from app.db.connection import db_manager
    from app.db.schema_manager import schema_manager
    from app.db.schema_registry import schema_registry

    # ── 1. Load rich schema-context JSON (primary source for LLM grounding) ──
    json_path = Path(__file__).parent / "data" / "kra_schema_llm_context.json"
    if schema_registry.load_from_llm_context_json(str(json_path)):
        logger.info(
            "Schema registry loaded from kra_schema_llm_context.json (%d tables)",
            len(schema_registry.get_valid_tables()),
        )
    else:
        logger.warning(
            "kra_schema_llm_context.json not found — registry will fall back to live DB"
        )

    if db_manager.health_check():
        logger.info("Database connection OK")
        try:
            schema_manager.refresh_schema()
            # refresh_schema() syncs the registry from DB only when the JSON
            # was not already loaded (see schema_manager.py).
            logger.info("Schema loaded on startup")
        except Exception as e:
            logger.warning(f"Schema load on startup failed: {e}")
    else:
        logger.warning("Database unreachable — check DATABASE_URL")

    if not settings.OPENAI_API_KEY:
        logger.warning("OPENAI_API_KEY not set — LLM calls will fail")

    if settings.REDIS_URL:
        from app.cache.redis_cache import sql_cache
        if sql_cache.health_check():
            logger.info("Redis connection OK")
        else:
            logger.warning("Redis unreachable — report refresh/streaming will be unavailable")
    else:
        logger.warning("REDIS_URL not set — report refresh/streaming will be unavailable")

    logger.info(f"Startup complete. Swagger UI: http://localhost:{settings.APP_PORT}/docs")
    yield


from app.api.routes import router

app = FastAPI(
    lifespan=lifespan,
    title="KRA AI Report Agent",
    description=(
        "Type a plain-English question — the agent generates the SQL, "
        "runs it against the `vthink_kra` database, and returns results "
        "with **recommended filters** for frontend drill-down.\n\n"
        "**Pipeline:** NL Query → GPT-4o-mini → SQL Validator → MySQL → Result + Filters"
    ),
    version="1.0.0",
    docs_url="/docs",
    redoc_url=None,
    openapi_url="/openapi.json",
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router, prefix="/api/v1")


@app.get("/", include_in_schema=False)
async def root():
    return JSONResponse({"docs": "/docs", "endpoint": "POST /api/v1/query"})


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "main:app",
        host="0.0.0.0",
        port=settings.APP_PORT,
        reload=True,
        log_level=settings.LOG_LEVEL.lower(),
    )
