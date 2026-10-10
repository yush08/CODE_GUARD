"""FastAPI application factory and cross-cutting error handling."""

from __future__ import annotations

import logging
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app import __version__
from app.api.v1.router import api_router
from app.core.config import get_settings
from app.core.exceptions import CodeGuardError
from app.core.logging import setup_logging

logger = logging.getLogger(__name__)

_HTTP_CODE_TO_ERROR = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "TOO_MANY_REQUESTS",
    500: "INTERNAL_ERROR",
    503: "SERVICE_UNAVAILABLE",
}


def _envelope(code: str, message: str, details: dict | None, request_id: str | None) -> dict:
    return {
        "error": {"code": code, "message": message, "details": details or {}},
        "request_id": request_id,
    }


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    setup_logging(settings.log_level)
    logger.info(
        "CodeGuard %s starting (env=%s, auth_mode=%s, db=%s)",
        __version__,
        settings.app_env,
        settings.auth_mode,
        settings.database_config_state().value,
    )
    from app.services.execution_queue import execution_queue

    execution_queue.start()
    try:
        yield
    finally:
        execution_queue.shutdown()
        logger.info("CodeGuard shutdown complete.")


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(
        title="CodeGuard API",
        description=(
            "Behavior-aware secure online judge backend. Executes C/C++ "
            "submissions in a controlled Windows environment, measures resource "
            "usage, records observable runtime behavior, and produces security "
            "reports. Built for an Operating Systems academic project."
        ),
        version=__version__,
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-ID") or uuid.uuid4().hex[:16]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-ID"] = request_id
        return response

    @app.exception_handler(CodeGuardError)
    async def handle_app_error(request: Request, exc: CodeGuardError):
        request_id = getattr(request.state, "request_id", None)
        return JSONResponse(
            status_code=exc.http_status,
            content=_envelope(exc.code, exc.message, exc.details, request_id),
        )

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(request: Request, exc: RequestValidationError):
        request_id = getattr(request.state, "request_id", None)
        issues = [
            {
                "location": ".".join(str(p) for p in err.get("loc", ())),
                "message": err.get("msg", "invalid"),
                "type": err.get("type", ""),
            }
            for err in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=_envelope(
                "VALIDATION_ERROR",
                "The request did not pass validation.",
                {"issues": issues},
                request_id,
            ),
        )

    async def handle_http_error(request: Request, exc: HTTPException):
        request_id = getattr(request.state, "request_id", None)
        code = _HTTP_CODE_TO_ERROR.get(exc.status_code, "HTTP_ERROR")
        detail = exc.detail if isinstance(exc.detail, str) else "Request failed."
        return JSONResponse(
            status_code=exc.status_code,
            content=_envelope(code, detail, None, request_id),
            headers=getattr(exc, "headers", None),
        )

    # FastAPI's HTTPException (raised by the app) and Starlette's (raised for
    # unmatched routes/methods) must both land in the same error envelope.
    app.add_exception_handler(HTTPException, handle_http_error)
    from starlette.exceptions import HTTPException as StarletteHTTPException

    app.add_exception_handler(StarletteHTTPException, handle_http_error)

    @app.exception_handler(Exception)
    async def handle_unexpected(request: Request, exc: Exception):
        request_id = getattr(request.state, "request_id", None)
        logger.exception("Unhandled error [%s] %s %s", request_id, request.method, request.url.path)
        return JSONResponse(
            status_code=500,
            content=_envelope(
                "INTERNAL_ERROR",
                "An unexpected internal error occurred. See server logs for details.",
                None,
                request_id,
            ),
        )

    app.include_router(api_router, prefix="/api/v1")
    return app


app = create_app()
