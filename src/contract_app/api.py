"""HTTP entry point for the contract audit application."""

from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.openapi.utils import get_openapi
from opentelemetry import trace
from opentelemetry.trace import Tracer
from pydantic import BaseModel
from starlette.responses import JSONResponse, Response

from contract_app.api_access_admin import router as access_admin_router
from contract_app.api_auth import router as auth_router
from contract_app.api_cases import router as case_router
from contract_app.api_documents import router as document_router
from contract_app.api_entities import router as entity_router
from contract_app.api_errors import ProblemDetailsResponse
from contract_app.api_human_tasks import router as human_task_router
from contract_app.api_knowledge import router as knowledge_router
from contract_app.api_review_samples import router as review_samples_router
from contract_app.api_runs import router as run_router
from contract_app.api_snapshots import router as snapshot_router
from contract_app.contextassembly.snapshot import PreviewConflict, PreviewDenied
from contract_app.documents.authorization import (
    DocumentAccessAuditDenied,
    DocumentSelectionConflict,
    DocumentSelectionDenied,
    DocumentSelectionInvalid,
)
from contract_app.documents.catalog import DocumentCatalogDenied
from contract_app.documents.ingestion import (
    DocumentUploadConflict,
    DocumentUploadDenied,
    DocumentUploadInvalid,
)
from contract_app.documents.parser import ParseError
from contract_app.documents.removal import (
    DocumentRemovalConflict,
    DocumentRemovalDenied,
    DocumentRemovalInvalid,
)
from contract_app.retrieval.pageindex_adapter import RetrievalDenied
from contract_app.telemetry import (
    configure_from_environment,
    current_trace_id,
    emit_operation,
    shutdown_telemetry,
)
from contract_app.workflow.job_store import BoundRunConflict, BoundRunDenied


class HealthStatus(BaseModel):
    status: str


@asynccontextmanager
async def _telemetry_lifespan(_app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        shutdown_telemetry()


def create_app(tracer: Tracer | None = None, *, owns_telemetry_providers: bool = False) -> FastAPI:
    app = FastAPI(
        title="Contract Audit API",
        description="Internal MVP contract audit API; external source integration is deferred.",
        version="0.3.0",
        openapi_version="3.1.0",
        servers=[{"url": "/"}],
        lifespan=_telemetry_lifespan if owns_telemetry_providers else None,
    )
    request_tracer = tracer or trace.get_tracer("contract_app.api")

    def problem(request: Request, status: int, code: str, title: str) -> JSONResponse:
        return JSONResponse(
            status_code=status,
            media_type="application/problem+json",
            content={
                "type": "about:blank",
                "title": title,
                "status": status,
                "code": code,
                "trace_id": current_trace_id() or request.state.request_id,
            },
        )

    @app.exception_handler(HTTPException)
    async def http_error(request: Request, exc: HTTPException) -> JSONResponse:
        known = {
            401: ("unauthorized", "Authentication required"),
            403: ("forbidden", "Access denied"),
            404: ("not_found", "Not found"),
            409: ("conflict", "Request conflicts with current state"),
            413: ("payload_too_large", "Payload too large"),
            422: ("validation_error", "Invalid request"),
            503: ("service_unavailable", "Service unavailable"),
        }
        code, title = known.get(exc.status_code, ("internal_error", "Request failed"))
        if exc.status_code == 403 and exc.detail == "run_source_access_changed":
            code, title = "run_source_access_changed", "Run source authorization changed"
        if exc.status_code == 503 and exc.detail == "review_execution_disabled":
            code, title = "review_execution_disabled", "Review execution is disabled"
        return problem(request, exc.status_code, code, title)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request: Request, _exc: RequestValidationError) -> JSONResponse:
        return problem(request, 422, "validation_error", "Invalid request")

    @app.exception_handler(PreviewDenied)
    @app.exception_handler(RetrievalDenied)
    @app.exception_handler(DocumentUploadDenied)
    @app.exception_handler(DocumentSelectionDenied)
    @app.exception_handler(DocumentAccessAuditDenied)
    @app.exception_handler(BoundRunDenied)
    @app.exception_handler(DocumentCatalogDenied)
    @app.exception_handler(DocumentRemovalDenied)
    async def authorization_error(request: Request, _exc: Exception) -> JSONResponse:
        return problem(request, 403, "forbidden", "Access denied")

    @app.exception_handler(PreviewConflict)
    @app.exception_handler(DocumentUploadConflict)
    @app.exception_handler(DocumentSelectionConflict)
    @app.exception_handler(DocumentRemovalConflict)
    @app.exception_handler(BoundRunConflict)
    async def preview_conflict(request: Request, _exc: Exception) -> JSONResponse:
        return problem(request, 409, "conflict", "Request conflicts with current state")

    @app.exception_handler(ParseError)
    @app.exception_handler(DocumentUploadInvalid)
    @app.exception_handler(DocumentSelectionInvalid)
    @app.exception_handler(DocumentRemovalInvalid)
    async def parse_error(request: Request, _exc: Exception) -> JSONResponse:
        return problem(request, 422, "validation_error", "Document cannot be parsed")

    @app.middleware("http")
    async def record_request(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = str(uuid4())
        request.state.request_id = request_id
        method = (
            request.method
            if request.method in {"GET", "POST", "PUT", "PATCH", "DELETE"}
            else "OTHER"
        )
        started = perf_counter()
        with request_tracer.start_as_current_span(
            "http.request", record_exception=False, set_status_on_exception=False
        ) as span:
            span.set_attribute("request.id", request_id)
            span.set_attribute("http.request.method", method)
            status_code = 500
            try:
                response = await call_next(request)
                status_code = response.status_code
                response.headers["X-Request-ID"] = request_id
                return response
            finally:
                route_object = request.scope.get("route")
                route = getattr(route_object, "path", "<unmatched>")
                span.set_attribute("http.route", route)
                span.set_attribute("http.response.status_code", status_code)
                trace_id = current_trace_id()
                fields: dict[str, str | int | float] = {
                    "request_id": request_id,
                    "method": method,
                    "route": route,
                    "status_code": status_code,
                    "duration_ms": round((perf_counter() - started) * 1000, 3),
                }
                if trace_id is not None:
                    fields["trace_id"] = trace_id
                emit_operation("http.request", **fields)

    @app.get("/health/live", response_model=HealthStatus, tags=["Operations"])
    def live() -> HealthStatus:
        return HealthStatus(status="ok")

    app.include_router(auth_router)
    app.include_router(access_admin_router)
    app.include_router(entity_router)
    app.include_router(case_router)
    app.include_router(snapshot_router)
    app.include_router(document_router)
    app.include_router(run_router)
    app.include_router(knowledge_router)
    app.include_router(review_samples_router)
    app.include_router(human_task_router)

    def custom_openapi() -> dict[str, object]:
        if app.openapi_schema is not None:
            return app.openapi_schema
        schema = get_openapi(
            title=app.title,
            version=app.version,
            description=app.description,
            routes=app.routes,
            servers=app.servers,
        )
        schemas = schema.setdefault("components", {}).setdefault("schemas", {})
        schemas["ProblemDetailsResponse"] = ProblemDetailsResponse.model_json_schema()
        app.openapi_schema = schema
        return schema

    app.openapi = custom_openapi  # type: ignore[method-assign]

    return app


configure_from_environment("contract-audit-api")
app = create_app(owns_telemetry_providers=True)
