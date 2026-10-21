"""Explicit, metadata-only operational traces and structured logs."""

import json
import logging
import math
import os
import re
from collections.abc import Callable
from typing import Any
from uuid import UUID

from opentelemetry import metrics, trace
from opentelemetry.context import Context
from opentelemetry.metrics import Meter
from opentelemetry.sdk.metrics import MeterProvider
from opentelemetry.sdk.metrics.export import (
    ConsoleMetricExporter,
    MetricExporter,
    PeriodicExportingMetricReader,
)
from opentelemetry.sdk.resources import Resource
from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import (
    BatchSpanProcessor,
    ConsoleSpanExporter,
    SimpleSpanProcessor,
    SpanExporter,
)
from opentelemetry.trace.propagation.tracecontext import TraceContextTextMapPropagator

_LOGGER = logging.getLogger("contract_app.operations")
_EVENTS = {
    "http.request",
    "workflow.task",
    "outbox.delivery",
    "document.parse",
    "document.index",
}
_FIELDS = {
    "request_id",
    "trace_id",
    "method",
    "route",
    "status_code",
    "tenant_id",
    "run_id",
    "task_id",
    "event_id",
    "task_type",
    "event_type",
    "attempt",
    "outcome",
    "duration_ms",
    "queue_wait_ms",
}
_UUID_FIELDS = {"request_id", "tenant_id", "run_id", "task_id", "event_id"}
_OUTCOMES = {
    "idle",
    "queued",
    "completed",
    "retried",
    "failed",
    "waiting_human",
    "lease_lost",
    "published",
    "dead_lettered",
    "error",
}


class OperationMetrics:
    """Low-cardinality counters and duration histograms for safe operation events."""

    def __init__(self, meter: Meter) -> None:
        self._operations = meter.create_counter(
            "contract_app.operations", unit="{operation}", description="Completed operations"
        )
        self._duration = meter.create_histogram(
            "contract_app.operation.duration", unit="ms", description="Operation duration"
        )
        self._queue_wait = meter.create_histogram(
            "contract_app.queue.wait", unit="ms", description="Durable queue wait time"
        )

    def record(self, event: str, fields: dict[str, str | int | float]) -> None:
        attributes: dict[str, str] = {"operation": event}
        if event == "http.request":
            status_code = fields.get("status_code")
            attributes.update(
                {
                    "http.method": str(fields.get("method", "OTHER")),
                    "http.route": str(fields.get("route", "<unmatched>")),
                    "http.status_class": (
                        f"{int(status_code) // 100}xx"
                        if isinstance(status_code, int)
                        else "unknown"
                    ),
                }
            )
        else:
            attributes["outcome"] = str(fields.get("outcome", "unknown"))
        self._operations.add(1, attributes)
        duration = fields.get("duration_ms")
        if isinstance(duration, int | float):
            self._duration.record(float(duration), attributes)
        queue_wait = fields.get("queue_wait_ms")
        if isinstance(queue_wait, int | float):
            self._queue_wait.record(float(queue_wait), attributes)


_operation_metrics = OperationMetrics(metrics.get_meter("contract_app.operations"))
_tracer_provider: TracerProvider | None = None
_meter_provider: MeterProvider | None = None


def _safe_field(name: str, value: str | int | float) -> bool:
    if name in _UUID_FIELDS:
        try:
            return isinstance(value, str) and str(UUID(value)) == value
        except ValueError:
            return False
    if name == "trace_id":
        return isinstance(value, str) and re.fullmatch(r"[0-9a-f]{32}", value) is not None
    if name == "method":
        return value in {"GET", "POST", "PUT", "PATCH", "DELETE", "OTHER"}
    if name == "route":
        return (
            isinstance(value, str)
            and len(value) <= 160
            and (
                value == "<unmatched>" or re.fullmatch(r"/[A-Za-z0-9_/{}/.:-]*", value) is not None
            )
        )
    if name == "outcome":
        return isinstance(value, str) and value in _OUTCOMES
    if name in {"task_type", "event_type"}:
        return isinstance(value, str) and re.fullmatch(r"[a-z][a-z0-9_.]{0,79}", value) is not None
    if name == "status_code":
        return isinstance(value, int) and 100 <= value <= 599
    if name == "attempt":
        return isinstance(value, int) and value >= 0
    if name in {"duration_ms", "queue_wait_ms"}:
        return isinstance(value, int | float) and math.isfinite(value) and value >= 0
    return False


def emit_operation(event: str, **fields: str | int | float) -> None:
    """Reject unknown fields so callers cannot log a contract body by accident."""
    if (
        event not in _EVENTS
        or not fields.keys() <= _FIELDS
        or any(not _safe_field(name, value) for name, value in fields.items())
    ):
        raise ValueError("unsafe_operation_log_fields")
    _operation_metrics.record(event, fields)
    record: dict[str, Any] = {"event": event, **fields}
    _LOGGER.info(json.dumps(record, ensure_ascii=False, separators=(",", ":")))


def current_trace_id() -> str | None:
    context = trace.get_current_span().get_span_context()
    if not context.is_valid:
        return None
    return f"{context.trace_id:032x}"


