# @file: backend/ingestion/tomtom_client.py
# @exports: TomTomIncidentClient
# @depends_on: backend/ingestion/source.py
# @purpose: Live North India incident geometries and descriptions with stable source identities.
import asyncio
import json
from urllib.parse import quote

from backend.ingestion.source import HTTPSource, observation

CATEGORIES = {1: 'accident', 2: 'fog', 3: 'dangerous conditions', 4: 'rain', 5: 'ice',
              6: 'traffic jam', 7: 'lane closed', 8: 'road closed', 9: 'roadwork', 10: 'wind', 11: 'flood'}


class TomTomIncidentClient(HTTPSource):
    async def rows(self, client, since, until):
        async def fetch_box(box):
            return await self.request(client, 'https://api.tomtom.com/traffic/services/5/incidentDetails',
            params={'key': self.settings.tomtom_api_key.get_secret_value(), 'bbox': box,
                'language': 'en-GB', 'timeValidityFilter': 'present',
                'fields': '{incidents{type,geometry{type,coordinates},properties{id,iconCategory,events{description},startTime,endTime,from,to,length,delay,roadNumbers}}}'})
        # Corridor-sized boxes stay below TomTom's 10,000 km² request limit.
        responses = await asyncio.gather(*(fetch_box(box) for box in (
            '77.8,29.8,78.4,30.6', '78.4,30.0,79.0,30.8', '79.0,30.3,79.7,31.0')))
        result, seen = [], set()
        for incident in [item for response in responses for item in response.json()['incidents']]:
            props = incident['properties']
            if props['id'] in seen:
                continue
            seen.add(props['id'])
            title = 'Road '+CATEGORIES.get(props.get('iconCategory'), 'traffic disruption')+' '+str(props.get('from') or '')
            description = '; '.join(event.get('description', '') for event in props.get('events', []))
            content = title+' '+description+'\n[structured_traffic '+json.dumps(incident)+']'
            result.append(observation(title, 'https://www.tomtom.com/traffic/incident/'+quote(str(props['id']), safe=''),
                                      content, None, until))  # Ongoing incident observed now; startTime remains in payload.
        return result
