# @file: backend/ingestion/deduplication.py
# @exports: normalized_url, fingerprint, deduplicate
# @depends_on: hashlib, urllib.parse
# @purpose: Stable canonical URLs and content fingerprints before durable outbox publication.
import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from pydantic import HttpUrl


def normalized_url(value):
    parts = urlsplit(str(value))
    host = (parts.hostname or '').lower().removeprefix('www.')
    host = 'x.com' if host == 'twitter.com' else host
    query = sorted((key, val) for key, val in parse_qsl(parts.query, keep_blank_values=True)
                   if not key.lower().startswith('utm_') and key.lower() not in ('fbclid', 'gclid', 'ref', 'ref_src'))
    # Keep route-significant query parameters such as YouTube video ID.
    authority = host+(':'+str(parts.port) if parts.port and parts.port not in (80, 443) else '')
    return urlunsplit(('https', authority, parts.path.rstrip('/') or '/', urlencode(query), ''))


def fingerprint(item):
    content = item.raw_text.split('\n[source_timestamp:', 1)[0].split('\n[weather_context', 1)[0]
    normalized = re.sub(r'\s+', ' ', (item.title+' '+content).lower()).strip()
    return hashlib.sha256(normalized.encode()).hexdigest()


def deduplicate(items):
    urls, hashes, result = set(), set(), []
    for item in items:
        url, digest = normalized_url(item.source_url), fingerprint(item)
        if url not in urls and digest not in hashes:
            result.append(item.model_copy(update={'source_url': HttpUrl(url)}))
            urls.add(url)
            hashes.add(digest)
    return result
