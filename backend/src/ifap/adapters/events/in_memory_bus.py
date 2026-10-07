"""In-process async event bus. Swap for Kafka / Azure Service Bus / Pub/Sub via the
`EventPublisher` port when agents move out of process (see ADR-006)."""

from __future__ import annotations

import asyncio
from collections import defaultdict

from ifap.application.ports import EventHandler
from ifap.domain.events import DomainEvent
from ifap.observability.logging import get_logger

_log = get_logger(__name__)


class InMemoryEventBus:
    def __init__(self) -> None:
        self._handlers: dict[type[DomainEvent], list[EventHandler]] = defaultdict(list)
        self.published: list[DomainEvent] = []

    def subscribe(self, event_type: type[DomainEvent], handler: EventHandler) -> None:
        self._handlers[event_type].append(handler)

    async def publish(self, event: DomainEvent) -> None:
        self.published.append(event)
        _log.info("event.published", event_name=event.name, event_id=str(event.event_id))
        handlers = self._handlers.get(type(event), [])
        results = await asyncio.gather(
            *(handler(event) for handler in handlers), return_exceptions=True
        )
        for result in results:
            if isinstance(result, BaseException):
                _log.error("event.handler_failed", event_name=event.name, error=str(result))
