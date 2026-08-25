from app.assets.schemas import Asset
from app.core.tables import (
    AssetRecord,
    DocumentGenerationJobRecord,
    KnowledgeBaseRecord,
    WorkflowDefinitionRecord,
)
from app.documents.schemas import DocumentGenerationJob
from app.knowledge.schemas import KnowledgeBase
from app.workflows.schemas import WorkflowDefinition


def workflow_to_record(entity: WorkflowDefinition) -> WorkflowDefinitionRecord:
    record = WorkflowDefinitionRecord(id=entity.id, tenant_id=entity.tenant_id)
    apply_workflow(record, entity)
    return record


def apply_workflow(record: WorkflowDefinitionRecord, entity: WorkflowDefinition) -> None:
    record.owner_id = entity.owner_id
    record.name = entity.name
    record.description = entity.description
    record.graph = entity.graph.model_dump(mode="json")
    record.status = entity.status.value
    record.revision = entity.revision
    record.created_at = entity.created_at
    record.updated_at = entity.updated_at


def workflow_from_record(record: WorkflowDefinitionRecord) -> WorkflowDefinition:
    return WorkflowDefinition(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_id=record.owner_id,
        name=record.name,
        description=record.description,
        graph=record.graph,
        status=record.status,
        revision=record.revision,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def knowledge_base_to_record(entity: KnowledgeBase) -> KnowledgeBaseRecord:
    record = KnowledgeBaseRecord(id=entity.id, tenant_id=entity.tenant_id)
    apply_knowledge_base(record, entity)
    return record


def apply_knowledge_base(record: KnowledgeBaseRecord, entity: KnowledgeBase) -> None:
    record.owner_id = entity.owner_id
    record.name = entity.name
    record.description = entity.description
    record.owner_department_id = entity.owner_department_id
    record.security_level = entity.security_level.value
    record.embedding_model_code = entity.embedding_model_code
    record.created_at = entity.created_at
    record.updated_at = entity.updated_at


def knowledge_base_from_record(record: KnowledgeBaseRecord) -> KnowledgeBase:
    return KnowledgeBase(
        id=record.id,
        tenant_id=record.tenant_id,
        owner_id=record.owner_id,
        name=record.name,
        description=record.description,
        owner_department_id=record.owner_department_id,
        security_level=record.security_level,
        embedding_model_code=record.embedding_model_code,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def document_to_record(entity: DocumentGenerationJob) -> DocumentGenerationJobRecord:
    record = DocumentGenerationJobRecord(id=entity.id, tenant_id=entity.tenant_id)
    apply_document(record, entity)
    return record


def apply_document(record: DocumentGenerationJobRecord, entity: DocumentGenerationJob) -> None:
    record.requested_by = entity.requested_by
    record.title = entity.title
    record.template_asset_id = entity.template_asset_id
    record.workflow_definition_id = entity.workflow_definition_id
    record.knowledge_base_ids = list(entity.knowledge_base_ids)
    record.logical_model_code = entity.logical_model_code
    record.instructions = entity.instructions
    record.sources = [source.model_dump(mode="json", exclude_none=True) for source in entity.sources]
    record.output_format = entity.output_format.value
    record.owner_department_id = entity.owner_department_id
    record.data_scope = entity.data_scope.value
    record.security_level = entity.security_level.value
    record.status = entity.status.value
    record.draft_asset_id = entity.draft_asset_id
    record.workflow_run_id = entity.workflow_run_id
    record.trace_id = entity.trace_id
    record.citations = [citation.model_dump(mode="json", exclude_none=True) for citation in entity.citations]
    record.error = None if entity.error is None else entity.error.model_dump(mode="json")
    record.finished_at = entity.finished_at
    record.created_at = entity.created_at
    record.updated_at = entity.updated_at


def document_from_record(record: DocumentGenerationJobRecord) -> DocumentGenerationJob:
    return DocumentGenerationJob(
        id=record.id,
        tenant_id=record.tenant_id,
        requested_by=record.requested_by,
        title=record.title,
        template_asset_id=record.template_asset_id,
        workflow_definition_id=record.workflow_definition_id,
        knowledge_base_ids=list(record.knowledge_base_ids),
        logical_model_code=record.logical_model_code,
        instructions=record.instructions,
        sources=record.sources,
        output_format=record.output_format,
        owner_department_id=record.owner_department_id,
        data_scope=record.data_scope,
        security_level=record.security_level,
        status=record.status,
        draft_asset_id=record.draft_asset_id,
        workflow_run_id=record.workflow_run_id,
        trace_id=record.trace_id,
        citations=record.citations,
        error=record.error,
        finished_at=record.finished_at,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )


def asset_to_record(entity: Asset) -> AssetRecord:
    record = AssetRecord(id=entity.id, tenant_id=entity.tenant_id)
    apply_asset(record, entity)
    return record


def apply_asset(record: AssetRecord, entity: Asset) -> None:
    record.type = entity.type.value
    record.name = entity.name
    record.description = entity.description
    record.version = entity.version
    record.status = entity.status.value
    record.mime_type = entity.mime_type
    record.storage_uri = entity.storage_uri
    record.content_hash = entity.content_hash
    record.creator_id = entity.creator_id
    record.owner_department_id = entity.owner_department_id
    record.project_id = entity.project_id
    record.data_scope = entity.data_scope.value
    record.security_level = entity.security_level.value
    record.lineage = [item.model_dump(mode="json") for item in entity.lineage]
    record.workflow_run_id = entity.workflow_run_id
    record.trace_id = entity.trace_id
    record.created_at = entity.created_at
    record.updated_at = entity.updated_at


def asset_from_record(record: AssetRecord) -> Asset:
    return Asset(
        id=record.id,
        tenant_id=record.tenant_id,
        type=record.type,
        name=record.name,
        description=record.description,
        version=record.version,
        status=record.status,
        mime_type=record.mime_type,
        storage_uri=record.storage_uri,
        content_hash=record.content_hash,
        creator_id=record.creator_id,
        owner_department_id=record.owner_department_id,
        project_id=record.project_id,
        data_scope=record.data_scope,
        security_level=record.security_level,
        lineage=record.lineage,
        workflow_run_id=record.workflow_run_id,
        trace_id=record.trace_id,
        created_at=record.created_at,
        updated_at=record.updated_at,
    )
