# @file: backend/spatial/routes.py
# @exports: router, process_spatial, active_incidents
# @depends_on: backend/spatial/{worker,records}.py
# @purpose: Spatial drain telemetry and strictly validated active Golden Record API.
from fastapi import APIRouter, HTTPException, Query, Request

from backend.schemas.spatial import GoldenRecordSchema
from backend.spatial.records import list_active
from backend.spatial.worker import SpatialBusy

router = APIRouter(prefix='/api/v1')


@router.post('/spatial/process')
async def process_spatial(request: Request):
    try:
        return await request.app.state.spatial.process()
    except SpatialBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, 'Spatial dependencies unavailable; pending entries retained') from exc


@router.get('/incidents/active', response_model=list[GoldenRecordSchema])
async def active_incidents(request: Request, limit: int = Query(default=100, ge=1, le=1000),
                           include_fixtures: bool = True):
    try:
        async with request.app.state.session_factory() as session:
            return await list_active(session, limit, include_fixtures)
    except Exception as exc:
        raise HTTPException(503, 'Incident database unavailable') from exc
