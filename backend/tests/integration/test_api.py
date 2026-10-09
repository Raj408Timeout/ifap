# pylint: disable=redefined-outer-name  # pytest fixtures are injected by name
"""End-to-end: real FastAPI app, real Chroma (temp dir), real SQLite, LangGraph pipeline."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ifap.api.app import create_app
from ifap.config.settings import (
    KnowledgeProviderKind,
    LLMProviderKind,
    LLMSettings,
    Settings,
    WorkflowDefinition,
)

pytestmark = pytest.mark.integration


@pytest.fixture
def client(app_settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(app_settings)) as test_client:
        yield test_client


def test_health_reports_seeded_knowledge(client: TestClient) -> None:
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert body["knowledge_documents"] == 200


def test_generate_review_revise_publish(client: TestClient) -> None:
    created = client.post(
        "/api/v1/questionnaires/generate",
        json={"message": "Build an 8 question compliance review for GDPR and vendor risk"},
    )
    assert created.status_code == 201, created.text
    body = created.json()
    questionnaire = body["questionnaire"]
    assert body["validation"]["is_valid"]
    assert questionnaire["survey_type"] == "compliance_review"
    assert len(questionnaire["questions"]) == 8
    assert [t["agent"] for t in body["trace"]] == [
        "intent",
        "template_retrieval",
        "questionnaire_builder",
        "validation",
    ]

    qid = questionnaire["id"]
    assert client.get(f"/api/v1/questionnaires/{qid}").json()["id"] == qid

    revised = client.put(
        f"/api/v1/questionnaires/{qid}",
        json={
            "title": "GDPR Review",
            "description": "",
            "questions": questionnaire["questions"][:3],
        },
    ).json()
    assert revised["questionnaire"]["version"] == 2
    assert len(revised["questionnaire"]["questions"]) == 3

    published = client.post(f"/api/v1/questionnaires/{qid}/publish").json()
    assert published["status"] == "published"
    conflict = client.put(
        f"/api/v1/questionnaires/{qid}",
        json={"title": "Again", "description": "", "questions": []},
    )
    assert conflict.status_code == 422


def test_unknown_questionnaire_is_404(client: TestClient) -> None:
    response = client.get("/api/v1/questionnaires/00000000-0000-0000-0000-000000000000")
    assert response.status_code == 404


def test_knowledge_search_with_metadata_filter(client: TestClient) -> None:
    results = client.get(
        "/api/v1/knowledge/search",
        params={"q": "pain severity", "survey_type": "healthcare_assessment"},
    ).json()["results"]
    assert results
    assert all(r["template"]["metadata"]["survey_type"] == "healthcare_assessment" for r in results)


def test_agents_catalogue(client: TestClient) -> None:
    body = client.get("/api/v1/agents").json()
    assert body["default_workflow"] == "standard"
    assert body["workflows"]["standard"][0] == "intent"
    assert "autonomous_builder" in body["workflows"]["autonomous"]
    assert body["llm_enabled"] is False


def test_llm_switch_without_provider(client: TestClient) -> None:
    status = client.get("/api/v1/llm").json()
    assert status == {
        "provider": "disabled",
        "model": None,
        "configured": False,
        "enabled": False,
        "models": [],
    }
    assert client.put("/api/v1/llm", json={"enabled": True}).status_code == 422


def test_unreachable_ollama_falls_back_to_heuristics(app_settings: Settings) -> None:
    # Port 9 (discard) is closed locally: simulates "Ollama is not running".
    settings = app_settings.model_copy(
        update={
            "llm": LLMSettings(
                provider=LLMProviderKind.OLLAMA, base_url="http://127.0.0.1:9/v1", max_retries=0
            )
        }
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/api/v1/llm").json()["enabled"] is True
        body = client.post(
            "/api/v1/questionnaires/generate", json={"message": "customer delivery survey"}
        ).json()
        llm_steps = [t for t in body["trace"] if t["agent"] in {"intent", "questionnaire_builder"}]
        assert all(t["strategy"] == "heuristic" for t in llm_steps)
        assert all("ConnectionError" in t["note"] for t in llm_steps)
        assert body["validation"]["is_valid"]

        off = client.put("/api/v1/llm", json={"enabled": False}).json()
        assert off["enabled"] is False
        body = client.post(
            "/api/v1/questionnaires/generate", json={"message": "customer delivery survey"}
        ).json()
        assert body["trace"][0]["note"] == "fallback: LLM switched off"


def test_list_and_reingest(client: TestClient) -> None:
    created = client.post("/api/v1/questionnaires/generate", json={"message": "product feedback"})
    listed = client.get("/api/v1/questionnaires", params={"limit": 5}).json()
    assert listed[0]["id"] == created.json()["questionnaire"]["id"]

    ingested = client.post("/api/v1/knowledge/ingest").json()
    assert ingested == {"indexed": 200, "total": 200}  # idempotent upsert


def test_broken_pipeline_returns_502(app_settings: Settings) -> None:
    broken = WorkflowDefinition(steps=["intent", "validation"])
    workflow = app_settings.workflow.model_copy(update={"workflows": {"standard": broken}})
    with TestClient(create_app(app_settings.model_copy(update={"workflow": workflow}))) as client:
        response = client.post("/api/v1/questionnaires/generate", json={"message": "a survey"})
    assert response.status_code == 502
    assert response.json()["error"] == "AgentExecutionError"


def test_startup_can_skip_ingestion_and_warns_on_typos(
    app_settings: Settings, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("IFAP_LLM_PROVIDER", "ollama")  # single-underscore typo
    knowledge = app_settings.knowledge.model_copy(
        update={"provider": KnowledgeProviderKind.IN_MEMORY, "ingest_on_startup": False}
    )
    observability = app_settings.observability.model_copy(update={"log_level": "INFO"})
    settings = app_settings.model_copy(
        update={"knowledge": knowledge, "observability": observability}
    )
    with TestClient(create_app(settings)) as client:
        assert client.get("/health").json()["knowledge_documents"] == 0
    output = capsys.readouterr().out
    assert "config.unrecognised_env_var" in output
    assert "IFAP_LLM_PROVIDER" in output
