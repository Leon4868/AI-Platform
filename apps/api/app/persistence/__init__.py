"""Opt-in SQLAlchemy persistence adapters; not wired into the application yet."""

from app.persistence.asset_repository import SQLAlchemyAssetRepository
from app.persistence.document_repository import SQLAlchemyDocumentRepository
from app.persistence.knowledge_repository import SQLAlchemyKnowledgeBaseRepository
from app.persistence.workflow_repository import SQLAlchemyWorkflowRepository

__all__ = [
    "SQLAlchemyAssetRepository",
    "SQLAlchemyDocumentRepository",
    "SQLAlchemyKnowledgeBaseRepository",
    "SQLAlchemyWorkflowRepository",
]
