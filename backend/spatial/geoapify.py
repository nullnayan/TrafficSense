# @file: backend/spatial/geoapify.py
# @exports: GeoapifyClient
# @depends_on: backend/spatial/{nominatim,normalization}.py, sqlalchemy, httpx
# @purpose: Bounded India-only Tier-2 geocoding with confidence/context checks and local write-through.
import asyncio

import httpx
from sqlalchemy import text

from backend.schemas.spatial import GeocodedLocation
from backend.spatial.nominatim import learned_id
from backend.spatial.normalization import normalize


class GeoapifyClient:
    def __init__(self, settings, client=None):
        self.settings, self.client = settings, client

    async def lookup(self, session, landmark, key):
        token = self.settings.geoapify_api_key.get_secret_value().strip()
        if not token or self.settings.ingestion_source_mode == 'fixtures':
            return None
        if self.client is None:
            async with httpx.AsyncClient(timeout=6) as client:
                return await GeoapifyClient(self.settings, client).lookup(session, landmark, key)
        try:
            response = await asyncio.wait_for(self.client.get('https://api.geoapify.com/v1/geocode/search',
                params={'text': ', '.join(filter(None, [landmark.primary_landmark, landmark.corridor_name,
                    landmark.locality, landmark.city, landmark.state, 'India'])),
                    'filter': 'countrycode:in', 'apiKey': token, 'limit': 1}, timeout=6), 6)
            response.raise_for_status()
            row = response.json()['features'][0]['properties']
            lon, lat, confidence = float(row['lon']), float(row['lat']), float(row['rank']['confidence'])
            if row.get('country_code') != 'in' or not 68 <= lon <= 98 or not 6 <= lat <= 38:
                return None
            if not self.settings.geocode_min_confidence <= confidence <= 1:
                return None
            for expected, actual in [(landmark.state, row.get('state')),
                                     (landmark.city, row.get('city') or row.get('town'))]:
                if expected and (not actual or normalize(expected) != normalize(actual)):
                    return None
            result = GeocodedLocation(longitude=lon, latitude=lat, method='geoapify', confidence=confidence,
                                      geoname_id=learned_id(key), display_name=row.get('formatted') or key)
        except (httpx.HTTPError, TimeoutError, KeyError, IndexError, ValueError, TypeError):
            return None
        # Database failures remain visible to the spatial worker, which owns transaction retries.
        await session.execute(text('''
            INSERT INTO gazetteer_places (geoname_id,name,ascii_name,alternate_names,search_aliases,
                admin1_state,city_name,cache_key,geom)
            VALUES (:id,:name,:key,:aliases,:key,:state,:city,:key,ST_SetSRID(ST_MakePoint(:lon,:lat),4326))
            ON CONFLICT (cache_key) DO UPDATE SET geom=EXCLUDED.geom,name=EXCLUDED.name,
                ascii_name=EXCLUDED.ascii_name,search_aliases=EXCLUDED.search_aliases
        '''), {'id': result.geoname_id, 'name': result.display_name, 'key': key, 'aliases': [key],
               'state': row.get('state') or landmark.state, 'city': row.get('city') or landmark.city,
               'lon': lon, 'lat': lat})
        return result
