import json
import logging
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
import pytest
from fastapi.testclient import TestClient
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    InMemoryMetricReader,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
)
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

from contract_app.api import create_app
from contract_app.documents.index_worker import DocumentIndexWorker
from contract_app.documents.parse_worker import DocumentParseWorker
from contract_app.telemetry import OperationMetrics, configure_from_environment, emit_operation
from contract_app.workflow.job_store import JobStore
from contract_app.workflow.outbox import LeasedEvent, OutboxDispatcher, OutboxStore
from contract_app.workflow.worker import Worker

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL")


def test_telemetry_is_disabled_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    import contract_app.telemetry as telemetry

    monkeypatch.delenv("CONTRACT_OTEL_EXPORTER", raising=False)
    monkeypatch.delenv("CONTRACT_OTEL_CONSOLE", raising=False)
    monkeypatch.setattr(telemetry, "configure_console_telemetry", lambda _service: pytest.fail())
    monkeypatch.setattr(telemetry, "configure_otlp_telemetry", lambda _service: pytest.fail())

    configure_from_environment("telemetry-test")


def test_legacy_console_flag_remains_supported(monkeypatch: pytest.MonkeyPatch) -> None:
    import contract_app.telemetry as telemetry

    configured: list[str] = []
    monkeypatch.delenv("CONTRACT_OTEL_EXPORTER", raising=False)
    monkeypatch.setenv("CONTRACT_OTEL_CONSOLE", "1")
    monkeypatch.setattr(telemetry, "configure_console_telemetry", configured.append)

    configure_from_environment("legacy-console-test")

    assert configured == ["legacy-console-test"]


def test_otlp_mode_uses_explicit_exporter_selection(monkeypatch: pytest.MonkeyPatch) -> None:
    import contract_app.telemetry as telemetry

    configured: list[str] = []
    monkeypatch.setenv("CONTRACT_OTEL_EXPORTER", "otlp")
    monkeypatch.delenv("CONTRACT_OTEL_CONSOLE", raising=False)
    monkeypatch.setattr(telemetry, "configure_otlp_telemetry", configured.append)

    configure_from_environment("otlp-test")

    assert configured == ["otlp-test"]


def test_otlp_protocol_honors_signal_override_and_http_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from contract_app.telemetry import _otlp_protocol

    monkeypatch.delenv("OTEL_EXPORTER_OTLP_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", raising=False)
    monkeypatch.delenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", raising=False)
    assert _otlp_protocol("traces") == "http/protobuf"
    assert _otlp_protocol("metrics") == "http/protobuf"

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    assert _otlp_protocol("traces") == "http/protobuf"
    assert _otlp_protocol("metrics") == "grpc"


def test_unsupported_otlp_protocol_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    from contract_app.telemetry import _otlp_protocol

    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/json")
    with pytest.raises(ValueError, match="unsupported_otlp_protocol"):
        _otlp_protocol("traces")


