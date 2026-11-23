from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import get_container
from app.schemas.case import CaseCommand, CaseDetail, CaseStatus, CaseSummary
from app.schemas.chat import ChatRequest, ChatResponse
from app.services.container import AppContainer

router = APIRouter(prefix="/cases", tags=["cases"])


@router.get("", response_model=list[CaseSummary])
async def list_cases(
    user_id: str = Query(min_length=1, max_length=64),
    status: CaseStatus | None = None,
    container: AppContainer = Depends(get_container),
) -> list[CaseSummary]:
    return await container.chat_service.cases.list_cases(user_id, status)


@router.get("/{case_id}", response_model=CaseDetail)
async def case_detail(
    case_id: str,
    user_id: str = Query(min_length=1, max_length=64),
    container: AppContainer = Depends(get_container),
) -> CaseDetail:
    return await container.chat_service.cases.detail(case_id, user_id)


@router.post("/{case_id}/confirm", response_model=ChatResponse)
async def confirm_case(
    case_id: str,
    payload: CaseCommand,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> ChatResponse:
    detail = await container.chat_service.cases.detail(case_id, payload.user_id)
    return await container.chat_service.chat(
        ChatRequest(
            message="确认执行",
            user_id=payload.user_id,
            session_id=detail.session_id,
            confirm_action_id=detail.action.action_id,
        ),
        trace_id=request.state.trace_id,
    )


@router.post("/{case_id}/dismiss", response_model=CaseDetail)
async def dismiss_case(
    case_id: str,
    payload: CaseCommand,
    request: Request,
    container: AppContainer = Depends(get_container),
) -> CaseDetail:
    return await container.chat_service.cases.dismiss(
        case_id, payload.user_id, request.state.trace_id
    )
