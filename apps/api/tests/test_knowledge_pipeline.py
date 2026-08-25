from uuid import uuid4

from fastapi.testclient import TestClient

from app.core.config import Settings
from app.knowledge.index import chunk_text, normalize_text
from app.main import create_app

IDEMPOTENCY = {"Idempotency-Key": "knowledge-pipeline-001"}


def client() -> TestClient:
    return TestClient(create_app(Settings(environment="test", storage_backend="memory")))


def tenant_headers(tenant_id: str, **extra: str) -> dict[str, str]:
    return {"X-Dev-Tenant-Id": tenant_id, **extra}


def create_knowledge_base(api: TestClient, *, tenant_id: str | None = None, name: str = "知识库") -> dict:
    headers = {"Idempotency-Key": f"knowledge-base-{uuid4()}"}
    if tenant_id:
        headers.update(tenant_headers(tenant_id))
    response = api.post(
        "/api/v1/knowledge-bases",
        headers=headers,
        json={
            "name": name,
            "ownerDepartmentId": "dept-product",
            "securityLevel": "internal",
            "embeddingModelCode": "offline-lexical-v1",
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


def upload(
    api: TestClient,
    knowledge_base_id: str,
    *,
    filename: str,
    content: bytes,
    mime_type: str,
    headers: dict[str, str] | None = None,
) -> dict:
    request_headers = {
        "Idempotency-Key": f"knowledge-upload-{uuid4()}",
        **(headers or {}),
    }
    response = api.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/documents",
        headers=request_headers,
        files={"file": (filename, content, mime_type)},
    )
    assert response.status_code == 202, response.text
    return response.json()


def search(
    api: TestClient,
    knowledge_base_id: str,
    query: str,
    *,
    top_k: int = 10,
    headers: dict[str, str] | None = None,
) -> dict:
    response = api.post(
        f"/api/v1/knowledge-bases/{knowledge_base_id}/search",
        headers=headers,
        json={"query": query, "topK": top_k},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_txt_index_returns_real_contract_citations_sorted_and_limited() -> None:
    with client() as api:
        knowledge_base = create_knowledge_base(api)
        first = upload(
            api,
            knowledge_base["id"],
            filename="产品周报.txt",
            content="苹果 苹果 项目按期完成。\n风险：供应链延期。".encode(),
            mime_type="text/plain",
        )
        second = upload(
            api,
            knowledge_base["id"],
            filename="补充说明.md",
            content="# 补充\n苹果 项目进入验收。".encode(),
            mime_type="text/markdown",
        )
        assert first["status"] == second["status"] == "indexed"
        assert first["indexedAt"] and second["indexedAt"]

        result = search(api, knowledge_base["id"], "苹果 苹果", top_k=1)
        assert len(result["citations"]) == 1
        citation = result["citations"][0]
        assert set(citation) == {
            "knowledgeDocumentId",
            "chunkId",
            "assetId",
            "quote",
            "score",
        }
        assert citation["knowledgeDocumentId"] == first["id"]
        assert citation["assetId"] == first["assetId"]
        assert "苹果 苹果" in citation["quote"]
        assert 0 < citation["score"] <= 1


def test_html_extractor_ignores_script_and_empty_search_stays_empty() -> None:
    with client() as api:
        knowledge_base = create_knowledge_base(api)
        uploaded = upload(
            api,
            knowledge_base["id"],
            filename="制度.html",
            content=(
                "<html><style>.secret{}</style><script>隐藏口令</script>"
                "<h1>休假制度</h1><p>员工每年享有带薪年假。</p></html>"
            ).encode(),
            mime_type="text/html",
        )
        assert uploaded["status"] == "indexed"
        visible = search(api, knowledge_base["id"], "带薪年假")
        assert visible["citations"]
        assert "带薪年假" in visible["citations"][0]["quote"]
        assert search(api, knowledge_base["id"], "隐藏口令")["citations"] == []
        assert search(api, knowledge_base["id"], "不存在的火星词汇")["citations"] == []


def test_search_filters_are_applied_before_scoring() -> None:
    with client() as api:
        knowledge_base = create_knowledge_base(api)
        first = upload(
            api,
            knowledge_base["id"],
            filename="产品路线.md",
            content="凤凰项目按期交付".encode(),
            mime_type="text/markdown",
        )
        second = upload(
            api,
            knowledge_base["id"],
            filename="财务说明.txt",
            content="凤凰项目预算已审批".encode(),
            mime_type="text/plain",
        )

        filtered = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
            json={
                "query": "凤凰项目",
                "topK": 10,
                "filters": {
                    "documentIds": [second["id"]],
                    "assetIds": [second["assetId"]],
                    "dataScopes": ["department"],
                    "securityLevels": ["internal"],
                    "titleContains": "财务",
                    "documentStatus": "indexed",
                },
            },
        )
        assert filtered.status_code == 200, filtered.text
        assert [item["knowledgeDocumentId"] for item in filtered.json()["citations"]] == [second["id"]]

        excluded = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
            json={
                "query": "凤凰项目",
                "topK": 10,
                "filters": {"documentIds": [first["id"]], "titleContains": "财务"},
            },
        )
        assert excluded.status_code == 200
        assert excluded.json()["citations"] == []


