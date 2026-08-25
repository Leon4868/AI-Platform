from dataclasses import dataclass

from app.agents.repository import InMemoryAgentRepository
from app.agents.service import AgentService, InMemoryAgentAuditSink

from app.assets.schemas import Asset
from app.audit.service import AuditService
from app.core.config import Settings
from app.core.database import Database
from app.core.idempotency import IdempotencyStore, InMemoryIdempotencyStore
from app.core.repository import InMemoryRepository, Repository
from app.core.service import ReadService
from app.core.storage import (
    InMemoryObjectStorage,
    ObjectStorage,
    S3CompatibleObjectStorage,
    S3StorageOptions,
)
from app.documents.schemas import DocumentGenerationJob
from app.documents.service import (
    DeterministicMarkdownComposer,
    DocumentTaskCoordinator,
    DocumentTaskService,
    ModelGatewayMarkdownComposer,
)
from app.identity.development import DevelopmentIdentityProvider
from app.identity.provider import IdentityProvider
from app.knowledge.index import InMemoryKnowledgeIndex, KnowledgeIndex
from app.knowledge.sql_index import SQLAlchemyKnowledgeIndex
from app.knowledge.schemas import KnowledgeBase
from app.model_gateway.gateway import ModelGateway, RoutingModelGateway, UnconfiguredModelGateway
from app.model_gateway.providers import (
    AnthropicMessagesProvider,
    GeminiGenerateContentProvider,
    OpenAIResponsesProvider,
)
from app.model_gateway.routing import ModelRouteRegistry, ModelRoutingConfig
from app.model_gateway.schemas import ModelProvider
from app.model_gateway.transport import HttpxModelHttpTransport
from app.persistence.asset_repository import SQLAlchemyAssetRepository
from app.persistence.audit_service import SQLAlchemyAuditService
from app.persistence.document_repository import SQLAlchemyDocumentRepository
from app.persistence.knowledge_repository import SQLAlchemyKnowledgeBaseRepository
from app.persistence.workflow_repository import SQLAlchemyWorkflowRepository
from app.runtime.executor import InProcessGraphExecutor
from app.runtime.repository import InMemoryWorkflowRunRepository, WorkflowRunRepository
from app.runtime.sql_repository import SQLAlchemyWorkflowRunRepository
from app.runtime.service import WorkflowRunService
from app.workflows.schemas import WorkflowDefinition
from app.workflows.service import WorkflowService
from app.workflows.validator import WorkflowGraphValidator


@dataclass(slots=True)
class Container:
    repository_backend: str
    database: Database | None
    identity_provider: IdentityProvider
    object_storage: ObjectStorage
    model_gateway: ModelGateway
    audit_service: AuditService
    idempotency_store: IdempotencyStore
    agent_repository: InMemoryAgentRepository
    agent_service: AgentService
    workflow_repository: Repository[WorkflowDefinition]
    workflow_service: WorkflowService
    workflow_run_repository: WorkflowRunRepository
    workflow_run_service: WorkflowRunService
    knowledge_repository: Repository[KnowledgeBase]
    knowledge_service: ReadService[KnowledgeBase]
    knowledge_index: KnowledgeIndex
    document_repository: Repository[DocumentGenerationJob]
    document_service: ReadService[DocumentGenerationJob]
    document_task_service: DocumentTaskService
    document_task_coordinator: DocumentTaskCoordinator
    asset_repository: Repository[Asset]
    asset_service: ReadService[Asset]


