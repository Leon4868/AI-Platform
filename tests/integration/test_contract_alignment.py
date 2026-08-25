import sys
from pathlib import Path


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
CONTRACT_PATH = REPOSITORY_ROOT / "packages" / "contracts" / "openapi.yaml"
sys.path.insert(0, str(REPOSITORY_ROOT))

from scripts.check_contract_alignment import (  # noqa: E402
    check_alignment,
    generated_fastapi_schema,
    load_contract_operations,
    load_fastapi_operations,
)


def test_checked_in_contract_has_operations_and_success_responses() -> None:
    operations = load_contract_operations()

    assert operations
    assert all(statuses for statuses in operations.values())


def test_fastapi_paths_methods_and_success_statuses_match_contract() -> None:
    problems = check_alignment()

    assert not problems, "OpenAPI contract drift:\n- " + "\n- ".join(problems)


def test_fastapi_schema_exposes_operations() -> None:
    # Separate smoke assertion gives a direct diagnostic if app construction or
    # OpenAPI generation changes independently of the checked-in contract.
    assert load_fastapi_operations(generated_fastapi_schema())


def _contract_path_block(path: str) -> str:
    contract = CONTRACT_PATH.read_text(encoding="utf-8")
    marker = f"  {path}:\n"
    start = contract.index(marker) + len(marker)
    next_path = contract.find("\n  /", start)
    return contract[start:] if next_path == -1 else contract[start:next_path]


def test_project_id_is_document_upload_metadata_not_knowledge_base_metadata() -> None:
    create_block = _contract_path_block("/v1/knowledge-bases")
    upload_block = _contract_path_block("/v1/knowledge-bases/{knowledgeBaseId}/documents")
    generated_schema = generated_fastapi_schema()
    generated = generated_schema["paths"]

    assert "projectId:" not in create_block
    assert "projectId: { type: string, minLength: 1, maxLength: 128 }" in upload_block
    create_properties = generated_schema["components"]["schemas"]["KnowledgeBaseCreate"]["properties"]
    upload_properties = generated["/api/v1/knowledge-bases/{knowledgeBaseId}/documents"]["post"][
        "requestBody"
    ]["content"]["multipart/form-data"]["schema"]["properties"]
    assert "projectId" not in create_properties
    assert upload_properties["projectId"]["maxLength"] == 128


def test_problem_details_responses_use_problem_json_media_type() -> None:
    contract = CONTRACT_PATH.read_text(encoding="utf-8")

    assert contract.count("$ref: ./schemas/common.schema.json#/$defs/problemDetails") == 2
    assert contract.count("application/problem+json:") == 2
