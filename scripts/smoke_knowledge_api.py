"""Local integration smoke check; removes only the source rows it creates."""

import os
from io import BytesIO
from uuid import UUID, uuid4

import psycopg
from docx import Document
from fastapi.testclient import TestClient

from contract_app.api import app
from contract_app.auth_security import Principal, require_principal, set_tenant
from contract_app.retrieval.knowledge import capture_sources


def main():
    tenant = UUID(os.environ["CONTRACT_REVIEW_TENANT_ID"])
    with psycopg.connect(os.environ["DATABASE_URL"]) as connection:
        set_tenant(connection, tenant)
        actor = connection.execute(
            "SELECT actor_id FROM app_users WHERE tenant_id=%s LIMIT 1", (tenant,)
        ).fetchone()[0]
    principal = Principal(
        tenant,
        actor,
        permissions=frozenset({"cases.use", "company_rules.use", "company_rules.manage"}),
    )
    app.dependency_overrides[require_principal] = lambda: principal
    client = TestClient(app)
    ids = []
    prefix = "smoke-" + str(uuid4())
    try:
        response = client.post(
            "/api/v1/knowledge-documents",
            data={"kind": "company", "title": prefix, "effective_from": "2026-01-01"},
            files={"file": ("policy.txt", "账期不得超过60天。".encode(), "text/plain")},
        )
        assert response.status_code == 201, response.text
        first = response.json()
        ids.append(first["id"])
        assert (
            client.get(f"/api/v1/knowledge-documents/{first['id']}").json()["segments"][0]["text"]
            == "账期不得超过60天。"
        )
        assert client.post(f"/api/v1/knowledge-documents/{first['id']}/publish").status_code == 200
        assert any(
            source["id"] == first["id"]
            for source in capture_sources(os.environ["DATABASE_URL"], tenant)["sources"]
        )
        document = Document()
        document.add_paragraph("销售折扣超过15%时需要审批。")
        stream = BytesIO()
        document.save(stream)
        response = client.post(
            "/api/v1/knowledge-documents",
            data={"kind": "company", "title": prefix, "source_key": first["source_key"]},
            files={"file": ("policy.docx", stream.getvalue())},
        )
        assert response.status_code == 201, response.text
        second = response.json()
        ids.append(second["id"])
        assert second["version"] == 2
        assert (
            client.get(f"/api/v1/knowledge-documents/{second['id']}/file").content
            == stream.getvalue()
        )
        assert client.post(f"/api/v1/knowledge-documents/{first['id']}/withdraw").status_code == 200
        assert not any(
            source["id"] == first["id"]
            for source in capture_sources(os.environ["DATABASE_URL"], tenant)["sources"]
        )
        assert client.post(f"/api/v1/knowledge-documents/{first['id']}/publish").status_code == 409
        app.dependency_overrides[require_principal] = lambda: Principal(
            uuid4(), actor, permissions=frozenset({"company_rules.use"})
        )
        assert client.get(f"/api/v1/knowledge-documents/{second['id']}").status_code == 404
        app.dependency_overrides[require_principal] = lambda: Principal(
            tenant, actor, permissions=frozenset({"company_rules.use"})
        )
        assert client.post(f"/api/v1/knowledge-documents/{second['id']}/publish").status_code == 403
        print(
            "Knowledge API: upload, readback, DOCX, download, versions, publish, withdraw, "
            "tenant isolation and permissions passed"
        )
    finally:
        app.dependency_overrides.clear()
        # The dev stack's administrator account is used only to clean this script's fixture rows.
        with psycopg.connect(
            os.environ["DATABASE_URL"].replace("contract_runtime:", "contract_admin:")
        ) as connection:
            for identifier in ids:
                connection.execute(
                    "DELETE FROM knowledge_documents WHERE tenant_id=%s AND id=%s AND title=%s",
                    (tenant, UUID(identifier), prefix),
                )


if __name__ == "__main__":
    main()
