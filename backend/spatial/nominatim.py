# @file: backend/spatial/nominatim.py
# @exports: NominatimClient, RequestLimiter, learned_id
# @depends_on: backend/core/config.py, backend/spatial/normalization.py
# @purpose: Opt-in 1Hz fallback, optional cross-process Redis gate, and deterministic write-through cache.
import asyncio
import hashlib
import json
import time

import httpx
from sqlalchemy import text

from backend.schemas.spatial import GeocodedLocation
from backend.spatial.normalization import normalize


class RequestLimiter:
    def __init__(self, redis=None, clock=time.monotonic, sleep=asyncio.sleep):
        self.redis, self.clock, self.sleep = redis, clock, sleep
        self.lock, self.last_started = asyncio.Lock(), float('-inf')

    async def wait(self):
        delay = 1.0 - (self.clock() - self.last_started)
        if delay > 0:
            await self.sleep(delay)
        if self.redis is not None:
            while not await self.redis.set('ts:rate:nominatim', '1', nx=True, px=1100):
                await self.sleep(0.1)
        self.last_started = self.clock()


def learned_id(key):
    return -(1_000_000_000_000 + int.from_bytes(hashlib.blake2b(key.encode(), digest_size=6).digest(), 'big'))


class NominatimClient:
    def __init__(self, settings, client=None, limiter=None):
        self.settings, self.client = settings, client
        self.limiter = limiter or RequestLimiter()

    async def lookup(self, session, landmark, key):
        if not self.settings.nominatim_enabled:
            return None
        owned = self.client is None
        client = self.client or httpx.AsyncClient(timeout=10)
        query = ', '.join(filter(None, [landmark.primary_landmark, landmark.corridor_name,
                                       landmark.locality, landmark.city, landmark.state, 'India']))
        try:
            async with self.limiter.lock:
                await self.limiter.wait()
                response = await client.get(self.settings.nominatim_base_url.rstrip('/')+'/search',
                    params={'q': query, 'format': 'jsonv2', 'countrycodes': 'in', 'limit': 1, 'addressdetails': 1},
                    headers={'User-Agent': self.settings.nominatim_user_agent}, timeout=10)
                response.raise_for_status()
            rows = response.json()
            if not rows:
                return None
            row = rows[0]
            address = row.get('address', {})
            if address.get('country_code', '').lower() != 'in':
                return None
            latitude, longitude = float(row['lat']), float(row['lon'])
            if not 6 <= latitude <= 38 or not 68 <= longitude <= 98:
                return None
            for expected, actual in [(landmark.state, address.get('state')),
                                     (landmark.city, address.get('city') or address.get('town'))]:
                if expected and actual and normalize(expected) != normalize(actual):
                    return None
            identifier = learned_id(key)
            name = row.get('display_name') or landmark.primary_landmark
            await session.execute(text('''
                INSERT INTO gazetteer_places (geoname_id,name,ascii_name,alternate_names,
                    search_aliases,admin1_state,admin2_district,city_name,cache_key,geom)
                VALUES (:id,:name,:ascii,:aliases,:aliases_text,:state,:district,:city,:key,
                    ST_SetSRID(ST_MakePoint(:lon,:lat),4326))
                ON CONFLICT (cache_key) DO UPDATE SET geom=EXCLUDED.geom, name=EXCLUDED.name,
                    ascii_name=EXCLUDED.ascii_name, search_aliases=EXCLUDED.search_aliases
            '''), {'id': identifier, 'name': name, 'ascii': key, 'aliases': [landmark.primary_landmark or key],
                   'aliases_text': normalize(landmark.primary_landmark), 'state': address.get('state') or landmark.state,
                   'district': address.get('state_district'), 'city': landmark.city or address.get('city') or address.get('town'),
                   'key': key, 'lon': longitude, 'lat': latitude})
            return GeocodedLocation(longitude=longitude, latitude=latitude, method='nominatim',
                                    confidence=0.7, geoname_id=identifier, display_name=name)
        except (KeyError, ValueError, TypeError, json.JSONDecodeError):
            return None
        finally:
            if owned:
                await client.aclose()
