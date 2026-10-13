"""OpenTelemetry tracing: one trace per request, with its database queries, its outbound
HTTP calls and the background jobs it starts as spans inside it.

Off by default and optional twice over: ``tracing_enabled`` turns it on, and the packages
come from the ``tracing`` extra (``pip install "curvevision-server[tracing]"``), so a
server that does not trace imports none of them. When on, spans go to an OTLP/HTTP
collector — Jaeger, Tempo, Honeycomb, Datadog's agent, the OpenTelemetry Collector — at
``tracing_otlp_endpoint``, or wherever the standard ``OTEL_EXPORTER_OTLP_ENDPOINT`` says.

What is traced:

* every HTTP request, by route template (FastAPI's instrumentation) — except ``/metrics``
  and the health check, which a scraper calls every few seconds;
* every SQL statement (SQLAlchemy's), as children of the request that issued it;
* every outbound HTTP call through httpx — model servers, hosted models, webhooks;
* every background job, as a span named for its kind (`run_task`).

Log lines carry the ``trace_id`` and ``span_id`` of the span they were written in
(`core.logging`), so a trace and its log lines find each other.

Until iteration 78 the plan called tracing *Planned* with "the instrumentation seam in
`core/observability`"; that module said where tracing would attach, and nothing did.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

from curvevision import __version__
from curvevision.core.config import Settings
from curvevision.core.logging import get_logger

if TYPE_CHECKING:
    from fastapi import FastAPI
    from sqlalchemy.ext.asyncio import AsyncEngine

logger = get_logger(__name__)

#: The provider in use, once `configure` has made one; `None` when tracing is off.
_provider: Any = None
#: Requests these routes make are not traced: they are polled, and would drown the rest.
EXCLUDED_URLS = "/metrics,/api/v1/health"


def _require() -> None:
    try:
        import opentelemetry.sdk.trace  # noqa: F401
    except ImportError as exc:
        raise RuntimeError(
            'tracing_enabled needs the tracing extra: pip install "curvevision-server[tracing]"'
        ) from exc


def configure(settings: Settings, *, exporter: Any = None) -> Any:
    """Make the tracer provider for this process, once; `None` when tracing is off.

    `exporter` replaces the OTLP one — tests pass an in-memory exporter, which is then
    flushed span by span rather than in batches.
    """
    global _provider
    if not settings.tracing_enabled:
        return None
    if _provider is not None and exporter is None:
        return _provider
    _require()
    from opentelemetry import trace
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor, SimpleSpanProcessor
    from opentelemetry.sdk.trace.sampling import ParentBased, TraceIdRatioBased

    provider = TracerProvider(
        resource=Resource.create(
            {
                "service.name": settings.tracing_service_name,
                "service.version": __version__,
                "deployment.environment": settings.environment,
            }
        ),
        # A request that arrives inside someone else's trace keeps their sampling decision.
        sampler=ParentBased(TraceIdRatioBased(settings.tracing_sample_ratio)),
    )
    if exporter is None:
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter

        # `None` leaves the standard OTEL_EXPORTER_OTLP_ENDPOINT, or its default, to decide.
        exporter = OTLPSpanExporter(endpoint=settings.tracing_otlp_endpoint)
        provider.add_span_processor(BatchSpanProcessor(exporter))
    else:
        provider.add_span_processor(SimpleSpanProcessor(exporter))

    # The process-wide provider is set once; a second app in one process (tests) uses its
    # own provider through the instrumentors' `tracer_provider` arguments instead.
    if _provider is None:
        trace.set_tracer_provider(provider)
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=provider)
    _provider = provider
    logger.info(
        "tracing on",
        extra={
            "endpoint": settings.tracing_otlp_endpoint or "OTEL_EXPORTER_OTLP_ENDPOINT",
            "sample_ratio": settings.tracing_sample_ratio,
        },
    )
    return provider


def instrument_app(app: FastAPI, provider: Any) -> None:
    from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

    FastAPIInstrumentor.instrument_app(app, tracer_provider=provider, excluded_urls=EXCLUDED_URLS)


def instrument_engine(engine: AsyncEngine) -> None:
    """Trace this engine's statements, when tracing is on."""
    if _provider is None:
        return
    from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

    # `skip_dep_check`: the instrumentation declares `sqlalchemy < 2.1` and, against 2.1,
    # instruments *nothing* — silently, but for one log line. It hooks SQLAlchemy's
    # `before_cursor_execute` events, which 2.1 keeps; `test_tracing.py` checks the spans
    # actually arrive, so a release that broke it would fail there rather than in the dark.
    SQLAlchemyInstrumentor().instrument(
        engine=engine.sync_engine,
        tracer_provider=_provider,
        enable_commenter=False,
        skip_dep_check=True,
    )


@asynccontextmanager
async def span(name: str, **attributes: Any) -> AsyncIterator[None]:
    """A span named `name` when tracing is on; nothing at all when it is off."""
    if _provider is None:
        yield
        return
    tracer = _provider.get_tracer("curvevision")
    with tracer.start_as_current_span(name, attributes=attributes):
        yield


def current_ids() -> tuple[str, str] | None:
    """The current span's trace and span ids, hex-encoded, or `None` outside one."""
    if _provider is None:
        return None
    from opentelemetry import trace

    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}", f"{context.span_id:016x}"


def shutdown() -> None:
    """Flush what is buffered: a batch not exported when the process stops is lost."""
    if _provider is not None:
        _provider.force_flush()


def reset() -> None:
    """Forget the provider and undo the process-wide instrumentation. Tests only."""
    global _provider
    if _provider is not None:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor
        from opentelemetry.instrumentation.sqlalchemy import SQLAlchemyInstrumentor

        SQLAlchemyInstrumentor().uninstrument()
        HTTPXClientInstrumentor().uninstrument()
    _provider = None
