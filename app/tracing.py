"""Optional OpenTelemetry tracing of agent runs, for local debugging only.

Traces contain prompts, tool arguments and tool results exactly as the model saw them: pseudonymized, but still
account data. They are sent over OTLP to OTEL_EXPORTER_OTLP_ENDPOINT (default http://localhost:4318), e.g. Jaeger.
The OpenTelemetry packages are dev dependencies, so this only works in a local dev environment.
"""

from pydantic_ai import Agent


def setup() -> None:
    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    provider = TracerProvider(resource=Resource.create({"service.name": "gw2-wayfinder"}))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter()))
    trace.set_tracer_provider(provider)
    Agent.instrument_all()
