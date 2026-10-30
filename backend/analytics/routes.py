# @file: backend/analytics/routes.py
# @exports: router, process_analytics
# @depends_on: backend/analytics/worker.py
# @purpose: Recoverable analytics drain and lifecycle telemetry API.
from fastapi import APIRouter, HTTPException, Request

from backend.analytics.worker import AnalyticsBusy

router = APIRouter(prefix='/api/v1/analytics')


@router.post('/process')
async def process_analytics(request: Request):
    try:
        return await request.app.state.analytics.process()
    except AnalyticsBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        raise HTTPException(503, 'Analytics dependencies unavailable; pending entries retained') from exc
