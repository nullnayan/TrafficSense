# @file: backend/main.py
# @exports: app, lifespan, health, trigger_ingestion, process_nlp, stream_events, sse_frames
# @depends_on: backend/core/{config,database,redis_bus}.py
# @purpose: Native API lifecycle, local UI CORS, durable pipeline controls and live incident SSE.
import asyncio
import logging
from contextlib import asynccontextmanager

from asyncpg import PostgresError
from fastapi import FastAPI, HTTPException, Request
from fastapi.encoders import jsonable_encoder
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError
from sqlalchemy.exc import SQLAlchemyError

from backend.analytics.routes import router as analytics_router
from backend.analytics.worker import AnalyticsService
from backend.assistant.routes import router as assistant_router
from backend.assistant.service import AssistantService
from backend.core.config import get_settings
from backend.core.database import create_database, verify_postgis_connection
from backend.core.redis_bus import RedisStreamBus
from backend.ingestion.macro_sentry import CycleBusy, MacroSentryRuntime
from backend.ingestion.providers import provider_status
from backend.nlp.worker import NLPBusy, NLPService
from backend.scout.routes import router as scout_router
from backend.scout.service import ScoutService
from backend.spatial.routes import router as spatial_router
from backend.spatial.worker import SpatialService
from backend.streaming.demo_pipeline import router as demo_router
from backend.streaming.sse import router as streaming_router
from backend.streaming.sse import sse_frames, stream_events  # noqa: F401 - stable public imports

logger = logging.getLogger(__name__)


async def dependency_health(application: FastAPI) -> dict:
    async def database_status() -> dict:
        try:
            details = await asyncio.wait_for(
                verify_postgis_connection(application.state.engine), timeout=12,
            )
            ready = details['gazetteer_count'] >= 10 and details['heartbeat_timestamp'] is not None
            return {'status': 'ok' if ready else 'unseeded', **details}
        except (PostgresError, SQLAlchemyError, OSError, asyncio.TimeoutError) as exc:
            logger.warning('Database health probe failed: %s', type(exc).__name__)
            return {'status': 'unavailable', 'postgis_version': None,
                    'gazetteer_count': None, 'heartbeat_timestamp': None}

    async def redis_status() -> dict:
        try:
            ready = await asyncio.wait_for(application.state.bus.ping(), timeout=4)
            return {'status': 'ok' if ready else 'unavailable'}
        except (RedisError, OSError, asyncio.TimeoutError):
            return {'status': 'unavailable'}

    database, redis = await asyncio.gather(database_status(), redis_status())
    ready = database['status'] == 'ok' and redis['status'] == 'ok'
    runtime = getattr(application.state, 'ingestion', None)
    providers = provider_status(getattr(application.state, 'settings', get_settings()),
        getattr(runtime, 'connectors', []), getattr(runtime, 'last_summary', None))
    return {'status': 'ok' if ready else 'degraded', 'database': database, 'redis': redis,
            'active_providers': providers}


@asynccontextmanager
async def lifespan(application: FastAPI):
    settings = get_settings()
    application.state.settings = settings
    application.state.engine, application.state.session_factory = create_database(settings)
    application.state.bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    application.state.ingestion = None
    application.state.nlp = NLPService(application.state.session_factory, application.state.bus, settings)
    application.state.spatial = SpatialService(application.state.session_factory, application.state.bus, settings)
    application.state.analytics = AnalyticsService(application.state.session_factory, application.state.bus, settings)
    application.state.scout = ScoutService(application.state.session_factory, application.state.bus.client, settings)
    application.state.assistant = AssistantService(application.state.session_factory, settings)
    try:
        application.state.startup_health = await dependency_health(application)
        if application.state.startup_health['status'] != 'ok':
            logger.warning('TrafficSense started with unavailable or unseeded dependencies')
        if settings.ingestion_enabled:
            application.state.ingestion = MacroSentryRuntime(
                application.state.session_factory, application.state.bus, settings,
            )
            application.state.ingestion.start()
        yield
    finally:
        if application.state.ingestion:
            await application.state.ingestion.stop()
        await asyncio.gather(application.state.bus.close(), application.state.engine.dispose())


app = FastAPI(title='TrafficSense', version='0.7.0', lifespan=lifespan)
app.add_middleware(CORSMiddleware, allow_origins=['http://localhost:5173', 'http://127.0.0.1:5173'],
                   allow_methods=['GET', 'POST'], allow_headers=['Content-Type', 'Last-Event-ID'])
app.include_router(spatial_router)
app.include_router(analytics_router)
app.include_router(scout_router)
app.include_router(assistant_router)
app.include_router(streaming_router)
app.include_router(demo_router)


@app.post('/api/v1/nlp/process')
async def process_nlp(request: Request):
    try:
        return await request.app.state.nlp.process()
    except NLPBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception('NLP dependencies unavailable; pending entries retained')
        raise HTTPException(503, 'NLP dependencies unavailable; retry after recovery') from exc


@app.post('/api/v1/ingest/trigger')
async def trigger_ingestion(request: Request):
    runtime = request.app.state.ingestion
    if runtime is None:
        raise HTTPException(503, 'Ingestion is disabled')
    try:
        return await runtime.trigger()
    except CycleBusy as exc:
        raise HTTPException(409, str(exc)) from exc
    except Exception as exc:
        logger.exception('Manual ingestion cycle failed')
        raise HTTPException(503, 'Ingestion dependencies unavailable; cycle can be retried') from exc


@app.get('/api/v1/health')
@app.get('/health')
async def health(request: Request):
    result = await dependency_health(request.app)
    return JSONResponse(jsonable_encoder(result), status_code=200 if result['status'] == 'ok' else 503)
