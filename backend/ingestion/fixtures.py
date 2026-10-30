# @file: backend/ingestion/fixtures.py
# @exports: fixture_payload
# @depends_on: none
# @purpose: Clearly synthetic RSS/JSON payloads with stable hourly identities for offline demos.
from datetime import datetime
from email.utils import format_datetime


def fixture_payload(kind: str, until_utc: datetime) -> str | dict:
    timestamp = until_utc.replace(minute=0, second=0, microsecond=0)
    identity = timestamp.strftime('%Y%m%d%H')
    url = f'https://fixtures.trafficsense.invalid/{kind}/{identity}'
    if kind == 'google_news_rss':
        return f'''<rss><channel><item><title>DEMO: Landslide blocks NH-7 Dehradun highway</title>
        <link>{url}</link><pubDate>{format_datetime(timestamp)}</pubDate>
        <description>SYNTHETIC FIXTURE: road closed after slope failure.</description></item>
        <item><title>Cricket team traffic standstill</title><link>{url}-noise</link>
        <pubDate>{format_datetime(timestamp)}</pubDate><description>Sports commentary only.</description>
        </item></channel></rss>'''
    row = {'url': url, 'title': 'DEMO: Severe waterlogging closes NH-7 road',
           'description': 'SYNTHETIC FIXTURE: highway diversion in Dehradun.'}
    if kind == 'gdelt_doc':
        return {'articles': [{**row, 'seendate': timestamp.strftime('%Y%m%dT%H%M%SZ')}]}
    return {'advisories': [{**row, 'published_at': timestamp.isoformat(),
                            'city': 'Dehradun', 'latitude': 30.3256, 'longitude': 78.0418}]}