def build_container(settings: Settings, *, identity_provider: IdentityProvider | None = None) -> Container:
    if identity_provider is None:
        if settings.environment == "production":
            raise RuntimeError("A production identity provider must be explicitly configured")
        identity_provider = DevelopmentIdentityProvider()

    if settings.storage_backend == "s3":
        object_storage: ObjectStorage = S3CompatibleObjectStorage(
            S3StorageOptions(
                bucket=settings.s3_bucket,
                region=settings.s3_region,
                endpoint_url=settings.s3_endpoint_url,
                access_key=settings.s3_access_key,
                secret_key=settings.s3_secret_key,
                use_path_style=settings.s3_use_path_style,
            )
        )
    else:
        object_storage = InMemoryObjectStorage()

    model_gateway = _build_model_gateway(settings)
    database: Database | None = None
    if settings.repository_backend == "postgresql":
        if not settings.database_url:
            raise RuntimeError("APP_DATABASE_URL is required when APP_REPOSITORY_BACKEND=postgresql")
        database = Database(settings.database_url)
        audit_service = SQLAlchemyAuditService(database.session_factory)
        workflow_repository: Repository[WorkflowDefinition] = SQLAlchemyWorkflowRepository(
            database.session_factory
        )
        knowledge_repository: Repository[KnowledgeBase] = SQLAlchemyKnowledgeBaseRepository(
            database.session_factory
        )
        document_repository: Repository[DocumentGenerationJob] = SQLAlchemyDocumentRepository(
            database.session_factory
        )
        asset_repository: Repository[Asset] = SQLAlchemyAssetRepository(database.session_factory)
        workflow_run_repository: WorkflowRunRepository = SQLAlchemyWorkflowRunRepository(
            database.session_factory
        )
        knowledge_index: KnowledgeIndex = SQLAlchemyKnowledgeIndex(database.session_factory)
    else:
        audit_service = AuditService()
        workflow_repository = InMemoryRepository()
        knowledge_repository = InMemoryRepository()
        document_repository = InMemoryRepository()
        asset_repository = InMemoryRepository()
        workflow_run_repository = InMemoryWorkflowRunRepository()
        knowledge_index = InMemoryKnowledgeIndex()
    workflow_run_service = WorkflowRunService(
        workflow_repository,
        workflow_run_repository,
        InProcessGraphExecutor(),
        audit_service,
    )
    if settings.document_composer == "model_gateway":
        if isinstance(model_gateway, UnconfiguredModelGateway):
            raise RuntimeError("APP_DOCUMENT_COMPOSER=model_gateway requires at least one enabled model route")
        composer = ModelGatewayMarkdownComposer(model_gateway)
    else:
        composer = DeterministicMarkdownComposer()
    document_task_service = DocumentTaskService(
        document_repository,
        asset_repository,
        object_storage,
        composer,
    )
    agent_repository = InMemoryAgentRepository()
    agent_service = AgentService(
        agent_repository,
        _UnconfiguredAgentResourceResolver(),
        InMemoryAgentAuditSink(),
    )
    return Container(
        repository_backend=settings.repository_backend,
        database=database,
        identity_provider=identity_provider,
        object_storage=object_storage,
        model_gateway=model_gateway,
        audit_service=audit_service,
        idempotency_store=InMemoryIdempotencyStore(),
        agent_repository=agent_repository,
        agent_service=agent_service,
        workflow_repository=workflow_repository,
        workflow_service=WorkflowService(workflow_repository, WorkflowGraphValidator(), audit_service),
        workflow_run_repository=workflow_run_repository,
        workflow_run_service=workflow_run_service,
        knowledge_repository=knowledge_repository,
        knowledge_service=ReadService(knowledge_repository, "knowledge_base"),
        knowledge_index=knowledge_index,
        document_repository=document_repository,
        document_service=ReadService(document_repository, "document_generation_job"),
        document_task_service=document_task_service,
        document_task_coordinator=DocumentTaskCoordinator(
            document_task_service,
            workflow_run_service,
        ),
        asset_repository=asset_repository,
        asset_service=ReadService(asset_repository, "asset"),
    )


class _UnconfiguredAgentResourceResolver:
    """Fail-closed until Prompt/Model/Knowledge/Tool registries are composed."""

    async def resolve(self, principal, reference):
        del principal, reference
        return None


def _build_model_gateway(settings: Settings) -> ModelGateway:
    config = ModelRoutingConfig.model_validate_json(settings.model_routes_json)
    enabled_routes = [route for route in config.routes if route.enabled]
    if not enabled_routes:
        return UnconfiguredModelGateway()

    transport = HttpxModelHttpTransport()
    providers = []
    configured_provider_names = {route.provider for route in enabled_routes}
    timeout = settings.model_request_timeout_seconds
    if ModelProvider.OPENAI in configured_provider_names and settings.openai_api_key is not None:
        providers.append(OpenAIResponsesProvider(
            settings.openai_api_key.get_secret_value(),
            transport,
            base_url=settings.openai_base_url,
            timeout_seconds=timeout,
        ))
    if ModelProvider.ANTHROPIC in configured_provider_names and settings.anthropic_api_key is not None:
        providers.append(AnthropicMessagesProvider(
            settings.anthropic_api_key.get_secret_value(),
            transport,
            base_url=settings.anthropic_base_url,
            timeout_seconds=timeout,
        ))
    if ModelProvider.GEMINI in configured_provider_names and settings.gemini_api_key is not None:
        providers.append(GeminiGenerateContentProvider(
            settings.gemini_api_key.get_secret_value(),
            transport,
            base_url=settings.gemini_base_url,
            timeout_seconds=timeout,
        ))
    return RoutingModelGateway(ModelRouteRegistry(enabled_routes), providers)
