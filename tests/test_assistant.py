# @file: tests/test_assistant.py
# @exports: Local/cloud slots, immutable templates, typed binds and injection regression tests
# @depends_on: backend/assistant, tests/phase6_support.py
# @purpose: Verify every intent, cloud revalidation and zero user-generated executable SQL.
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest
from pydantic import SecretStr, ValidationError

from backend.assistant.intent_parser import local_slots, parse_query
from backend.assistant.sql_templates import TEMPLATES, execute_template
from backend.core.config import Settings
from backend.schemas.assistant import QueryParameters, SlotFillingQuery
from backend.schemas.enums import QueryIntent
from tests.phase6_support import NOW, golden


@pytest.mark.asyncio
@pytest.mark.parametrize('query,intent', [
    ('Show severe landslides on NH-7 in the last 6 hours', 'congestion_hotspots'),
    ('Which corridors in Dehradun have the highest clearance time?', 'average_clearance'),
    ('Are there active accidents near Clock Tower?', 'nearby_incidents'),
    ('Show history on NH 7 in the past 2 days', 'corridor_history')])
async def test_local_examples_and_all_intents(query, intent):
    geocoder = MagicMock(geocode=AsyncMock(return_value=golden().location))
    parsed = await local_slots(query, AsyncMock(), Settings(), geocoder)
    assert parsed.intent == intent and parsed.sql_template_id == intent
    if 'severe landslides' in query:
        assert parsed.slots.incident_type == 'landslide' and parsed.slots.severity == 'severe'
        assert parsed.slots.corridor_name == 'NH-7' and parsed.slots.time_window_hours == 6
    if 'Dehradun' in query:
        assert parsed.slots.city == 'Dehradun'


@pytest.mark.asyncio
@pytest.mark.parametrize('query', ["NH-7'; DROP TABLE raw_reports; --", 'Show top 101 incidents',
                                   'Show incidents last 0 hours', 'History without a corridor'])
async def test_local_malicious_or_invalid_slots_rejected(query):
    with pytest.raises(ValueError):
        await local_slots(query, AsyncMock(), Settings())


@pytest.mark.asyncio
@pytest.mark.parametrize('valid', [True, False])
async def test_cloud_json_is_strictly_revalidated_or_local_fallback(valid):
    target = {'intent': 'congestion_hotspots', 'slots': {'city': 'Dehradun'}}
    if not valid:
        target['sql'] = 'DROP TABLE raw_reports'
    import json
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json={
        'choices': [{'message': {'content': json.dumps(target)}}]}))
    settings = Settings(assistant_cloud_slots_enabled=True, llm_api_key=SecretStr('test'), llm_model_id='test')
    async with httpx.AsyncClient(transport=transport) as client:
        parsed, source = await parse_query('Show incidents in Dehradun', AsyncMock(), settings, client)
    assert parsed.slots.city == 'Dehradun'
    assert source == ('cloud_json_validated' if valid else 'deterministic_local')


@pytest.mark.asyncio
@pytest.mark.parametrize('intent', list(QueryIntent))
async def test_only_allowlisted_readonly_bound_templates(intent):
    injection = "NH-7'; DROP TABLE raw_reports; --"
    parameters = QueryParameters(corridor_name=injection, latitude=30, longitude=77, limit=3)
    query = SlotFillingQuery(intent=intent, slots=parameters)
    result = MagicMock()
    result.mappings.return_value.all.return_value = [{'row': i} for i in range(8)]
    session = AsyncMock()
    @asynccontextmanager
    async def begin():
        yield
    session.begin = begin
    session.execute.return_value = result
    rows = await execute_template(session, query, False, NOW)
    calls = session.execute.call_args_list
    assert str(calls[0].args[0]) == 'SET TRANSACTION READ ONLY'
    assert calls[-1].args[0] is TEMPLATES[intent]
    assert calls[-1].args[1]['corridor'] == injection and injection not in str(calls[-1].args[0])
    assert len(rows) == 3 and calls[-1].args[1]['limit'] == 3


def test_template_mismatch_unknown_intent_and_extra_sql_rejected():
    with pytest.raises(ValidationError):
        SlotFillingQuery(intent='average_clearance', sql_template_id='congestion_hotspots', slots={})
    with pytest.raises(ValidationError):
        SlotFillingQuery.model_validate({'intent': 'raw_sql', 'slots': {}})
    with pytest.raises(ValidationError):
        SlotFillingQuery.model_validate({'intent': 'average_clearance', 'slots': {}, 'sql': 'SELECT 1'})
    with pytest.raises(TypeError):
        TEMPLATES['anything'] = 'SELECT 1'
