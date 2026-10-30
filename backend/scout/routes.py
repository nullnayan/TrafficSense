# @file: backend/scout/routes.py
# @exports: router, evaluate
# @depends_on: backend/scout/service.py, backend/schemas/spatial.py
# @purpose: Validated route evaluation endpoint with safe dependency error responses.
from fastapi import APIRouter, HTTPException, Request
from sqlalchemy.exc import SQLAlchemyError

from backend.schemas.spatial import RouteScoutRequest, RouteScoutResponse

router = APIRouter(prefix='/api/v1/scout', tags=['scout'])


@router.post('/evaluate', response_model=RouteScoutResponse)
async def evaluate(payload: RouteScoutRequest, request: Request):
    try:
        return await request.app.state.scout.evaluate(payload)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    except (SQLAlchemyError, OSError) as exc:
        raise HTTPException(503, 'Scout database unavailable; retry after recovery') from exc
