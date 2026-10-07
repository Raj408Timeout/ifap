"""OpenTelemetry tracing + metrics bootstrap. Exporters are configuration driven."""

from __future__ import annotations

from opentelemetry import metrics, trace
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import BatchSpanProcessor, ConsoleSpanExporter

from ifap.config.settings import ObservabilitySettings

INSTRUMENTATION_NAME = "ifap"

tracer = trace.get_tracer(INSTRUMENTATION_NAME)
meter = metrics.get_meter(INSTRUMENTATION_NAME)

agent_runs = meter.create_counter(
    "ifap.agent.runs", unit="1", description="Agent executions by agent and status"
)
agent_duration = meter.create_histogram(
    "ifap.agent.duration", unit="ms", description="Agent execution latency"
)


def configure_tracing(settings: ObservabilitySettings) -> None:
    """Install the SDK tracer provider once per process (OTel forbids overriding it)."""
    if isinstance(trace.get_tracer_provider(), TracerProvider):
        return
    provider = TracerProvider(resource=Resource.create({"service.name": settings.service_name}))
    if settings.console_traces:
        provider.add_span_processor(BatchSpanProcessor(ConsoleSpanExporter()))
    trace.set_tracer_provider(provider)
