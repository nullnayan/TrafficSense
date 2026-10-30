# @file: backend/ingestion/gov_hazard_client.py
# @exports: GovHazardClient
# @depends_on: backend/ingestion/source.py, defusedxml
# @purpose: SACHET CAP/RSS, regional NASA/USGS/GDACS context and configured open-data resources.
import hashlib
import json
from datetime import datetime, timezone

from defusedxml import ElementTree

from backend.ingestion.source import HTTPSource, observation


def regional(coordinates):
    return len(coordinates) >= 2 and 68 <= coordinates[0] <= 98 and 6 <= coordinates[1] <= 38


class GovHazardClient(HTTPSource):
    async def rows(self, client, since, until):
        kind = self.options['kind']
        if kind in ('sachet', 'gdacs'):
            url = 'https://sachet.ndma.gov.in/CapFeed' if kind == 'sachet' else 'https://www.gdacs.org/xml/rss.xml'
            response = await self.request(client, url)
            root = ElementTree.fromstring(response.text)
            if root.tag.rsplit('}', 1)[-1] == 'alert':
                fields = {node.tag.rsplit('}', 1)[-1]: node.text for node in root.iter() if node.text}
                return [observation(fields.get('headline') or fields.get('event', 'Official hazard alert'),
                    fields.get('web') or url+'?identifier='+fields.get('identifier', ''),
                    fields.get('description', ''), fields.get('sent'), until)]
            if kind == 'gdacs':
                for item in list(root):
                    for entry in list(item):
                        if entry.tag == 'item':
                            country = next((n.text for n in entry if n.tag.endswith('country')), '')
                            if country and 'India' not in country:
                                item.remove(entry)
                return ElementTree.tostring(root, encoding='unicode')
            return response.text
        if kind == 'eonet':
            response = await self.request(client, 'https://eonet.gsfc.nasa.gov/api/v3/events', params={
                'status': 'open', 'category': 'landslides,floods,severeStorms',
                'start': since.strftime('%Y-%m-%d'), 'end': until.strftime('%Y-%m-%d'), 'limit': 100})
            result = []
            for event in response.json()['events']:
                geometries = [g for g in event['geometry'] if g.get('type') == 'Point' and regional(g['coordinates'])]
                if geometries:
                    geometry = max(geometries, key=lambda g: g['date'])
                    result.append(observation(event['title'], event['link'],
                        event.get('description') or event['title']+' [regional_hazard_context '+json.dumps(geometry)+']',
                        geometry['date'], until))
            return result
        if kind == 'usgs':
            response = await self.request(client, 'https://earthquake.usgs.gov/fdsnws/event/1/query', params={
                'format': 'geojson', 'starttime': since.isoformat(), 'endtime': until.isoformat(),
                'minlatitude': 28, 'maxlatitude': 32.5, 'minlongitude': 77, 'maxlongitude': 81.5})
            return [observation(row['properties']['title'], row['properties']['url'],
                'Seismic context only; road damage is unconfirmed. '+json.dumps(row['geometry']),
                datetime.fromtimestamp(row['properties']['time']/1000, timezone.utc).isoformat(), until)
                for row in response.json()['features']]
        resource = self.options['resource']
        response = await self.request(client, 'https://api.data.gov.in/resource/'+resource, params={
            'api-key': self.settings.data_gov_in_api_key.get_secret_value(), 'format': 'json', 'limit': 100})
        # Dataset fields differ. Only explicit incident titles/text are accepted; arbitrary civic rows remain context.
        return [observation(row.get('title', ''), row.get('url') or 'https://www.data.gov.in/resource/'+resource+
            '?record='+hashlib.sha256(json.dumps(row, sort_keys=True).encode()).hexdigest(),
            row.get('description') or row.get('text'), row.get('published_at'), until)
            for row in response.json()['records'] if row.get('title') and (row.get('description') or row.get('text'))]
