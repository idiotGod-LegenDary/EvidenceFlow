"""Admin routes for tool and skill introspection."""

import hmac

from fastapi import APIRouter, Depends, Request

from app.api.dependencies import get_container
from app.core.exceptions import InvalidRequestError, UnauthorizedError
from app.retrieval.knowledge import KnowledgeBuildReport
from app.runtime.routing import operation_specs
from app.services.container import AppContainer

router = APIRouter(prefix="/admin", tags=["admin"])


async def require_admin(request: Request, container: AppContainer = Depends(get_container)) -> None:
    token = container.settings.admin_api_token.get_secret_value()
    if not token:
        if container.settings.app_env == "production":
            raise UnauthorizedError("admin API is disabled")
        return
    provided = request.headers.get("x-admin-token", "")
    if not hmac.compare_digest(provided, token):
        raise UnauthorizedError("invalid admin token")


@router.get("/tools", dependencies=[Depends(require_admin)])
async def list_tools(container: AppContainer = Depends(get_container)) -> dict:
    names = container.chat_service.registry.names()
    return {"tools": names, "count": len(names)}


@router.get("/skills", dependencies=[Depends(require_admin)])
async def list_skills() -> dict:
    return {"skills": operation_specs()}


@router.get(
    "/knowledge", dependencies=[Depends(require_admin)], response_model=KnowledgeBuildReport
)
async def knowledge_status(
    container: AppContainer = Depends(get_container),
) -> KnowledgeBuildReport:
    if container.knowledge is None or container.knowledge.report is None:
        raise InvalidRequestError("knowledge runtime is unavailable")
    return container.knowledge.report


@router.post(
    "/knowledge/reload", dependencies=[Depends(require_admin)], response_model=KnowledgeBuildReport
)
async def reload_knowledge(
    force: bool = False,
    allow_empty: bool = False,
    container: AppContainer = Depends(get_container),
) -> KnowledgeBuildReport:
    """Reconcile server-side files; never accept file paths or content from callers."""
    if container.knowledge is None:
        raise InvalidRequestError("knowledge runtime is unavailable")
    return await container.knowledge.reload(force=force, allow_empty=allow_empty)
