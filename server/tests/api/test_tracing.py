"""OpenTelemetry tracing, read back from an in-memory exporter.

Until iteration 78 tracing was *Planned*, and `core/observability.py` said where it would
attach. These check what an operator would look for in a trace viewer: a request is one
trace, named by its route; its SQL statements are spans inside it; a background job it
starts is a span too; the endpoints a scraper polls make no traces; and a log line written
during a request names the trace it belongs to.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from typing import Any

import pytest

pytest.importorskip("opentelemetry.sdk", reason="tracing is the `tracing` extra")

from opentelemetry.sdk.trace.export.in_memory_span_exporter import (
    InMemorySpanExporter,
)
from opentelemetry.trace import SpanKind

from curvevision.core import tracing
from curvevision.core.config import Settings
from curvevision.core.logging import JSONFormatter
from tests.api.test_collaboration import make_task
from tests.conftest import ApiActor


@pytest.fixture
def exporter() -> Iterator[InMemorySpanExporter]:
    tracing.reset()
    captured = InMemorySpanExporter()
    yield captured
    tracing.reset()


@pytest.fixture
def settings(settings: Settings, exporter: InMemorySpanExporter) -> Settings:
    """The suite's settings with tracing on, exporting to memory — before the app exists."""
    traced = settings.model_copy(update={"tracing_enabled": True})
    tracing.configure(traced, exporter=exporter)
    return traced


@pytest.fixture(autouse=True)
def traced_engine(engine: Any, settings: Settings) -> None:
    # The suite's app reads through the test's engine rather than the one the app's own
    # lifespan makes, so that is the one to instrument.
    tracing.instrument_engine(engine)


def server_spans(exporter: InMemorySpanExporter) -> list[Any]:
    return [s for s in exporter.get_finished_spans() if s.kind is SpanKind.SERVER]


async def test_a_request_is_a_trace_named_by_its_route_with_its_queries_inside(
    owner: ApiActor, project: dict[str, Any], exporter: InMemorySpanExporter
) -> None:
    exporter.clear()

    response = await owner.get(f"/api/v1/projects/{project['id']}")

    assert response.status_code == 200
    [request] = [s for s in server_spans(exporter) if "/projects/" in s.name]
    assert request.name == "GET /api/v1/projects/{project_id}"  # the template, not the id
    assert request.attributes is not None
    assert (
        request.attributes.get(
            "http.status_code", request.attributes.get("http.response.status_code")
        )
        == 200
    )
    queries = [
        s
        for s in exporter.get_finished_spans()
        if (s.attributes and "db.statement" in s.attributes)
        or (s.attributes and "db.query.text" in s.attributes)
    ]
    assert queries, "no SQL span was recorded"
    assert {q.context.trace_id for q in queries} == {request.context.trace_id}


async def test_what_a_scraper_polls_makes_no_trace(
    owner: ApiActor, exporter: InMemorySpanExporter
) -> None:
    exporter.clear()

    assert (await owner.get("/api/v1/health")).status_code == 200
    await owner.get("/metrics")

    assert server_spans(exporter) == []


async def test_a_background_job_is_a_span_in_the_trace_that_queued_it(
    owner: ApiActor, project: dict[str, Any], exporter: InMemorySpanExporter
) -> None:
    """Recounting a task's media queues a probe; the inline queue runs it in the request."""
    task, _jobs = await make_task(owner, project, frames=1)
    exporter.clear()

    queued = await owner.post(f"/api/v1/tasks/{task['id']}/media/recount")

    assert queued.status_code in (200, 201, 202), queued.text
    [job] = [s for s in exporter.get_finished_spans() if s.name == "job media.probe_task"]
    [request] = [s for s in server_spans(exporter) if s.name.endswith("/media/recount")]
    assert job.context.trace_id == request.context.trace_id


async def test_a_log_line_names_the_trace_it_was_written_in(
    exporter: InMemorySpanExporter,
) -> None:
    record = logging.LogRecord("curvevision.test", logging.INFO, __file__, 1, "hello", None, None)

    async with tracing.span("work"):
        ids = tracing.current_ids()
        line = json.loads(JSONFormatter().format(record))

    assert ids is not None
    assert (line["trace_id"], line["span_id"]) == ids
    [work] = [s for s in exporter.get_finished_spans() if s.name == "work"]
    assert f"{work.context.trace_id:032x}" == ids[0]


def test_off_by_default_it_makes_nothing() -> None:
    tracing.reset()
    assert tracing.configure(Settings(secret_key="x" * 32)) is None
    assert tracing.current_ids() is None
