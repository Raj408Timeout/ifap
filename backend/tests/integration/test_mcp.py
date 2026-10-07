"""MCP server: every tool through a real MCP client, plus the stdio process Claude Desktop runs."""

# pylint: disable=redefined-outer-name  # pytest fixtures are injected by name

from __future__ import annotations

import os
import sys
from collections.abc import AsyncIterator
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest
from mcp import Client, StdioServerParameters
from mcp.server.mcpserver import MCPServer

from ifap.api.container import Container, build_container
from ifap.config.settings import KnowledgeProviderKind, Settings
from ifap.mcp_server import cli
from ifap.mcp_server.server import build_mcp_server

pytestmark = pytest.mark.integration

EXPECTED_TOOLS = {
    "get_platform_status",
    "list_survey_types",
    "search_templates",
    "generate_questionnaire",
    "get_generation_result",
    "build_questionnaire_from_templates",
    "get_questionnaire",
    "list_questionnaires",
    "publish_questionnaire",
    "set_llm_enabled",
}


@pytest.fixture
async def container(app_settings: Settings) -> AsyncIterator[Container]:
    knowledge = app_settings.knowledge.model_copy(
        update={"provider": KnowledgeProviderKind.IN_MEMORY}
    )
    built = await build_container(app_settings.model_copy(update={"knowledge": knowledge}))
    await built.ingestion.ensure_seeded(built.template_source)
    yield built
    await built.engine.dispose()


@pytest.fixture
def server(container: Container) -> MCPServer:
    return build_mcp_server(container)


async def _call(server: MCPServer, tool: str, **arguments: Any) -> Any:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)
    assert not result.is_error, result.content
    structured: dict[str, Any] = result.structured_content or {}
    # list-returning tools are wrapped as {"result": [...]} by the MCP SDK
    return structured["result"] if set(structured) == {"result"} else structured


async def _error(server: MCPServer, tool: str, **arguments: Any) -> str:
    async with Client(server) as client:
        result = await client.call_tool(tool, arguments)
    assert result.is_error
    text = getattr(result.content[0], "text", "")
    assert isinstance(text, str)
    return text


async def test_lists_all_tools_with_descriptions(server: MCPServer) -> None:
    async with Client(server) as client:
        tools = (await client.list_tools()).tools
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert all(tool.description for tool in tools)


async def test_platform_status_and_survey_types(server: MCPServer) -> None:
    status = await _call(server, "get_platform_status")
    assert status["default_workflow"] == "standard"
    assert "autonomous_builder" in status["workflows"]["autonomous"]
    assert status["knowledge_documents"] == 200
    types = await _call(server, "list_survey_types")
    assert {t["survey_type"] for t in types} >= {"customer_satisfaction", "compliance_review"}


async def test_search_templates(server: MCPServer) -> None:
    hits = await _call(server, "search_templates", query="medication allergies", top_k=3)
    assert hits[0]["id"].startswith("hc-")
    filtered = await _call(
        server, "search_templates", query="support", survey_type="customer_satisfaction"
    )
    assert all(hit["id"].startswith("cs-") for hit in filtered)
    message = await _error(server, "search_templates", query="x", top_k=999)
    assert "top_k" in message


@pytest.mark.parametrize("workflow", ["standard", "autonomous"])
async def test_generate_questionnaire_with_each_workflow(server: MCPServer, workflow: str) -> None:
    job = await _call(
        server,
        "generate_questionnaire",
        request="Annual GDPR and vendor risk compliance review",
        question_count=5,
        workflow=workflow,
    )
    assert job["status"] == "succeeded"  # heuristic agents finish well inside the wait window
    assert job["next_action"].startswith("Done")
    assert len(job["completed_steps"]) == 4
    result = job["result"]
    assert result["workflow"] == workflow
    assert result["interpreted_survey_type"] == "compliance_review"
    assert result["questionnaire"]["question_count"] == 5
    assert result["questionnaire"]["is_valid"]


async def test_generate_rejects_bad_input_before_starting_a_job(server: MCPServer) -> None:
    message = await _error(server, "generate_questionnaire", request="a survey", workflow="x")
    assert "Unknown workflow 'x'" in message
    too_short = await _error(server, "generate_questionnaire", request="ab")
    assert "at least 3 characters" in too_short


async def test_slow_generation_returns_job_and_can_be_polled(container: Container) -> None:
    """Simulates a run longer than the host's timeout: the first call returns `running`
    with a job id, and polling eventually returns the finished questionnaire."""
    settings = container.settings.model_copy(
        update={"mcp": container.settings.mcp.model_copy(update={"wait_seconds": 0.001})}
    )
    server = build_mcp_server(replace(container, settings=settings))
    first = await _call(server, "generate_questionnaire", request="customer delivery survey")
    assert first["status"] == "running"
    assert first["result"] is None
    assert first["job_id"] in first["next_action"]

    polled = first
    for _ in range(500):
        polled = await _call(server, "get_generation_result", job_id=first["job_id"])
        if polled["status"] != "running":
            break
    assert polled["status"] == "succeeded"
    assert polled["result"]["questionnaire"]["is_valid"]
    explicit = await _call(server, "get_generation_result", job_id=first["job_id"], wait_seconds=5)
    assert explicit["status"] == "succeeded"


