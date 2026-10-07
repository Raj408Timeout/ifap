"""SQLAlchemy 2.0 async repository. PostgreSQL in Docker, SQLite locally/in tests.

The aggregate is stored as a JSON document plus indexed scalar columns (document-relational
hybrid): questionnaires are read/written as a whole, while list/filter queries use columns.
Schema migrations move to Alembic in Phase 2.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import JSON, DateTime, Integer, String, Uuid, select
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from ifap.config.settings import DatabaseSettings
from ifap.domain.questionnaire import Questionnaire


class Base(DeclarativeBase):
    pass


class QuestionnaireRecord(Base):
    __tablename__ = "questionnaires"

    id: Mapped[UUID] = mapped_column(Uuid, primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    survey_type: Mapped[str] = mapped_column(String(100), index=True)
    status: Mapped[str] = mapped_column(String(20), index=True)
    version: Mapped[int] = mapped_column(Integer)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)


def create_engine(settings: DatabaseSettings) -> AsyncEngine:
    return create_async_engine(settings.url, echo=settings.echo)


async def create_schema(engine: AsyncEngine) -> None:
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)


class SqlAlchemyQuestionnaireRepository:
    def __init__(self, engine: AsyncEngine) -> None:
        self._sessions = async_sessionmaker(engine, expire_on_commit=False)

    async def save(self, questionnaire: Questionnaire) -> None:
        async with self._sessions.begin() as session:
            await session.merge(_to_record(questionnaire))

    async def get(self, questionnaire_id: UUID) -> Questionnaire | None:
        async with self._sessions() as session:
            record = await session.get(QuestionnaireRecord, questionnaire_id)
            return _to_domain(record) if record else None

    async def list(self, *, limit: int, offset: int) -> list[Questionnaire]:
        statement = (
            select(QuestionnaireRecord)
            .order_by(QuestionnaireRecord.updated_at.desc())
            .limit(limit)
            .offset(offset)
        )
        async with self._sessions() as session:
            records = (await session.scalars(statement)).all()
            return [_to_domain(record) for record in records]


def _to_record(questionnaire: Questionnaire) -> QuestionnaireRecord:
    return QuestionnaireRecord(
        id=questionnaire.id,
        title=questionnaire.title,
        survey_type=questionnaire.survey_type,
        status=questionnaire.status.value,
        version=questionnaire.version,
        payload=questionnaire.model_dump(mode="json"),
        created_at=questionnaire.created_at,
        updated_at=questionnaire.updated_at,
    )


def _to_domain(record: QuestionnaireRecord) -> Questionnaire:
    return Questionnaire.model_validate(record.payload)
