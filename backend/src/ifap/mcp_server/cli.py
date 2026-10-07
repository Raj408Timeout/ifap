"""Run IFAP as an MCP server.

    python -m ifap.mcp_server                      # stdio (Claude Desktop, Claude Code)
    python -m ifap.mcp_server --transport http     # streamable HTTP on 127.0.0.1:8100/mcp

Defaults chosen for running next to the REST API on one laptop (each overridable with the
usual IFAP_ environment variables):

* knowledge base in memory, re-seeded from the template JSON at start (~1 s), so this
  process never shares Chroma's on-disk index with the API process;
* logs on stderr - stdout carries the stdio protocol.
"""

from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Sequence

from ifap.api.container import build_container
from ifap.config.settings import Settings
from ifap.mcp_server.server import build_mcp_server
from ifap.observability.logging import configure_logging, get_logger
from ifap.observability.telemetry import configure_tracing

PROCESS_DEFAULTS = {
    "IFAP_KNOWLEDGE__PROVIDER": "in_memory",
    "IFAP_OBSERVABILITY__LOG_STREAM": "stderr",
    "IFAP_OBSERVABILITY__SERVICE_NAME": "ifap-mcp",
}

_log = get_logger(__name__)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(prog="ifap-mcp", description="IFAP MCP server")
    parser.add_argument("--transport", choices=["stdio", "http"], default="stdio")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8100)
    return parser.parse_args(argv)


async def serve(settings: Settings, *, transport: str, host: str, port: int) -> None:
    configure_logging(settings.observability)
    configure_tracing(settings.observability)
    container = await build_container(settings)
    seeded = await container.ingestion.ensure_seeded(container.template_source)
    _log.info("mcp.starting", transport=transport, seeded=seeded, llm=container.llm.status())
    server = build_mcp_server(container)
    try:
        if transport == "stdio":
            await server.run_stdio_async()
        else:
            await server.run_streamable_http_async(host=host, port=port)
    finally:
        await container.engine.dispose()


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    for key, value in PROCESS_DEFAULTS.items():
        os.environ.setdefault(key, value)
    asyncio.run(serve(Settings(), transport=args.transport, host=args.host, port=args.port))
