from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware

from app.assets.router import router as assets_router
from app.audit.router import router as audit_router
from app.container import build_container
from app.core.config import Settings, get_settings
from app.core.errors import DomainError
from app.documents.router import router as documents_router
from app.knowledge.router import router as knowledge_router
from app.workflow_runs.router import router as workflow_runs_router
from app.workflows.router import router as workflows_router


@asynccontextmanager
async def _lifespan(app: FastAPI) -> AsyncIterator[None]:
    try:
        yield
    finally:
        # In-flight runs are background tasks; without this they are torn down
        # mid-node and never emit a terminal event to their subscribers.
        await app.state.container.workflow_run_service.aclose()
        await app.state.container.document_task_coordinator.aclose()
        if app.state.container.database is not None:
            await app.state.container.database.close()


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    app = FastAPI(title=settings.app_name, version="0.1.0", lifespan=_lifespan)
    app.state.container = build_container(settings)
    if origins := settings.cors_origins():
        app.add_middleware(
            CORSMiddleware,
            allow_origins=origins,
            allow_credentials=True,
            allow_methods=["GET", "POST", "OPTIONS"],
            allow_headers=[
                "Accept",
                "Authorization",
                "Content-Type",
                "Idempotency-Key",
                "Last-Event-ID",
                "X-Request-Id",
            ],
        )

    @app.middleware("http")
    async def request_id_middleware(request: Request, call_next):
        request_id = request.headers.get("X-Request-Id", str(uuid4()))[:100]
        request.state.request_id = request_id
        response = await call_next(request)
        response.headers["X-Request-Id"] = request_id
        return response

    @app.exception_handler(DomainError)
    async def domain_error_handler(request: Request, exc: DomainError) -> JSONResponse:
        body = {
            "type": f"urn:enterprise-ai:error:{exc.error_code}",
            "title": exc.title,
            "status": exc.status_code,
            "detail": exc.detail,
            "instance": str(request.url.path),
            "request_id": getattr(request.state, "request_id", None),
        }
        if exc.errors:
            body["errors"] = exc.errors
        return JSONResponse(body, status_code=exc.status_code, media_type="application/problem+json")

    @app.exception_handler(RequestValidationError)
    async def request_validation_error_handler(request: Request, exc: RequestValidationError) -> JSONResponse:
        return JSONResponse(
            {
                "type": "urn:enterprise-ai:error:request_validation",
                "title": "Request validation failed",
                "status": 422,
                "detail": "One or more request fields are invalid",
                "instance": str(request.url.path),
                "request_id": getattr(request.state, "request_id", None),
                "errors": jsonable_encoder(exc.errors()),
            },
            status_code=422,
            media_type="application/problem+json",
        )

    @app.get("/health/live", tags=["health"])
    async def liveness() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"], response_model=None)
    async def readiness() -> dict[str, str] | JSONResponse:
        database = app.state.container.database
        try:
            if database is not None:
                await database.ping()
            await app.state.container.object_storage.ping()
        except Exception:
            return JSONResponse(
                {
                    "status": "unready",
                    "repository": app.state.container.repository_backend,
                    "storage": settings.storage_backend,
                },
                status_code=503,
            )
        return {
            "status": "ready",
            "repository": app.state.container.repository_backend,
            "storage": settings.storage_backend,
        }

    app.include_router(workflows_router, prefix=settings.api_prefix)
    app.include_router(workflow_runs_router, prefix=settings.api_prefix)
    app.include_router(knowledge_router, prefix=settings.api_prefix)
    app.include_router(documents_router, prefix=settings.api_prefix)
    app.include_router(assets_router, prefix=settings.api_prefix)
    app.include_router(audit_router, prefix=settings.api_prefix)
    return app


app = create_app()
