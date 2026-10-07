"""`TemplateSource` adapter that reads templates from a JSON file."""

from __future__ import annotations

from pathlib import Path

from pydantic import TypeAdapter

from ifap.domain.intent import IntentTaxonomy
from ifap.domain.knowledge import QuestionTemplate

_TEMPLATES = TypeAdapter(list[QuestionTemplate])


class JsonTemplateSource:
    def __init__(self, path: Path) -> None:
        self._path = path

    @property
    def name(self) -> str:
        return self._path.name

    def load(self) -> list[QuestionTemplate]:
        return _TEMPLATES.validate_json(self._path.read_bytes())


def load_taxonomy(path: Path) -> IntentTaxonomy:
    return IntentTaxonomy.model_validate_json(path.read_bytes())
