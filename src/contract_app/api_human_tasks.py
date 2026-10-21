"""Authorized M1 fact-review queue and versioned decisions."""

import os
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field, field_validator

from contract_app.api_errors import COMMON_PROBLEM_RESPONSES, problem_responses
from contract_app.api_snapshots import Principal, require_principal
from contract_app.auth_security import require_permission
from contract_app.workflow.job_store import HumanTaskConflict, HumanTaskView, JobStore

router = APIRouter(
    prefix="/api/v1",
    tags=["HumanTasks"],
    responses=COMMON_PROBLEM_RESPONSES,
    dependencies=[Depends(require_permission("tasks.use"))],
)


def get_job_store() -> JobStore:
    database_url = os.environ.get("DATABASE_URL")
    if not database_url:
        raise HTTPException(status_code=503, detail="service_unavailable")
    return JobStore(database_url)


class HumanTaskResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    task_id: UUID
    case_id: UUID
    run_id: UUID
    type: Literal["fact_review", "price_review"]
    status: Literal["open", "completed", "cancelled", "superseded"]
    title: str
    reason: str
    required_role: str
    decision: Literal["confirm_fact", "record_review", "request_material"] | None
    version: int
    created_at: datetime


class HumanTaskListResponse(BaseModel):
    items: list[HumanTaskResponse]
    next_cursor: str | None = None


class FactReviewDecisionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
    decision: Literal["confirm_fact", "record_review", "request_material"]
    reason: str = Field(min_length=1, max_length=4000)

    @field_validator("reason")
    @classmethod
    def reason_must_contain_non_whitespace(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("reason_must_not_be_blank")
        return value


def _response(task: HumanTaskView) -> HumanTaskResponse:
    return HumanTaskResponse(
        task_id=task.id,
        case_id=task.case_id,
        run_id=task.run_id,
        type=task.type,  # type: ignore[arg-type]
        status=task.status,  # type: ignore[arg-type]
        title="复核合同事实" if task.type == "fact_review" else "核对合同价款",
        reason=task.reason_code,
        required_role=task.required_role,
        decision=task.decision,  # type: ignore[arg-type]
        version=task.version,
        created_at=task.created_at,
    )


@router.get("/human-tasks", operation_id="listHumanTasks", response_model=HumanTaskListResponse)
def list_human_tasks(
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
    case_id: UUID | None = None,
    run_id: UUID | None = None,
    status: Literal["open", "completed", "cancelled", "superseded"] | None = None,
    cursor: str | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> HumanTaskListResponse:
    try:
        tasks, next_cursor = jobs.list_human_tasks(
            principal.tenant_id,
            principal.actor_id,
            case_id=case_id,
            run_id=run_id,
            status=status,
            cursor=cursor,
            limit=limit,
        )
    except ValueError as exc:
        raise HTTPException(status_code=422, detail="invalid_cursor_or_limit") from exc
    return HumanTaskListResponse(items=[_response(task) for task in tasks], next_cursor=next_cursor)


@router.post(
    "/human-tasks/{task_id}/decisions",
    operation_id="decideHumanTask",
    response_model=HumanTaskResponse,
    responses=problem_responses(404, 409),
)
def decide_human_task(
    task_id: UUID,
    body: FactReviewDecisionRequest,
    principal: Annotated[Principal, Depends(require_principal)],
    jobs: Annotated[JobStore, Depends(get_job_store)],
    idempotency_key: Annotated[
        str,
        Header(
            alias="Idempotency-Key",
            min_length=8,
            max_length=128,
            pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$",
        ),
    ],
) -> HumanTaskResponse:
    try:
        if body.decision == "confirm_fact":
            task = jobs.resolve_fact_review(
                principal.tenant_id,
                task_id,
                principal.actor_id,
                body.version,
                body.reason,
                idempotency_key,
            )
        else:
            task = jobs.resolve_price_review(
                principal.tenant_id,
                task_id,
                principal.actor_id,
                body.version,
                body.decision,
                body.reason,
                idempotency_key,
            )
    except HumanTaskConflict as exc:
        raise HTTPException(status_code=409, detail="human_task_conflict") from exc
    if task is None:
        raise HTTPException(status_code=404, detail="human_task_not_found")
    return _response(task)
