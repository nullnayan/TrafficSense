# @file: backend/ingestion/parsing.py
# @exports: parse_records, validate_window
# @depends_on: backend/schemas/ingestion.py
# @purpose: Defensive RSS/Atom/JSON normalization, UTC windows, and source provenance.
import re
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html import unescape
from xml.etree.ElementTree import ParseError

from defusedxml import ElementTree
from defusedxml.common import DefusedXmlException
from pydantic import ValidationError

from backend.schemas.ingestion import RawIngestedItem


def validate_window(since: datetime, until: datetime) -> tuple[datetime, datetime]:
    if since.tzinfo is None or until.tzinfo is None or since.utcoffset() is None or until.utcoffset() is None:
        raise ValueError('Ingestion windows require timezone-aware datetimes')
    if since >= until:
        raise ValueError('since_utc must precede until_utc')
    return since.astimezone(timezone.utc), until.astimezone(timezone.utc)


def parse_records(payload, source: str, since: datetime, until: datetime) -> tuple[list, int]:
    since, until = validate_window(since, until)
    if isinstance(payload, str):
        try:
            root = ElementTree.fromstring(payload)
        except (ParseError, DefusedXmlException) as exc:
            raise ValueError('Invalid or unsafe XML') from exc
        rows = []
        for entry in root.iter():
            if entry.tag.rsplit('}', 1)[-1] not in ('item', 'entry'):
                continue
            row = {}
            for child in entry:
                tag = child.tag.rsplit('}', 1)[-1]
                row[tag] = child.get('href') or ''.join(child.itertext())
            rows.append(row)
    elif isinstance(payload, list):
        rows = payload
    elif isinstance(payload, dict):
        if not any(key in payload for key in ('articles', 'advisories', 'items')):
            raise ValueError('JSON has no recognized records collection')
        rows = payload.get('articles', payload.get('advisories', payload.get('items', [])))
    else:
        raise ValueError('Unsupported feed payload')
    if not isinstance(rows, list):
        raise ValueError('Feed records must be an array')
    records, invalid = [], 0
    for row in rows:
        try:
            if not isinstance(row, dict):
                raise ValueError('Invalid record')
            stamp = row.get('published_at') or row.get('pubDate') or row.get('published') or row.get('seendate')
            try:
                published = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            except (ValueError, TypeError):
                published = parsedate_to_datetime(stamp)
            if published.tzinfo is None:
                raise ValueError('Missing publication timezone')
            item = RawIngestedItem(
                source_name=source, source_url=row.get('url') or row.get('link'),
                title=unescape(row.get('title', '')).strip()[:1000],
                raw_text=unescape(re.sub('<[^>]+>', ' ', row.get('description') or row.get('summary')
                                        or row.get('text') or row.get('title', ''))).strip()[:100000],
                published_at=published,
            )
            if since <= item.published_at < until:
                records.append((item, row))
        except (ValueError, TypeError, AttributeError, ValidationError):
            invalid += 1
    return records, invalid