def test_index_is_isolated_by_knowledge_base_and_tenant() -> None:
    tenant_a = str(uuid4())
    tenant_b = str(uuid4())
    with client() as api:
        kb_a1 = create_knowledge_base(api, tenant_id=tenant_a, name="A1")
        kb_a2 = create_knowledge_base(api, tenant_id=tenant_a, name="A2")
        upload(
            api,
            kb_a1["id"],
            filename="隔离.txt",
            content="租户甲专属凤凰计划".encode(),
            mime_type="text/plain",
            headers=tenant_headers(tenant_a),
        )

        assert search(api, kb_a2["id"], "凤凰计划", headers=tenant_headers(tenant_a))["citations"] == []
        cross_tenant = api.post(
            f"/api/v1/knowledge-bases/{kb_a1['id']}/search",
            headers=tenant_headers(tenant_b),
            json={"query": "凤凰计划", "topK": 10},
        )
        assert cross_tenant.status_code == 404

        kb_b = create_knowledge_base(api, tenant_id=tenant_b, name="B")
        assert search(api, kb_b["id"], "凤凰计划", headers=tenant_headers(tenant_b))["citations"] == []


def test_pdf_and_binary_are_stored_but_never_fake_indexed() -> None:
    with client() as api:
        knowledge_base = create_knowledge_base(api)
        pdf = upload(
            api,
            knowledge_base["id"],
            filename="扫描件.pdf",
            content=b"%PDF-1.7\x00binary demo",
            mime_type="application/pdf",
        )
        binary = upload(
            api,
            knowledge_base["id"],
            filename="archive.bin",
            content=b"\x00\x01\x02not searchable",
            mime_type="application/octet-stream",
        )
        disguised_binary = upload(
            api,
            knowledge_base["id"],
            filename="fake.txt",
            content=b"readable-prefix\x00binary-tail",
            mime_type="text/plain",
        )
        assert pdf["status"] == binary["status"] == disguised_binary["status"] == "failed"
        assert "indexedAt" not in pdf and "indexedAt" not in binary
        assert search(api, knowledge_base["id"], "searchable PDF binary")["citations"] == []


def test_upload_and_search_permissions_are_separate() -> None:
    with client() as api:
        knowledge_base = create_knowledge_base(api)
        denied_upload = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers={**IDEMPOTENCY, "X-Dev-Permissions": "knowledge.read"},
            files={"file": ("denied.txt", b"denied", "text/plain")},
        )
        assert denied_upload.status_code == 403

        denied_search = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/search",
            headers={"X-Dev-Permissions": "knowledge.write"},
            json={"query": "denied", "topK": 5},
        )
        assert denied_search.status_code == 403


def test_document_scope_and_clearance_are_enforced_before_retrieval() -> None:
    tenant_id = str(uuid4())
    owner_id = str(uuid4())
    colleague_id = str(uuid4())
    with client() as api:
        owner_headers = {
            "X-Dev-Tenant-Id": tenant_id,
            "X-Dev-User-Id": owner_id,
            "X-Dev-Department-Ids": "dept-product",
            "X-Dev-Security-Clearance": "department_sensitive",
        }
        knowledge_base = create_knowledge_base(api, tenant_id=tenant_id)
        response = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers={
                "Idempotency-Key": f"acl-upload-{uuid4()}",
                **owner_headers,
            },
            data={
                "dataScope": "department",
                "securityLevel": "department_sensitive",
            },
            files={
                "file": (
                    "sensitive.txt",
                    "部门敏感凤凰预算".encode(),
                    "text/plain",
                )
            },
        )
        assert response.status_code == 202, response.text
        assert response.json()["status"] == "indexed"

        base_colleague = {
            "X-Dev-Tenant-Id": tenant_id,
            "X-Dev-User-Id": colleague_id,
        }
        assert search(
            api,
            knowledge_base["id"],
            "凤凰预算",
            headers=base_colleague,
        )["citations"] == []
        assert search(
            api,
            knowledge_base["id"],
            "凤凰预算",
            headers={**base_colleague, "X-Dev-Department-Ids": "dept-product"},
        )["citations"] == []

        allowed = search(
            api,
            knowledge_base["id"],
            "凤凰预算",
            headers={
                **base_colleague,
                "X-Dev-Department-Ids": "dept-product",
                "X-Dev-Security-Clearance": "department_sensitive",
            },
        )
        assert allowed["citations"]


