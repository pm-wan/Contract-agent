"""Tenant-scoped full-text knowledge documents, rather than hand-authored checks."""

from __future__ import annotations

import json
from datetime import date
from hashlib import sha256
from typing import Annotated, Literal
from uuid import UUID, uuid4
from urllib.parse import quote

import psycopg
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from psycopg.rows import dict_row
from starlette.responses import Response

from contract_app.api_cases import get_database_url
from contract_app.auth_security import (
    Principal,
    require_any_permission,
    require_permission,
    require_principal,
    set_tenant,
)
from contract_app.retrieval.knowledge import MAX_SOURCE_BYTES, parse_source, serialize_source
from contract_app.retrieval.legal_web import legal_search_status

router = APIRouter(
    prefix="/api/v1/knowledge-documents",
    tags=["Knowledge documents"],
    dependencies=[Depends(require_any_permission("company_rules.use", "company_rules.manage"))],
)
PrincipalDep = Annotated[Principal, Depends(require_principal)]
DatabaseDep = Annotated[str, Depends(get_database_url)]
FIELDS = """id,source_key,version,kind,title,file_name,media_type,source_url,issuer,scope,
    effective_from,effective_until,status,content_sha256,warnings,created_at,published_at,
    jsonb_array_length(segments) AS passage_count"""


@router.get("")
def list_knowledge(principal: PrincipalDep, database_url: DatabaseDep) -> dict:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        rows = connection.execute(
            f"SELECT {FIELDS} FROM knowledge_documents WHERE tenant_id=%s ORDER BY created_at DESC",
            (principal.tenant_id,),
        ).fetchall()
    return {
        "items": [serialize_source(row) for row in rows],
        "online_legal_search": legal_search_status(),
    }


@router.post(
    "", status_code=201, dependencies=[Depends(require_permission("company_rules.manage"))]
)
async def upload_knowledge(
    principal: PrincipalDep,
    database_url: DatabaseDep,
    file: Annotated[UploadFile, File()],
    kind: Annotated[Literal["legal", "company"], Form()],
    title: Annotated[str, Form(min_length=1, max_length=255)],
    source_url: Annotated[str, Form(max_length=2000)] = "",
    issuer: Annotated[str, Form(max_length=255)] = "",
    scope: Annotated[str, Form(max_length=1000)] = "",
    effective_from: Annotated[date | None, Form()] = None,
    effective_until: Annotated[date | None, Form()] = None,
    source_key: Annotated[UUID | None, Form()] = None,
) -> dict:
    if not title.strip() or (
        effective_from and effective_until and effective_until < effective_from
    ):
        raise HTTPException(422, "knowledge_metadata_invalid")
    if source_url and not source_url.startswith("https://"):
        raise HTTPException(422, "knowledge_source_url_requires_https")
    data = await file.read(MAX_SOURCE_BYTES + 1)
    file_name = (file.filename or "source.txt").replace("\\", "/").rsplit("/", 1)[-1][:255]
    try:
        media_type, segments, warnings = parse_source(data, file_name)
    except Exception as exc:
        raise HTTPException(422, "knowledge_parse_failed") from exc
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        key = source_key or uuid4()
        connection.execute(
            "SELECT pg_advisory_xact_lock(hashtextextended(%s,0))",
            (f"{principal.tenant_id}:{key}",),
        )
        previous = connection.execute(
            "SELECT kind,max(version) OVER () AS version FROM "
            "knowledge_documents WHERE tenant_id=%s AND source_key=%s LIMIT 1",
            (principal.tenant_id, key),
        ).fetchone()
        if source_key and (previous is None or previous["kind"] != kind):
            raise HTTPException(404, "knowledge_source_not_found")
        row = connection.execute(
            f"""INSERT INTO knowledge_documents
            (tenant_id,source_key,version,kind,title,file_name,media_type,source_url,issuer,scope,
             effective_from,effective_until,content_sha256,file_data,segments,warnings,created_by)
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s)
            RETURNING {FIELDS}""",
            (
                principal.tenant_id,
                key,
                previous["version"] + 1 if previous else 1,
                kind,
                title.strip(),
                file_name,
                media_type,
                source_url,
                issuer.strip(),
                scope.strip(),
                effective_from,
                effective_until,
                sha256(data).hexdigest(),
                data,
                json.dumps(segments, ensure_ascii=False),
                json.dumps(warnings, ensure_ascii=False),
                principal.actor_id,
            ),
        ).fetchone()
    return serialize_source(row)


@router.get("/{document_id}")
def get_knowledge(document_id: UUID, principal: PrincipalDep, database_url: DatabaseDep) -> dict:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        row = connection.execute(
            f"SELECT {FIELDS},segments FROM knowledge_documents WHERE tenant_id=%s AND id=%s",
            (principal.tenant_id, document_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "knowledge_source_not_found")
    return serialize_source(row)


@router.get("/{document_id}/file")
def download_knowledge(
    document_id: UUID, principal: PrincipalDep, database_url: DatabaseDep
) -> Response:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        row = connection.execute(
            "SELECT media_type,file_data,file_name FROM knowledge_documents WHERE tenant_id=%s AND id=%s",
            (principal.tenant_id, document_id),
        ).fetchone()
        if row is None:
            raise HTTPException(404, "knowledge_source_not_found")
    return Response(
        bytes(row["file_data"]),
        media_type=row["media_type"],
        headers={"Content-Disposition": "attachment; filename*=UTF-8''" + quote(row["file_name"]), "Cache-Control": "no-store"},
    )


@router.post(
    "/{document_id}/{action}", dependencies=[Depends(require_permission("company_rules.manage"))]
)
def change_knowledge_status(
    document_id: UUID,
    action: Literal["publish", "withdraw"],
    principal: PrincipalDep,
    database_url: DatabaseDep,
) -> dict:
    with psycopg.connect(database_url, row_factory=dict_row) as connection:
        set_tenant(connection, principal.tenant_id)
        status, old, stamp = (
            ("published", "draft", "published_at")
            if action == "publish"
            else ("withdrawn", "published", "withdrawn_at")
        )
        row = connection.execute(
            f"UPDATE knowledge_documents SET status=%s,{stamp}=now() "
            f"WHERE tenant_id=%s AND id=%s AND status=%s RETURNING {FIELDS}",
            (status, principal.tenant_id, document_id, old),
        ).fetchone()
        if row is None:
            raise HTTPException(409, "knowledge_status_changed")
    return serialize_source(row)
