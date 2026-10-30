# @file: backend/ingestion/x_twitter_client.py
# @exports: XTwitterClient, HANDLES
# @depends_on: backend/ingestion/{source,social_html}.py
# @purpose: Official public syndication timelines plus explicitly configured authenticated search operation.
import json

from backend.ingestion.social_html import NextDataParser
from backend.ingestion.source import HTTPSource, disruption, observation, walk_dicts

HANDLES = ('uttarakhandcops', 'NHAI_Official', 'dtptraffic', 'ndmaindia', 'Indiametdept',
           'blrcitytraffic', 'MumbaiPolice')


class XTwitterClient(HTTPSource):
    async def rows(self, client, since, until):
        handle = self.options.get('handle')
        if handle:
            response = await self.request(client,
                'https://syndication.twitter.com/srv/timeline-profile/screen-name/'+handle)
            parser = NextDataParser()
            parser.feed(response.text)
            if not parser.parts:
                raise ValueError('X public timeline JSON unavailable')
            data = json.loads(''.join(parser.parts))
        else:
            settings = self.settings
            if not (settings.x_auth_token.get_secret_value() and settings.x_ct0.get_secret_value()
                    and settings.x_search_query_id and settings.x_bearer_token.get_secret_value()):
                raise ValueError('X search requires cookie pair, bearer and current operation ID')
            response = await self.request(client, 'https://x.com/i/api/graphql/'+settings.x_search_query_id+'/SearchTimeline',
                headers={'Authorization': 'Bearer '+settings.x_bearer_token.get_secret_value(),
                         'X-CSRF-Token': settings.x_ct0.get_secret_value(), 'X-Twitter-Auth-Type': 'OAuth2Session',
                         'Cookie': 'auth_token='+settings.x_auth_token.get_secret_value()+'; ct0='+settings.x_ct0.get_secret_value()},
                params={'variables': json.dumps({'rawQuery': '(traffic OR landslide OR blocked) (India OR Uttarakhand)',
                         'count': 20, 'querySource': 'typed_query', 'product': 'Latest'}),
                        'features': json.dumps({'responsive_web_graphql_exclude_directive_enabled': True,
                         'responsive_web_graphql_skip_user_profile_image_extensions_enabled': False,
                         'responsive_web_graphql_timeline_navigation_enabled': True})})
            data = response.json()
            if data.get('errors'):
                raise ValueError('X search operation rejected')
        result, seen = [], set()
        for node in walk_dicts(data):
            legacy = node.get('legacy', node)
            text = legacy.get('full_text') or legacy.get('text')
            identity = legacy.get('id_str') or node.get('rest_id')
            if text and identity and identity not in seen and disruption(text):
                seen.add(identity)
                result.append(observation(text[:200], f'https://x.com/{handle or "i"}/status/{identity}',
                                          text, legacy.get('created_at'), until))
        return result
