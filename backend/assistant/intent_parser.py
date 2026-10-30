# @file: backend/assistant/intent_parser.py
# @exports: local_slots, parse_query
# @depends_on: backend/schemas/assistant.py, backend/scout/osrm.py, httpx
# @purpose: Deterministic offline intent/slot extraction and optional strictly revalidated cloud JSON.
import re

import httpx

from backend.schemas.assistant import QueryParameters, SlotFillingQuery
from backend.schemas.enums import IncidentType, QueryIntent, Severity
from backend.scout.osrm import resolve_endpoint
from backend.spatial.normalization import CITIES


async def local_slots(query, session, settings, geocoder=None):
    lowered = query.casefold().strip()
    if any(token in lowered for token in (';', '--', '/*', '*/')):
        raise ValueError('Query contains unsupported SQL punctuation; use plain language')
    if not lowered:
        raise ValueError('Query must contain text')
    nearby = re.search(r'\b(?:near|nearby|around)\b\s*(.+)', query, re.I)
    intent = (QueryIntent.NEARBY_INCIDENTS if nearby else
              QueryIntent.AVERAGE_CLEARANCE if re.search(r'\b(average|highest|longest|clearance time)\b', lowered) else
              QueryIntent.CORRIDOR_HISTORY if re.search(r'\b(history|historical|past incidents)\b', lowered) else
              QueryIntent.CONGESTION_HOTSPOTS)
    types = {kind.value.replace('_', ' '): kind for kind in IncidentType}
    types.update({'accidents': IncidentType.ACCIDENT, 'landslides': IncidentType.LANDSLIDE,
                  'collisions': IncidentType.COLLISION, 'roadworks': IncidentType.ROADWORK})
    kind = next((value for term, value in types.items() if re.search(rf'\b{re.escape(term)}\b', lowered)), None)
    severity = next((value for value in Severity if re.search(rf'\b{value.value}\b', lowered)), None)
    city = next((city.title() for city in CITIES if re.search(rf'\b{city}\b', lowered)), None)
    corridor = re.search(r'\b(NH|SH)\s*[-–]?\s*(\d+)\b', query, re.I)
    road = re.search(r'\b(?:on|along|for)\s+([\w -]+?(?:road|expressway|highway))\b', query, re.I)
    corridor_name = f'{corridor[1].upper()}-{corridor[2]}' if corridor else road[1].strip() if road else None
    hours = re.search(r'\b(?:last|past)\s+(\d+(?:\.\d+)?)\s*(hours?|hrs?|days?|minutes?|mins?)\b', lowered)
    window = float(hours[1])*(24 if hours[2].startswith('day') else 1/60 if hours[2].startswith('min') else 1) if hours else 24
    limit = re.search(r'\b(?:top|limit|show)\s+(\d+)\b', lowered)
    values = {'incident_type': kind, 'severity': severity, 'city': city,
              'corridor_name': corridor_name, 'time_window_hours': window, 'limit': int(limit[1]) if limit else 20}
    if nearby:
        landmark = re.split(r'\b(?:in the last|in the past|last|past|within)\b', nearby[1], flags=re.I)[0].strip(' ?.,')
        location = await resolve_endpoint(session, landmark, settings, geocoder)
        values.update(latitude=location.latitude, longitude=location.longitude)
        radius = re.search(r'\bwithin\s+(\d+(?:\.\d+)?)\s*(km|m|meters?|kilomet(?:er|re)s?)\b', lowered)
        if radius:
            values['radius_meters'] = float(radius[1])*(1000 if radius[2].startswith('k') else 1)
    return SlotFillingQuery(intent=intent, slots=QueryParameters(**values))


async def cloud_slots(query, settings, client):
    response = await client.post(settings.llm_base_url.rstrip('/')+'/chat/completions',
        headers={'Authorization': f'Bearer {settings.llm_api_key.get_secret_value()}'},
        timeout=settings.assistant_timeout_seconds, json={
            'model': settings.finetuned_llm_model_id or settings.llm_model_id,
            'temperature': 0, 'response_format': {'type': 'json_object'}, 'messages': [
                {'role': 'system', 'content': 'Return only SlotFillingQuery JSON. Never generate SQL. '
                    'Choose an allowlisted intent and matching sql_template_id, and validated slots. Schema: '
                    +str(SlotFillingQuery.model_json_schema())},
                {'role': 'user', 'content': query}]})
    response.raise_for_status()
    return SlotFillingQuery.model_validate_json(response.json()['choices'][0]['message']['content'])


async def parse_query(query, session, settings, client=None, geocoder=None):
    if settings.assistant_cloud_slots_enabled and settings.llm_api_key.get_secret_value() and (
            settings.finetuned_llm_model_id or settings.llm_model_id):
        try:
            if client:
                return await cloud_slots(query, settings, client), 'cloud_json_validated'
            async with httpx.AsyncClient() as owned:
                return await cloud_slots(query, settings, owned), 'cloud_json_validated'
        except (httpx.HTTPError, ValueError, KeyError, TypeError, IndexError):
            pass
    return await local_slots(query, session, settings, geocoder), 'deterministic_local'
