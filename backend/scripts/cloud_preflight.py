"""Check the cloud dependencies in backend/.env.cloud before deploying. Never prints secrets.

    cd backend && .venv/bin/python scripts/cloud_preflight.py

1. Neon: region (to pick the matching Cloud Run region), connectivity, Alembic migrations.
2. Gemini: models available to the key, and one real structured-output call.
"""

from __future__ import annotations

import asyncio
import time
from pathlib import Path

import httpx
from pydantic import SecretStr
from sqlalchemy import text

from ifap.adapters.llm.openai_compatible import OpenAICompatibleLLMClient
from ifap.adapters.persistence.schema import create_engine, migrate, translate_url
from ifap.agents.builtin.intent_agent import IntentExtraction
from ifap.application.ports import LLMUnavailableError
from ifap.config.settings import DatabaseSettings, LLMProviderKind, LLMSettings

ENV_FILE = Path(__file__).resolve().parents[1] / ".env.cloud"


def read_env(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.lstrip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip().strip("\"'")
    return values


async def check_database(url: str) -> None:
    parsed, connect_args = translate_url(url)
    region = (parsed.host or "").split(".")[1:3]
    print(f"[neon] host region: {'.'.join(region) or 'unknown'} | tls: {connect_args or 'none'}")
    engine = create_engine(DatabaseSettings(url=url))
    started = time.perf_counter()
    async with engine.connect() as connection:
        version = (await connection.execute(text("select version()"))).scalar_one()
    print(f"[neon] connected in {time.perf_counter() - started:.1f}s: {str(version)[:40]}")
    await migrate(engine)
    async with engine.connect() as connection:
        revision = (
            await connection.execute(text("select version_num from alembic_version"))
        ).scalar_one()
    print(f"[neon] migrations applied, schema revision {revision}")
    await engine.dispose()


async def check_gemini(api_key: str) -> None:
    settings = LLMSettings(provider=LLMProviderKind.GEMINI, api_key=SecretStr(api_key))
    async with httpx.AsyncClient(timeout=30) as client:
        response = await client.get(
            f"{settings.resolved_base_url}models", headers={"Authorization": f"Bearer {api_key}"}
        )
    response.raise_for_status()
    models = sorted(m["id"].removeprefix("models/") for m in response.json().get("data", []))
    flash = [m for m in models if "flash" in m and "image" not in m and "tts" not in m]
    print(f"[gemini] {len(models)} models available; flash models: {flash[:8]}")
    print(f"[gemini] configured default model: {settings.resolved_model}")
    started = time.perf_counter()
    try:
        result = await OpenAICompatibleLLMClient(settings).generate_structured(
            system="Classify the need. survey_type is one of: customer_satisfaction, "
            "employee_engagement, compliance_review.",
            user="Quarterly pulse on employee burnout",
            output_type=IntentExtraction,
        )
        print(
            f"[gemini] structured call ok in {time.perf_counter() - started:.1f}s: "
            f"survey_type={result.survey_type}"
        )
    except LLMUnavailableError as exc:
        print(f"[gemini] structured call FAILED: {exc}")


async def main() -> None:
    if not ENV_FILE.exists():
        raise SystemExit(f"Create {ENV_FILE} first (see README > Deployment)")
    env = read_env(ENV_FILE)
    for key in ("IFAP_DATABASE__URL", "IFAP_LLM__API_KEY"):
        print(f"[env] {key}: {'set' if env.get(key) else 'MISSING'}")
    if env.get("IFAP_DATABASE__URL"):
        await check_database(env["IFAP_DATABASE__URL"])
    if env.get("IFAP_LLM__API_KEY"):
        await check_gemini(env["IFAP_LLM__API_KEY"])


if __name__ == "__main__":
    asyncio.run(main())
