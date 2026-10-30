# @file: backend/spatial/gazetteer_loader.py
# @exports: read_records, import_gazetteer, main
# @depends_on: backend/core/database.py, data/regional_gazetteer.csv
# @purpose: Bounded GeoNames 19-column TSV/simplified CSV upserts and optional admin-code decoding.
import argparse
import asyncio
import csv
import json
from pathlib import Path

from sqlalchemy import text

from backend.core.config import ROOT, get_settings
from backend.core.database import create_database

UPSERT = text('''
    INSERT INTO gazetteer_places (geoname_id,name,ascii_name,alternate_names,feature_class,
        feature_code,admin1_state,admin2_district,population,city_name,search_aliases,geom)
    VALUES (:id,:name,:ascii,:aliases,:class,:code,:state,:district,:population,:city,:alias_text,
        ST_SetSRID(ST_MakePoint(:lon,:lat),4326))
    ON CONFLICT (geoname_id) DO UPDATE SET name=EXCLUDED.name, ascii_name=EXCLUDED.ascii_name,
        alternate_names=EXCLUDED.alternate_names, feature_class=EXCLUDED.feature_class,
        feature_code=EXCLUDED.feature_code, admin1_state=EXCLUDED.admin1_state,
        admin2_district=EXCLUDED.admin2_district, population=EXCLUDED.population,
        city_name=EXCLUDED.city_name, search_aliases=EXCLUDED.search_aliases, geom=EXCLUDED.geom
''')


def admin_map(path):
    if not path:
        return {}
    with Path(path).open(encoding='utf-8-sig') as handle:
        return {row[0]: row[1] for row in csv.reader(handle, delimiter='\t') if len(row) >= 2}


def read_records(path, states=None, districts=None):
    states, districts = states or {}, districts or {}
    with Path(path).open(encoding='utf-8-sig', newline='') as handle:
        first = handle.readline()
        handle.seek(0)
        standard = first.count('\t') >= 18
        reader = csv.reader(handle, delimiter='\t') if standard else csv.DictReader(handle)
        for row in reader:
            try:
                if standard:
                    if len(row) != 19 or row[8] != 'IN':
                        raise ValueError('Expected an Indian GeoNames row with 19 columns')
                    record = {'id': int(row[0]), 'name': row[1], 'ascii': row[2] or row[1],
                        'aliases': row[3].split(',') if row[3] else [], 'lat': float(row[4]),
                        'lon': float(row[5]), 'class': row[6], 'code': row[7],
                        'state': states.get(f'IN.{row[10]}', row[10]),
                        'district': districts.get(f'IN.{row[10]}.{row[11]}', row[11]),
                        'population': int(row[14] or 0), 'city': row[1] if row[6] == 'P' else None}
                else:
                    if row.get('country_code', 'IN') != 'IN':
                        raise ValueError('Only Indian records are supported')
                    record = {'id': int(row['geoname_id']), 'name': row['name'],
                        'ascii': row.get('ascii_name') or row['name'],
                        'aliases': (row.get('alternate_names') or '').replace('|', ',').split(','),
                        'lat': float(row['latitude']), 'lon': float(row['longitude']),
                        'class': row.get('feature_class') or 'S', 'code': row.get('feature_code') or 'LMRK',
                        'state': row.get('admin1_state') or row.get('state'),
                        'district': row.get('admin2_district') or row.get('district'),
                        'population': int(row.get('population') or 0), 'city': row.get('city_name') or None}
                if (not record['name'] or not -90 <= record['lat'] <= 90 or not -180 <= record['lon'] <= 180
                        or record['population'] < 0 or not -(2**63) <= record['id'] < 2**63):
                    raise ValueError('Invalid identifier, population or coordinates')
                record['alias_text'] = ' '.join(record['aliases'])
                yield record
            except (ValueError, KeyError, TypeError, IndexError):
                yield None


async def import_gazetteer(session, path, batch_size=1000, states=None, districts=None):
    if not 1 <= batch_size <= 10000:
        raise ValueError('batch_size must be 1..10000')
    count, rejected, batch = 0, 0, []
    for record in read_records(path, states, districts):
        if record is None:
            rejected += 1
            continue
        batch.append(record)
        if len(batch) >= batch_size:
            async with session.begin():
                await session.execute(UPSERT, batch)
            count += len(batch)
            batch = []
    if batch:
        async with session.begin():
            await session.execute(UPSERT, batch)
        count += len(batch)
    return {'upserted_count': count, 'rejected_count': rejected}


async def run(args):
    engine, factory = create_database(get_settings())
    try:
        paths = ([ROOT/'data/regional_gazetteer.csv'] if args.seed_regional else []) + ([args.file] if args.file else [])
        if not paths:
            raise ValueError('Provide --file or --seed-regional')
        async with factory() as session:
            for path in paths:
                result = await import_gazetteer(session, path, args.batch_size,
                    admin_map(args.admin1_file), admin_map(args.admin2_file))
                print(json.dumps({'file': str(path), **result}))
            async with session.begin():
                await session.execute(text('ANALYZE gazetteer_places'))
    finally:
        await engine.dispose()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--file', type=Path)
    parser.add_argument('--seed-regional', action='store_true')
    parser.add_argument('--batch-size', type=int, default=1000)
    parser.add_argument('--admin1-file', type=Path)
    parser.add_argument('--admin2-file', type=Path)
    asyncio.run(run(parser.parse_args()))


if __name__ == '__main__':
    main()