def test_otlp_exporters_follow_per_signal_protocols(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    pytest.importorskip("opentelemetry.exporter.otlp.proto.grpc.metric_exporter")
    from contract_app.telemetry import _create_otlp_exporters

    for variable in (
        "OTEL_EXPORTER_OTLP_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_HEADERS",
        "OTEL_EXPORTER_OTLP_TRACES_HEADERS",
        "OTEL_EXPORTER_OTLP_METRICS_HEADERS",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_METRICS_PROTOCOL", "grpc")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_TRACES_ENDPOINT", "https://collector.test/custom")

    span_exporter, metric_exporter = _create_otlp_exporters()

    assert "proto.http.trace_exporter" in type(span_exporter).__module__
    assert "proto.grpc.metric_exporter" in type(metric_exporter).__module__
    assert span_exporter._endpoint == "https://collector.test/custom"
    span_exporter.shutdown()
    metric_exporter.shutdown()


def test_otlp_http_exports_traces_and_metrics_to_local_receiver(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pytest.importorskip("opentelemetry.exporter.otlp.proto.http.trace_exporter")
    from contract_app.telemetry import _create_otlp_exporters

    requests: list[tuple[str, bytes, str | None]] = []

    class Receiver(BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = self.rfile.read(int(self.headers.get("Content-Length", "0")))
            requests.append((self.path, body, self.headers.get("x-api-key")))
            self.send_response(200)
            self.send_header("Content-Type", "application/x-protobuf")
            self.send_header("Content-Length", "0")
            self.end_headers()

        def log_message(self, _format: str, *_args: object) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    host, port = server.server_address
    for variable in (
        "OTEL_EXPORTER_OTLP_TRACES_ENDPOINT",
        "OTEL_EXPORTER_OTLP_METRICS_ENDPOINT",
        "OTEL_EXPORTER_OTLP_TRACES_PROTOCOL",
        "OTEL_EXPORTER_OTLP_METRICS_PROTOCOL",
    ):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_PROTOCOL", "http/protobuf")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_ENDPOINT", f"http://{host}:{port}")
    monkeypatch.setenv("OTEL_EXPORTER_OTLP_HEADERS", "x-api-key=local-test-token")

    tracer_provider: TracerProvider | None = None
    meter_provider: MeterProvider | None = None
    try:
        span_exporter, metric_exporter = _create_otlp_exporters()
        tracer_provider = TracerProvider()
        tracer_provider.add_span_processor(BatchSpanProcessor(span_exporter))
        meter_provider = MeterProvider(
            metric_readers=[PeriodicExportingMetricReader(metric_exporter, 60_000)]
        )

        with tracer_provider.get_tracer("local-export-test").start_as_current_span(
            "safe.test.operation"
        ) as span:
            span.set_attribute("operation", "safe.test.operation")
        meter_provider.get_meter("local-export-test").create_counter("safe.test.count").add(
            1, {"operation": "safe.test.operation"}
        )

        assert tracer_provider.force_flush(timeout_millis=5_000)
        assert meter_provider.force_flush(timeout_millis=5_000)
    finally:
        if tracer_provider is not None:
            tracer_provider.shutdown()
        if meter_provider is not None:
            meter_provider.shutdown()
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)

    assert {path for path, _body, _key in requests} >= {"/v1/traces", "/v1/metrics"}
    assert all(body and b"local-test-token" not in body for _path, body, _key in requests)
    assert all(key == "local-test-token" for _path, _body, key in requests)


def test_conflicting_and_unknown_telemetry_modes_fail_closed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTRACT_OTEL_EXPORTER", "otlp")
    monkeypatch.setenv("CONTRACT_OTEL_CONSOLE", "1")
    with pytest.raises(ValueError, match="conflicting_telemetry_exporter_configuration"):
        configure_from_environment("telemetry-test")

    monkeypatch.delenv("CONTRACT_OTEL_CONSOLE")
    monkeypatch.setenv("CONTRACT_OTEL_EXPORTER", "other")
    with pytest.raises(ValueError, match="invalid_telemetry_exporter"):
        configure_from_environment("telemetry-test")


def test_otlp_telemetry_uses_batch_spans_and_periodic_metrics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contract_app.telemetry as telemetry

    span_exporter = ConsoleSpanExporter()
    metric_exporter = ConsoleMetricExporter()
    tracer_providers: list[TracerProvider] = []
    meter_providers: list[MeterProvider] = []
    monkeypatch.setattr(
        telemetry, "_create_otlp_exporters", lambda: (span_exporter, metric_exporter)
    )
    monkeypatch.setattr(telemetry.trace, "set_tracer_provider", tracer_providers.append)
    monkeypatch.setattr(telemetry.metrics, "set_meter_provider", meter_providers.append)
    monkeypatch.setattr(telemetry, "_tracer_provider", None)
    monkeypatch.setattr(telemetry, "_meter_provider", None)
    monkeypatch.setattr(telemetry, "_operation_metrics", telemetry._operation_metrics)

    telemetry.configure_otlp_telemetry("otlp-provider-test")

    assert len(tracer_providers) == len(meter_providers) == 1
    tracer_provider = tracer_providers[0]
    meter_provider = meter_providers[0]
    assert tracer_provider.resource.attributes["service.name"] == "otlp-provider-test"
    assert meter_provider._sdk_config.resource.attributes["service.name"] == "otlp-provider-test"
    assert len(tracer_provider._active_span_processor._span_processors) == 1
    assert isinstance(
        tracer_provider._active_span_processor._span_processors[0],
        BatchSpanProcessor,
    )
    assert len(meter_provider._metric_readers) == 1
    assert isinstance(
        meter_provider._metric_readers[0],
        PeriodicExportingMetricReader,
    )
    telemetry.shutdown_telemetry()


def _tracer() -> tuple[TracerProvider, InMemorySpanExporter]:
    provider = TracerProvider()
    exporter = InMemorySpanExporter()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    return provider, exporter


def test_api_request_trace_and_structured_log_exclude_query_and_body(
    caplog: pytest.LogCaptureFixture,
) -> None:
    provider, exporter = _tracer()
    app = create_app(provider.get_tracer("api-test"))
    with caplog.at_level(logging.INFO, logger="contract_app.operations"):
        response = TestClient(app).get("/health/live?contract_text=SECRET_AMOUNT_123")
    assert response.status_code == 200
    UUID(response.headers["X-Request-ID"])
    spans = exporter.get_finished_spans()
    assert len(spans) == 1
    attributes = spans[0].attributes
    assert attributes["http.route"] == "/health/live"
    assert attributes["http.response.status_code"] == 200
    assert "SECRET_AMOUNT_123" not in str(attributes)
    records = [
        json.loads(record.message)
        for record in caplog.records
        if record.name == "contract_app.operations"
    ]
    assert len(records) == 1
    assert records[0]["event"] == "http.request"
    assert records[0]["request_id"] == response.headers["X-Request-ID"]
    assert "SECRET_AMOUNT_123" not in str(records)
    provider.shutdown()


def test_api_owned_telemetry_is_shutdown_with_app_lifespan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import contract_app.api as api

    shutdown_calls: list[bool] = []
    monkeypatch.setattr(api, "shutdown_telemetry", lambda: shutdown_calls.append(True))
    app = api.create_app(owns_telemetry_providers=True)

    with TestClient(app) as client:
        assert client.get("/health/live").status_code == 200

    assert shutdown_calls == [True]


def test_log_guard_rejects_payload_and_unstructured_identifiers() -> None:
    with pytest.raises(ValueError, match="unsafe_operation_log_fields"):
        emit_operation("workflow.task", payload="contract content")
    with pytest.raises(ValueError, match="unsafe_operation_log_fields"):
        emit_operation("workflow.task", task_id="secret contract content")
    with pytest.raises(ValueError, match="unsafe_operation_log_fields"):
        emit_operation("http.request", route="/cases/1?secret=contract")
    with pytest.raises(ValueError, match="unsafe_operation_log_fields"):
        emit_operation("workflow.task", outcome="completed", queue_wait_ms=-1)


def test_operation_metrics_use_only_low_cardinality_safe_attributes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reader = InMemoryMetricReader()
    provider = MeterProvider(metric_readers=[reader])
    recorder = OperationMetrics(provider.get_meter("telemetry-test"))
    monkeypatch.setattr("contract_app.telemetry._operation_metrics", recorder)

    emit_operation(
        "http.request",
        request_id=str(uuid4()),
        trace_id="a" * 32,
        method="GET",
        route="/health/live",
        status_code=503,
        duration_ms=12.5,
    )
    emit_operation(
        "workflow.task",
        task_id=str(uuid4()),
        outcome="retried",
        duration_ms=80,
        queue_wait_ms=123.5,
    )
    emit_operation("document.parse", outcome="queued", duration_ms=120450.8)

    class EmptyJobStore:
        def claim(self, *_args: object, **_kwargs: object) -> None:
            return None

    tenant_id = uuid4()
    parse_worker = DocumentParseWorker(EmptyJobStore(), tenant_id, "parse-test", object())
    index_worker = DocumentIndexWorker(EmptyJobStore(), tenant_id, "index-test", object(), object())
    assert parse_worker.run_once() == "idle"
    assert index_worker.run_once() == "idle"

    class ClaimedJobStore:
        def __init__(self, queue_wait_ms: float) -> None:
            self.job = SimpleNamespace(storage_backend="unexpected", queue_wait_ms=queue_wait_ms)

        def claim(self, *_args: object, **_kwargs: object) -> SimpleNamespace:
            return self.job

        def fail(self, *_args: object, **_kwargs: object) -> str:
            return "failed"

        def retry_or_fail(self, *_args: object, **_kwargs: object) -> str:
            return "failed"

    object_store = SimpleNamespace(backend="expected")
    parse_worker = DocumentParseWorker(ClaimedJobStore(7.5), tenant_id, "parse-test", object_store)
    index_worker = DocumentIndexWorker(
        ClaimedJobStore(9.25), tenant_id, "index-test", object_store, object()
    )
    assert parse_worker.run_once() == "failed"
    assert index_worker.run_once() == "failed"

    resource_metrics = reader.get_metrics_data().resource_metrics
    metrics_by_name = {
        metric.name: metric
        for scope in resource_metrics[0].scope_metrics
        for metric in scope.metrics
    }
    operations = metrics_by_name["contract_app.operations"].data.data_points
    assert {frozenset(point.attributes.items()) for point in operations} == {
        frozenset(
            {
                ("operation", "http.request"),
                ("http.method", "GET"),
                ("http.route", "/health/live"),
                ("http.status_class", "5xx"),
            }
        ),
        frozenset({("operation", "workflow.task"), ("outcome", "retried")}),
        frozenset({("operation", "document.parse"), ("outcome", "idle")}),
        frozenset({("operation", "document.parse"), ("outcome", "queued")}),
        frozenset({("operation", "document.index"), ("outcome", "idle")}),
        frozenset({("operation", "document.parse"), ("outcome", "failed")}),
        frozenset({("operation", "document.index"), ("outcome", "failed")}),
    }
    durations = metrics_by_name["contract_app.operation.duration"].data.data_points
    assert {point.sum for point in durations} >= {12.5, 80.0}
    assert len(durations) == 7
    queue_waits = metrics_by_name["contract_app.queue.wait"].data.data_points
    assert {frozenset(point.attributes.items()): point.sum for point in queue_waits} == {
        frozenset({("operation", "workflow.task"), ("outcome", "retried")}): 123.5,
        frozenset({("operation", "document.parse"), ("outcome", "failed")}): 7.5,
        frozenset({("operation", "document.index"), ("outcome", "failed")}): 9.25,
    }
    forbidden_dimensions = {"request_id", "trace_id", "task_id"}
    assert all(
        not forbidden_dimensions.intersection(point.attributes)
        for point in (*operations, *durations, *queue_waits)
    )
    provider.shutdown()


@pytest.mark.skipif(not TEST_DATABASE_URL, reason="TEST_DATABASE_URL is not set")
def test_worker_and_outbox_spans_do_not_capture_payload_or_exception_text(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert TEST_DATABASE_URL is not None
    provider, exporter = _tracer()
    tracer = provider.get_tracer("workflow-test")
    metric_reader = InMemoryMetricReader()
    metric_provider = MeterProvider(metric_readers=[metric_reader])
    monkeypatch.setattr(
        "contract_app.telemetry._operation_metrics",
        OperationMetrics(metric_provider.get_meter("workflow-test")),
    )
    tenant = uuid4()
    jobs = JobStore(TEST_DATABASE_URL)
    _, run_id = jobs.create_case_and_run(tenant, "trace fixture")
    app = create_app(tracer)

    @app.post("/test/enqueue")
    def enqueue_fixture() -> dict[str, str]:
        task_id = jobs.enqueue(
            tenant, run_id, "secret_task", "trace-task", {"secret": "CONTRACT_TEXT_42"}
        )
        return {"task_id": str(task_id)}

    with caplog.at_level(logging.INFO, logger="contract_app.operations"):
        response = TestClient(app).post("/test/enqueue?contract_text=CONTRACT_TEXT_42")
        assert response.status_code == 200
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE tasks SET created_at = now() - interval '2 seconds', "
            "next_run_at = now() - interval '2 seconds' WHERE id = %s",
            (response.json()["task_id"],),
        )

    def fail_with_secret(_payload: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("CONTRACT_TEXT_42")

    worker = Worker(
        jobs,
        tenant,
        "trace-worker",
        {"secret_task": fail_with_secret},
        max_attempts=1,
        tracer=tracer,
    )
    with caplog.at_level(logging.INFO, logger="contract_app.operations"):
        assert worker.run_once() == "failed"

        def fail_publish(_event: LeasedEvent) -> None:
            raise RuntimeError("CONTRACT_TEXT_42")

        dispatcher = OutboxDispatcher(
            OutboxStore(TEST_DATABASE_URL),
            tenant,
            "trace-dispatcher",
            fail_publish,
            max_attempts=1,
            tracer=tracer,
        )
    with psycopg.connect(TEST_DATABASE_URL) as connection:
        connection.execute("SELECT set_config('app.tenant_id', %s, true)", (str(tenant),))
        connection.execute(
            "UPDATE outbox_events SET created_at = now() - interval '2 seconds', "
            "next_attempt_at = now() - interval '2 seconds'"
        )
    with caplog.at_level(logging.INFO, logger="contract_app.operations"):
        assert dispatcher.dispatch_once() == "dead_lettered"
    spans = exporter.get_finished_spans()
    assert [span.name for span in spans] == ["http.request", "workflow.task", "outbox.delivery"]
    assert spans[1].attributes["workflow.outcome"] == "failed"
    assert spans[2].attributes["outbox.outcome"] == "dead_lettered"
    assert spans[1].parent is not None and spans[1].parent.span_id == spans[0].context.span_id
    assert spans[2].parent is not None and spans[2].parent.span_id == spans[1].context.span_id
    assert len({span.context.trace_id for span in spans}) == 1
    assert "CONTRACT_TEXT_42" not in str([span.attributes for span in spans])
    assert "CONTRACT_TEXT_42" not in str([record.message for record in caplog.records])
    queue_waits = {
        frozenset(point.attributes.items()): point.sum
        for resource in metric_reader.get_metrics_data().resource_metrics
        for scope in resource.scope_metrics
        for metric in scope.metrics
        if metric.name == "contract_app.queue.wait"
        for point in metric.data.data_points
    }
    assert queue_waits == {
        frozenset({("operation", "workflow.task"), ("outcome", "failed")}): pytest.approx(
            2000, abs=500
        ),
        frozenset({("operation", "outbox.delivery"), ("outcome", "dead_lettered")}): pytest.approx(
            2000, abs=500
        ),
    }
    metric_provider.shutdown()
    provider.shutdown()
