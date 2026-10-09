"""FastAPI application factory."""

from __future__ import annotations

import os
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

from ifap.api.container import build_container
from ifap.api.routers import platform, questionnaires
from ifap.api.schemas import ErrorResponse
from ifap.config.settings import Settings, get_settings, unrecognised_env_vars
from ifap.domain.errors import (
    AgentExecutionError,
    DomainRuleViolationError,
    IFAPError,
    NotFoundError,
)
from ifap.observability.logging import configure_logging, get_logger
from ifap.observability.telemetry import configure_tracing

_log = get_logger(__name__)

_STATUS_BY_ERROR: dict[type[IFAPError], int] = {
    NotFoundError: 404,
    DomainRuleViolationError: 422,
    AgentExecutionError: 502,
}


async def _handle_domain_error(_: Request, exc: Exception) -> JSONResponse:
    status = next((code for kind, code in _STATUS_BY_ERROR.items() if isinstance(exc, kind)), 500)
    body = ErrorResponse(error=type(exc).__name__, detail=str(exc))
    return JSONResponse(status_code=status, content=body.model_dump())


def create_app(settings: Settings | None = None) -> FastAPI:
    resolved = settings or get_settings()
    configure_logging(resolved.observability)
    configure_tracing(resolved.observability)
    for name in unrecognised_env_vars(os.environ):
        _log.warning(
            "config.unrecognised_env_var",
            variable=name,
            hint="nested settings need a double underscore, e.g. IFAP_LLM__PROVIDER",
        )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
        container = await build_container(resolved)
        if resolved.knowledge.ingest_on_startup:
            seeded = await container.ingestion.ensure_seeded(container.template_source)
            _log.info("knowledge.seeded", documents=seeded)
        app.state.container = container
        yield
        await container.engine.dispose()

    app = FastAPI(title=resolved.api.title, version=resolved.api.version, lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=resolved.api.cors_origins,
        allow_origin_regex=resolved.api.cors_origin_regex,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    app.add_exception_handler(IFAPError, _handle_domain_error)
    app.include_router(platform.health_router)
    app.include_router(platform.router, prefix=resolved.api.prefix)
    app.include_router(questionnaires.router, prefix=resolved.api.prefix)
    FastAPIInstrumentor.instrument_app(app)
    return app
