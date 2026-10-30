# @file: backend/spatial/demo.py
# @exports: seed_demo, main
# @depends_on: data/spatial_demo_reports.jsonl, backend/core/database.py
# @purpose: Explicitly seed three idempotent precise-landmark fixtures for an offline spatial viva.
import asyncio
import json
from datetime import timedelta
from uuid import NAMESPACE_URL, uuid5

from sqlalchemy import text

from backend.core.config import ROOT, get_settings
from backend.core.database import create_database
from backend.core.redis_bus import RedisStreamBus, StreamNames
from backend.schemas.extraction import ExtractedIncidentSchema, LandmarkTuple
from backend.schemas.ingestion import utc_now


async def seed_demo(factory, bus):
    rows = [json.loads(line) for line in (ROOT/'data/spatial_demo_reports.jsonl').read_text().splitlines() if line]
    anchor = utc_now().replace(minute=0, second=0, microsecond=0)-timedelta(hours=1)
    inserted, published = 0, 0
    for row in rows:
        identity = uuid5(NAMESPACE_URL, row['id'])
        attributes = ExtractedIncidentSchema(report_id=identity, incident_type='landslide', severity='severe',
            temporal_status='active', lanes_blocked=row['lanes_blocked'], summary_reason=row['title'],
            landmark_tuple=LandmarkTuple(primary_landmark='Kali Mandir Choke Point', corridor_name='NH-7',
                                         city='Dehradun', state='Uttarakhand'))
        async with factory() as session, session.begin():
            created = (await session.execute(text('''
                INSERT INTO raw_reports (report_id,source_name,source_url,title,raw_text,published_at,
                    setfit_is_relevant,setfit_confidence,temporal_status,landmark_tuple,extracted_attributes,pipeline_status)
                VALUES (:id,'fixture:spatial_demo',:url,:title,:body,:published,true,0.9,'active',
                    CAST(:landmark AS jsonb),CAST(:attributes AS jsonb),'EXTRACTED')
                ON CONFLICT (source_url) DO NOTHING RETURNING report_id
            '''), {'id': identity, 'url': f'https://fixtures.trafficsense.invalid/{row["id"]}',
                'title': row['title'], 'body': row['raw_text'], 'published': anchor+timedelta(minutes=row['minutes']),
                'landmark': attributes.landmark_tuple.model_dump_json(), 'attributes': attributes.model_dump_json()})).scalar_one_or_none()
        inserted += int(created is not None)
        _, fresh = await bus.publish_once(StreamNames.EXTRACTED_ITEMS, attributes, str(identity))
        published += int(fresh)
    return {'fixture_inserted': inserted, 'fixture_published': published}


async def run():
    settings = get_settings()
    engine, factory = create_database(settings)
    bus = RedisStreamBus.from_url(settings.redis_url.get_secret_value())
    try:
        print(json.dumps(await seed_demo(factory, bus)))
    finally:
        await bus.close()
        await engine.dispose()


def main():
    asyncio.run(run())


if __name__ == '__main__':
    main()
