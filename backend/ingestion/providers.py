# @file: backend/ingestion/providers.py
# @exports: omnichannel_connectors, provider_status
# @depends_on: backend/ingestion/{news_api_client,rss_client,social_omnichannel_client,x_twitter_client,gov_hazard_client,tomtom_client}.py
# @purpose: Configured independent sources and credential-free provider activation/count diagnostics.
from backend.ingestion.gov_hazard_client import GovHazardClient
from backend.ingestion.news_api_client import NewsAPIClient
from backend.ingestion.rss_client import NewsRSSClient
from backend.ingestion.social_omnichannel_client import SocialClient
from backend.ingestion.tomtom_client import TomTomIncidentClient
from backend.ingestion.x_twitter_client import HANDLES, XTwitterClient

KEY_SOURCES = {'serper': 'serper_x_social', 'tavily': 'tavily_news', 'newsdata': 'newsdata_io',
               'currents': 'currents_gnews', 'gnews': 'currents_gnews', 'youtube': 'youtube_live_news'}


def configured(settings, name):
    value = getattr(settings, name, None)
    return bool(value and value.get_secret_value().strip())


def omnichannel_connectors(settings, client=None):
    if settings.ingestion_source_mode == 'fixtures':
        return []
    sources = [NewsAPIClient(source+'_'+kind, settings, client, kind=kind, source_type=source)
               for kind, source in KEY_SOURCES.items() if configured(settings, kind+'_api_key')]
    if configured(settings, 'tomtom_api_key'):
        sources.append(TomTomIncidentClient('tomtom_incidents', settings, client))
    if configured(settings, 'data_gov_in_api_key'):
        sources.extend(GovHazardClient('data_gov_in_'+resource, settings, client, kind='data_gov',
                       resource=resource, source_type='data_gov_in') for resource in settings.data_gov_resource_ids)
    if not settings.direct_scrapers_enabled:
        return sources
    sources.extend([NewsRSSClient('google_news_1h', settings, client),
                    NewsRSSClient('google_news_6h', settings, client, recency='6h'),
                    NewsRSSClient('bing_news_rss', settings, client, engine='bing')])
    query = 'site:x.com (traffic OR landslide OR highway blocked) (India OR Uttarakhand)'
    sources.extend(NewsRSSClient('x_search_rss_'+engine, settings, client, engine=engine,
                   query=query, source_type='x_search_rss') for engine in ('google', 'bing'))
    sources.extend(XTwitterClient('x_syndication_'+handle, settings, client, handle=handle,
                   source_type='x_syndication') for handle in HANDLES)
    if all(configured(settings, field) for field in ('x_auth_token', 'x_ct0', 'x_bearer_token')) and settings.x_search_query_id:
        sources.append(XTwitterClient('x_authenticated_search', settings, client, source_type='x_search_rss'))
    sources.extend(SocialClient('telegram_public_'+channel, settings, client, kind='telegram',
                   channel=channel, source_type='telegram_public') for channel in settings.telegram_channels)
    sources.extend(SocialClient('reddit_'+subreddit, settings, client, kind='reddit', subreddit=subreddit,
                   source_type='reddit_bluesky') for subreddit in ('uttarakhand', 'dehradun', 'india'))
    sources.append(SocialClient('bluesky_search', settings, client, kind='bluesky', source_type='reddit_bluesky'))
    sources.extend(GovHazardClient('gov_ndma_sachet' if kind == 'sachet' else 'satellite_hazard_'+kind,
                   settings, client, kind=kind, source_type='gov_ndma_sachet' if kind == 'sachet' else 'satellite_hazard')
                   for kind in ('sachet', 'eonet', 'usgs', 'gdacs'))
    return sources


def provider_status(settings, connectors, summary=None):
    latest = (summary or {}).get('active_providers', {})
    status = {source.name: {'enabled': True, 'mode': source.mode, 'returned_count': None,
                           'status': 'not_polled'} for source in connectors}
    status.update(latest)
    live = getattr(settings, 'ingestion_source_mode', 'fixtures') != 'fixtures'
    for key in (*KEY_SOURCES, 'tomtom', 'geoapify', 'ors', 'data_gov_in'):
        name = key+'_api'
        enabled = live and configured(settings, key+'_api_key')
        if key == 'data_gov_in':
            enabled = enabled and bool(getattr(settings, 'data_gov_resource_ids', []))
        matched = [latest[s.name] for s in connectors if s.name in latest and
                   (s.options.get('kind') == ('data_gov' if key == 'data_gov_in' else key)
                    if hasattr(s, 'options') else False)]
        if key == 'tomtom' and 'tomtom_incidents' in latest:
            matched = [latest['tomtom_incidents']]
        status[name] = {'enabled': enabled, 'status': ('ok' if all(p['status'] == 'ok' for p in matched)
            else 'degraded') if matched else ('configured' if enabled else 'disabled'),
            'returned_count': sum(p['returned_count'] for p in matched) if matched else None}
    x_enabled = any(s.name == 'x_authenticated_search' for s in connectors)
    status['x_cookie_search'] = {'enabled': x_enabled, 'returned_count': None,
        'status': 'configured' if x_enabled else 'requires_cookie_pair_bearer_operation_id'}
    status['open_meteo'] = {'enabled': live and (getattr(settings, 'ingestion_weather_enabled', False)
                           or getattr(settings, 'scout_live_traffic_enabled', False)),
                           'status': 'context_only', 'returned_count': None}
    return status
