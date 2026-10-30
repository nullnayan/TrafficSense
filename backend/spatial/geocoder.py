# @file: backend/spatial/geocoder.py
# @exports: Geocoder, candidate_score
# @depends_on: backend/spatial/{normalization,nominatim}.py
# @purpose: Indexed trigram candidates, bounded fuzzy confidence, measured SQL timing, then cached fallback.
from time import perf_counter

from rapidfuzz.fuzz import WRatio, token_set_ratio
from sqlalchemy import text

from backend.core.config import get_settings
from backend.schemas.spatial import GeocodedLocation
from backend.spatial.geoapify import GeoapifyClient
from backend.spatial.nominatim import NominatimClient, RequestLimiter
from backend.spatial.normalization import city_names, contextualize, normalize, query_candidates, state_names

CANDIDATES = text('''
    SELECT DISTINCT ON (g.geoname_id) g.geoname_id,g.name,g.ascii_name,g.alternate_names,g.cache_key,
        ST_X(g.geom) AS longitude,ST_Y(g.geom) AS latitude
    FROM unnest(CAST(:queries AS text[])) AS q(term)
    CROSS JOIN LATERAL (
        WITH matches AS MATERIALIZED (
            SELECT * FROM gazetteer_places WHERE ascii_name % q.term OR search_aliases % q.term
        ) SELECT * FROM matches
        WHERE (:state='' OR lower(admin1_state)=ANY(CAST(:states AS text[])))
          AND (:city='' OR lower(city_name)=ANY(CAST(:cities AS text[])) OR lower(admin2_district)=ANY(CAST(:cities AS text[]))
               OR lower(ascii_name) LIKE :city_pattern OR lower(search_aliases) LIKE :city_pattern)
        ORDER BY GREATEST(similarity(ascii_name,q.term),similarity(COALESCE(search_aliases,''),q.term)) DESC,
            geoname_id LIMIT 40
    ) g ORDER BY g.geoname_id
''')


def candidate_score(row, queries, key):
    if row['cache_key'] == key:
        return 1.0
    names = [row['ascii_name'], row['name'], *(row['alternate_names'] or [])]
    return min(1.0, max((0.65*WRatio(normalize(name), query) +
                        0.35*token_set_ratio(normalize(name), query))/100 *
                       (0.7 + 0.3*len(set(normalize(name).split()) & set(query.split()))/len(query.split()))
                       for name in names if name for query in queries))


class Geocoder:
    def __init__(self, settings=None, client=None, limiter=None, redis=None):
        self.settings = settings or get_settings()
        self.fallback = NominatimClient(self.settings, client, limiter or RequestLimiter(redis))
        self.tier_two = GeoapifyClient(self.settings, client)
        self.last_total_ms = 0.0

    async def geocode(self, session, landmark):
        started = perf_counter()
        landmark = contextualize(landmark)
        queries = query_candidates(landmark)
        if not queries or not (landmark.primary_landmark or landmark.locality):
            self.last_total_ms = (perf_counter()-started)*1000
            return None
        key = normalize(' '.join(filter(None, [landmark.primary_landmark, landmark.corridor_name,
                                               landmark.locality, landmark.city, landmark.state])))
        query_started = perf_counter()
        rows = (await session.execute(CANDIDATES, {'queries': queries, 'key': key,
            'state': normalize(landmark.state), 'states': state_names(landmark.state),
            'city': normalize(landmark.city), 'cities': city_names(landmark.city),
            'city_pattern': '%'+normalize(landmark.city)+'%'})).mappings().all()
        query_ms = (perf_counter()-query_started)*1000
        ranked = sorted([(candidate_score(row, queries, key), row) for row in rows],
                        key=lambda pair: (-pair[0], pair[1]['geoname_id']))
        result = None
        if ranked:
            score, best = ranked[0]
            if len(ranked) > 1 and not (landmark.city or landmark.state):
                second = ranked[1]
                far = abs(best['latitude']-second[1]['latitude']) + abs(best['longitude']-second[1]['longitude']) > 0.02
                if far and score-second[0] < 0.1:
                    score = min(score, 0.64)
            if score >= self.settings.geocode_min_confidence:
                result = GeocodedLocation(longitude=best['longitude'], latitude=best['latitude'],
                    method='gazetteer', confidence=score, geoname_id=best['geoname_id'],
                    display_name=best['name'], query_latency_ms=query_ms)
        if result is None:
            result = await self.tier_two.lookup(session, landmark, key)
            if result is None:
                result = await self.fallback.lookup(session, landmark, key)
            if result:
                result.query_latency_ms = query_ms
        self.last_total_ms = (perf_counter()-started)*1000
        return result