def capture_traceparent() -> str | None:
    carrier: dict[str, str] = {}
    TraceContextTextMapPropagator().inject(carrier)
    return carrier.get("traceparent")


def context_from_traceparent(value: str | None) -> Context | None:
    if value is None:
        return None
    return TraceContextTextMapPropagator().extract({"traceparent": value})


def configure_console_telemetry(service_name: str) -> TracerProvider:
    """Configure synchronous console spans and periodic console metrics."""
    if not service_name or len(service_name) > 80:
        raise ValueError("invalid_telemetry_service")
    provider, _ = _configure_exporters(
        service_name,
        ConsoleSpanExporter(),
        ConsoleMetricExporter(),
        batch_spans=False,
    )
    if not _LOGGER.handlers:
        handler = logging.StreamHandler()
        handler.setFormatter(logging.Formatter("%(message)s"))
        _LOGGER.addHandler(handler)
    _LOGGER.setLevel(logging.INFO)
    _LOGGER.propagate = False
    return provider


def _otlp_protocol(signal_name: str) -> str:
    signal_variable = f"OTEL_EXPORTER_OTLP_{signal_name.upper()}_PROTOCOL"
    protocol = (
        os.environ.get(signal_variable)
        or os.environ.get("OTEL_EXPORTER_OTLP_PROTOCOL")
        or "http/protobuf"
    )
    if protocol not in {"grpc", "http/protobuf"}:
        raise ValueError("unsupported_otlp_protocol")
    return protocol


def _create_otlp_exporters() -> tuple[SpanExporter, MetricExporter]:
    traces_protocol = _otlp_protocol("traces")
    metrics_protocol = _otlp_protocol("metrics")
    try:
        if traces_protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.trace_exporter import (
                OTLPSpanExporter as GrpcSpanExporter,
            )

            span_exporter_factory: Callable[[], SpanExporter] = GrpcSpanExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import (
                OTLPSpanExporter as HttpSpanExporter,
            )

            span_exporter_factory = HttpSpanExporter

        if metrics_protocol == "grpc":
            from opentelemetry.exporter.otlp.proto.grpc.metric_exporter import (
                OTLPMetricExporter as GrpcMetricExporter,
            )

            metric_exporter_factory: Callable[[], MetricExporter] = GrpcMetricExporter
        else:
            from opentelemetry.exporter.otlp.proto.http.metric_exporter import (
                OTLPMetricExporter as HttpMetricExporter,
            )

            metric_exporter_factory = HttpMetricExporter
    except ImportError as exc:
        raise RuntimeError("otlp_exporter_extra_not_installed") from exc
    return span_exporter_factory(), metric_exporter_factory()


def _configure_exporters(
    service_name: str,
    span_exporter: SpanExporter,
    metric_exporter: MetricExporter,
    *,
    batch_spans: bool,
) -> tuple[TracerProvider, MeterProvider]:
    if not service_name or len(service_name) > 80:
        raise ValueError("invalid_telemetry_service")
    resource = Resource.create({"service.name": service_name})
    tracer_provider = TracerProvider(resource=resource)
    span_processor = (
        BatchSpanProcessor(span_exporter) if batch_spans else SimpleSpanProcessor(span_exporter)
    )
    tracer_provider.add_span_processor(span_processor)
    meter_provider = MeterProvider(
        resource=resource,
        metric_readers=[
            PeriodicExportingMetricReader(
                metric_exporter,
                export_interval_millis=10_000,
            )
        ],
    )
    trace.set_tracer_provider(tracer_provider)
    metrics.set_meter_provider(meter_provider)
    global _operation_metrics, _tracer_provider, _meter_provider
    _operation_metrics = OperationMetrics(meter_provider.get_meter("contract_app.operations"))
    _tracer_provider = tracer_provider
    _meter_provider = meter_provider
    return tracer_provider, meter_provider


def configure_otlp_telemetry(service_name: str) -> None:
    """Configure batched OTLP traces and periodic OTLP metrics using standard OTEL env vars."""
    span_exporter, metric_exporter = _create_otlp_exporters()
    _configure_exporters(service_name, span_exporter, metric_exporter, batch_spans=True)


def shutdown_telemetry() -> None:
    """Flush and close configured SDK providers during process shutdown."""
    if _tracer_provider is not None:
        _tracer_provider.shutdown()
    if _meter_provider is not None:
        _meter_provider.shutdown()


def configure_from_environment(service_name: str) -> None:
    exporter = os.environ.get("CONTRACT_OTEL_EXPORTER")
    legacy_console = os.environ.get("CONTRACT_OTEL_CONSOLE") == "1"
    if exporter is None:
        mode = "console" if legacy_console else "disabled"
    else:
        if legacy_console:
            raise ValueError("conflicting_telemetry_exporter_configuration")
        mode = exporter
    if mode == "disabled":
        return
    if mode == "console":
        configure_console_telemetry(service_name)
        return
    if mode == "otlp":
        configure_otlp_telemetry(service_name)
        return
    raise ValueError("invalid_telemetry_exporter")
