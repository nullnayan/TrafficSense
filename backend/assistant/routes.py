# @file: backend/assistant/routes.py
# @exports: router, query
# @depends_on: backend/assistant/service.py, backend/schemas/assistant.py
# @purpose: Natural-language assistant endpoint with validation and read-only dependency failure handling.
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from backend.schemas.assistant import AssistantQueryRequest, ParameterizedSQLResponse

router = APIRouter(prefix='/api/v1/assistant', tags=['assistant'])


@router.post('/query', response_model=ParameterizedSQLResponse)
async def query(payload: AssistantQueryRequest, request: Request):
    try:
        return await request.app.state.assistant.query(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(503, 'Assistant database unavailable; retry after recovery') from exc
