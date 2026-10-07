"""Structured logging (JSON in production, pretty console locally)."""

from __future__ import annotations

import logging
import sys

import structlog

from ifap.config.settings import ObservabilitySettings


def configure_logging(settings: ObservabilitySettings) -> None:
    level = logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
    renderer: structlog.typing.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Default factory resolves sys.stdout per logger (safe when stdout is swapped);
        # stderr is pinned for stdio MCP servers, whose stdout carries the protocol.
        logger_factory=structlog.PrintLoggerFactory(
            sys.stderr if settings.log_stream == "stderr" else None
        ),
    )


def get_logger(name: str) -> structlog.typing.FilteringBoundLogger:
    logger: structlog.typing.FilteringBoundLogger = structlog.get_logger(name)
    return logger