async def test_unknown_job_is_reported(server: MCPServer) -> None:
    message = await _error(server, "get_generation_result", job_id="nope")
    assert "Unknown job 'nope'" in message


async def test_claude_as_builder_round_trip(server: MCPServer) -> None:
    draft = await _call(
        server,
        "build_questionnaire_from_templates",
        title="Allergy check",
        survey_type="healthcare_assessment",
        template_ids=["hc-008", "hc-007"],  # child before parent
    )
    assert not draft["is_valid"]
    assert "depends on" in draft["issues"][0]

    fixed = await _call(
        server,
        "build_questionnaire_from_templates",
        title="Allergy check",
        survey_type="healthcare_assessment",
        template_ids=["hc-007", "hc-008"],
    )
    assert fixed["is_valid"]
    fetched = await _call(server, "get_questionnaire", questionnaire_id=fixed["id"])
    assert [q["id"] for q in fetched["questions"]] == ["hc-007", "hc-008"]
    listed = await _call(server, "list_questionnaires", limit=5)
    assert listed[0]["id"] == fixed["id"]
    published = await _call(server, "publish_questionnaire", questionnaire_id=fixed["id"])
    assert published["status"] == "published"


async def test_builder_errors_reach_the_model(server: MCPServer) -> None:
    unknown = await _error(
        server,
        "build_questionnaire_from_templates",
        title="Bad",
        survey_type="x",
        template_ids=["nope-1"],
    )
    assert "Unknown template ids: nope-1" in unknown
    empty = await _error(
        server, "build_questionnaire_from_templates", title="Bad", survey_type="x", template_ids=[]
    )
    assert "at least one" in empty
    bad_id = await _error(server, "get_questionnaire", questionnaire_id="not-a-uuid")
    assert "not a valid questionnaire id" in bad_id
    missing = await _error(
        server, "publish_questionnaire", questionnaire_id="00000000-0000-0000-0000-000000000000"
    )
    assert "not found" in missing


async def test_llm_switch_tool(server: MCPServer) -> None:
    status = await _call(server, "set_llm_enabled", enabled=False)
    assert status["enabled"] is False
    message = await _error(server, "set_llm_enabled", enabled=True)  # no provider in tests
    assert "No LLM provider configured" in message


# ---------------------------------------------------------------- CLI and real stdio process


async def test_serve_runs_the_selected_transport(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[str] = []

    async def fake_stdio(self: MCPServer) -> None:
        calls.append(f"stdio:{self.name}")

    async def fake_http(self: MCPServer, *, host: str, port: int) -> None:
        del self
        calls.append(f"http:{host}:{port}")

    monkeypatch.setattr(MCPServer, "run_stdio_async", fake_stdio)
    monkeypatch.setattr(MCPServer, "run_streamable_http_async", fake_http)
    await cli.serve(app_settings, transport="stdio", host="h", port=1)
    await cli.serve(app_settings, transport="http", host="0.0.0.0", port=8100)  # noqa: S104
    assert calls == ["stdio:ifap", "http:0.0.0.0:8100"]


def test_main_applies_process_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def fake_serve(settings: Settings, *, transport: str, host: str, port: int) -> None:
        seen.update(
            transport=transport,
            port=port,
            host=host,
            knowledge=settings.knowledge.provider,
            stream=settings.observability.log_stream,
        )

    for key in cli.PROCESS_DEFAULTS:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr(cli, "serve", fake_serve)
    cli.main(["--transport", "http", "--port", "9100"])
    assert seen == {
        "transport": "http",
        "port": 9100,
        "host": "127.0.0.1",
        "knowledge": KnowledgeProviderKind.IN_MEMORY,
        "stream": "stderr",
    }


async def test_stdio_server_process_speaks_clean_mcp(tmp_path: Path) -> None:
    """What Claude Desktop does: launch the process from another directory and talk over
    stdin/stdout. Any stray print to stdout would break this handshake."""
    params = StdioServerParameters(
        command=sys.executable,
        args=["-m", "ifap.mcp_server"],
        cwd="/",
        env={
            "PATH": os.environ.get("PATH", ""),
            "IFAP_LLM__PROVIDER": "disabled",
            "IFAP_DATABASE__URL": f"sqlite+aiosqlite:///{tmp_path / 'mcp.db'}",
        },
    )
    async with Client(params) as client:
        tools = (await client.list_tools()).tools
        result = await client.call_tool("search_templates", {"query": "burnout", "top_k": 2})
    assert {tool.name for tool in tools} == EXPECTED_TOOLS
    assert not result.is_error