def test_project_scope_requires_membership_and_protects_asset_download() -> None:
    tenant_id = str(uuid4())
    owner_id = str(uuid4())
    colleague_id = str(uuid4())
    with client() as api:
        knowledge_base = create_knowledge_base(api, tenant_id=tenant_id)
        base_upload_headers = {
            "Idempotency-Key": f"project-upload-{uuid4()}",
            "X-Dev-Tenant-Id": tenant_id,
            "X-Dev-User-Id": owner_id,
            "X-Dev-Security-Clearance": "department_sensitive",
        }
        missing_project = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers=base_upload_headers,
            data={"dataScope": "project", "securityLevel": "department_sensitive"},
            files={"file": ("project.txt", "阿波罗预算".encode(), "text/plain")},
        )
        assert missing_project.status_code == 422

        forged_project = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers={
                **base_upload_headers,
                "Idempotency-Key": f"project-upload-{uuid4()}",
            },
            data={
                "dataScope": "project",
                "securityLevel": "department_sensitive",
                "projectId": "apollo",
            },
            files={"file": ("forged-project.txt", "伪造项目归属".encode(), "text/plain")},
        )
        assert forged_project.status_code == 403

        uploaded = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers={**base_upload_headers, "Idempotency-Key": f"project-upload-{uuid4()}", "X-Dev-Project-Ids": "apollo"},
            data={
                "dataScope": "project",
                "securityLevel": "department_sensitive",
                "projectId": "apollo",
            },
            files={"file": ("project.txt", "阿波罗预算".encode(), "text/plain")},
        )
        assert uploaded.status_code == 202, uploaded.text
        asset_id = uploaded.json()["assetId"]

        colleague = {"X-Dev-Tenant-Id": tenant_id, "X-Dev-User-Id": colleague_id}
        assert api.get(f"/api/v1/assets/{asset_id}", headers=colleague).status_code == 404
        assert search(api, knowledge_base["id"], "阿波罗预算", headers=colleague)["citations"] == []

        member = {
            **colleague,
            "X-Dev-Project-Ids": "apollo",
            "X-Dev-Security-Clearance": "department_sensitive",
        }
        assert api.get(f"/api/v1/assets/{asset_id}", headers=member).status_code == 200
        assert search(api, knowledge_base["id"], "阿波罗预算", headers=member)["citations"]


def test_upload_idempotency_fingerprint_includes_project_id() -> None:
    tenant_id = str(uuid4())
    idempotency_key = f"project-fingerprint-{uuid4()}"
    with client() as api:
        knowledge_base = create_knowledge_base(api, tenant_id=tenant_id)
        headers = {
            "Idempotency-Key": idempotency_key,
            "X-Dev-Tenant-Id": tenant_id,
            "X-Dev-Project-Ids": "apollo,zeus",
        }
        request = {
            "dataScope": "project",
            "securityLevel": "internal",
            "projectId": "apollo",
        }
        first = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers=headers,
            data=request,
            files={"file": ("project.txt", "项目归属不可漂移".encode(), "text/plain")},
        )
        assert first.status_code == 202, first.text

        second = api.post(
            f"/api/v1/knowledge-bases/{knowledge_base['id']}/documents",
            headers=headers,
            data={**request, "projectId": "zeus"},
            files={"file": ("project.txt", "项目归属不可漂移".encode(), "text/plain")},
        )
        assert second.status_code == 409, second.text


def test_normalization_and_chunking_are_deterministic_and_validated() -> None:
    source = "  Alpha\r\n\r\n\r\nBeta   Gamma  "
    assert normalize_text(source) == "Alpha\n\nBeta Gamma"
    assert chunk_text(source, size=10, overlap=2) == chunk_text(source, size=10, overlap=2)
    assert all(chunk.strip() == chunk for chunk in chunk_text(source, size=10, overlap=2))

    try:
        chunk_text("invalid", size=10, overlap=10)
    except ValueError as exc:
        assert "overlap" in str(exc)
    else:
        raise AssertionError("invalid chunk overlap was accepted")
