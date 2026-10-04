"""
backend/app/main.py
FastAPI application entry point.
Registers routers, creates DB tables on startup, configures structured logging.
"""
from __future__ import annotations

import logging
import sys

from contextlib import asynccontextmanager
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.config.settings import get_settings
from app.db.engine import Base, engine
from app.models import orm  # noqa: F401 — ensure models are imported before create_all
from app.api import calls as calls_router
from app.api import counsellors as counsellors_router
from app.api import rubric as rubric_router
from app.api import settings as settings_router
from app.models.schemas import ErrorOut, HealthOut


# ── Structured logging ────────────────────────────────────────────────────────
def _configure_logging():
    settings = get_settings()
    logging.basicConfig(
        stream=sys.stdout,
        level=getattr(logging, settings.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )


_configure_logging()
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    from app.db.engine import ensure_schema_migrations
    ensure_schema_migrations(engine)
    logger.info("Database tables and migrations created/verified")
    settings = get_settings()
    logger.info(
        "Startup config | Model: %s | Price version: %s ($%.2f/M in, $%.2f/M out)",
        settings.claude_model,
        settings.llm_price_version,
        settings.llm_cost_per_million_input_usd,
        settings.llm_cost_per_million_output_usd,
    )
    yield


# ── Application ───────────────────────────────────────────────────────────────
app = FastAPI(
    title="PW Counselling QA API",
    version="1.0.0",
    lifespan=lifespan,
    description=(
        "Internal tool for scoring counselling calls against a rubric. "
        "Uses a synthetic demo policy — not PW's actual policy."
    ),
)

# Configurable CORS origins
settings = get_settings()
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins_list,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Demo Access Token Protection for Mutating Endpoints ──────────────────────
@app.middleware("http")
async def demo_access_token_middleware(request: Request, call_next):
    current_settings = get_settings()
    if current_settings.demo_access_token and request.method in ("POST", "PUT", "PATCH", "DELETE"):
        auth_header = request.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        if not token:
            token = request.headers.get("X-Demo-Access-Token", "").strip()

        if token != current_settings.demo_access_token:
            return JSONResponse(
                status_code=401,
                content={"error": "unauthorized", "detail": "Invalid or missing DEMO_ACCESS_TOKEN"},
            )
    return await call_next(request)


# ── Routers ───────────────────────────────────────────────────────────────────
app.include_router(calls_router.router)
app.include_router(counsellors_router.router)
app.include_router(rubric_router.router)
app.include_router(settings_router.router)


# ── Health check ──────────────────────────────────────────────────────────────
@app.get("/health", response_model=HealthOut, tags=["meta"])
def health(request: Request):
    from sqlalchemy import text
    try:
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        db_status = "ok"
    except Exception as exc:
        db_status = f"error: {exc}"
    return HealthOut(status="ok", db=db_status)


# ── Global exception handler ──────────────────────────────────────────────────
@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    logger.exception("Unhandled exception on %s %s", request.method, request.url.path)
    return JSONResponse(
        status_code=500,
        content=ErrorOut(error="internal_server_error", detail=str(exc)).model_dump(),
    )
