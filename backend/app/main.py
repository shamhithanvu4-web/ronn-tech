"""FastAPI application entry point: `uvicorn app.main:app` (run from backend/)."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.config import PRODUCT_NAME, SEED_CSV, get_settings
from app.db import init_db, session_scope
from app.errors import AppError

log = logging.getLogger("ronntech")


def _load_seed_if_empty() -> None:
    from app.labels.store import label_counts, load_seed_csv

    with session_scope() as db:
        if not label_counts(db) and SEED_CSV.exists():
            counts = load_seed_csv(db, SEED_CSV)
            log.info("Loaded label seed: %s", counts)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    settings.data_mode  # validate early
    init_db()
    _load_seed_if_empty()
    background = None
    if settings.ENABLE_BACKGROUND_TASKS:
        from app.background import start_background, stop_background

        background = await start_background()
    try:
        yield
    finally:
        if background is not None:
            await stop_background(background)


app = FastAPI(title=f"{PRODUCT_NAME} API", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in get_settings().FRONTEND_ORIGIN.split(",") if o.strip()],
    allow_credentials=False,
    allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Signature"],
)


@app.exception_handler(AppError)
async def app_error_handler(_request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(status_code=exc.status_code, content={"detail": exc.detail, "code": exc.code})


@app.exception_handler(StarletteHTTPException)
async def http_error_handler(_request: Request, exc: StarletteHTTPException) -> JSONResponse:
    codes = {401: "unauthorized", 403: "forbidden", 404: "not_found", 405: "method_not_allowed"}
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": str(exc.detail), "code": codes.get(exc.status_code, "http_error")},
    )


@app.exception_handler(RequestValidationError)
async def validation_error_handler(_request: Request, exc: RequestValidationError) -> JSONResponse:
    first = exc.errors()[0] if exc.errors() else {}
    loc = ".".join(str(p) for p in first.get("loc", []) if p != "body")
    return JSONResponse(
        status_code=422,
        content={"detail": f"{loc}: {first.get('msg', 'invalid request')}".strip(": "), "code": "validation_error"},
    )


@app.get("/")
def root() -> dict:
    return {"product": PRODUCT_NAME, "api": "/api", "docs": "/docs"}


def _include_routers() -> None:
    from app.api import auth, system

    app.include_router(auth.router, prefix="/api")
    app.include_router(system.router, prefix="/api")
    for name in ("analyses", "reports", "cases", "requests", "entities", "watchlist", "dashboard", "integrations"):
        try:
            module = __import__(f"app.api.{name}", fromlist=["router"])
        except ModuleNotFoundError as exc:
            if exc.name == f"app.api.{name}":
                continue
            raise
        app.include_router(module.router, prefix="/api")


_include_routers()
