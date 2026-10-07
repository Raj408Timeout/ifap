"""Runtime on/off switch for the LLM (Proxy pattern over the `LLMClient` port).

Agents keep calling `generate_structured`; when the switch is off (or no provider is
configured) the call raises `LLMUnavailableError`, which every LLM-backed agent already
handles by falling back to its deterministic heuristic. Flipping the switch therefore
changes behaviour instantly, without a restart and without agents knowing it exists.

The switch is process-local and not persisted: a restart resets it to `IFAP_LLM__ENABLED`.
"""

from __future__ import annotations

from pydantic import BaseModel

from ifap.application.ports import LLMClient, LLMStatus, LLMUnavailableError
from ifap.domain.errors import DomainRuleViolationError


class SwitchableLLMClient:
    def __init__(
        self, inner: LLMClient | None, *, provider: str, model: str | None, enabled: bool
    ) -> None:
        self._inner = inner
        self._provider = provider
        self._model = model
        self._enabled = enabled and inner is not None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def status(self) -> LLMStatus:
        return LLMStatus(
            provider=self._provider,
            model=self._model,
            configured=self._inner is not None,
            enabled=self._enabled,
        )

    def set_enabled(self, enabled: bool) -> LLMStatus:
        if enabled and self._inner is None:
            raise DomainRuleViolationError(
                "No LLM provider configured - set IFAP_LLM__PROVIDER (e.g. 'ollama') and restart"
            )
        self._enabled = enabled
        return self.status()

    async def generate_structured[T: BaseModel](
        self, *, system: str, user: str, output_type: type[T]
    ) -> T:
        if self._inner is None:
            raise LLMUnavailableError("no LLM provider configured")
        if not self._enabled:
            raise LLMUnavailableError("LLM switched off")
        return await self._inner.generate_structured(
            system=system, user=user, output_type=output_type
        )
