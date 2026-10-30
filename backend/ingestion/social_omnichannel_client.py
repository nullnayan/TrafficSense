# @file: backend/ingestion/social_omnichannel_client.py
# @exports: SocialClient
# @depends_on: backend/ingestion/{source,social_html}.py
# @purpose: Public Telegram previews, regional Reddit JSON and bounded Bluesky post search.
from datetime import datetime, timezone

from backend.ingestion.social_html import TelegramParser
from backend.ingestion.source import HTTPSource, disruption, observation


class SocialClient(HTTPSource):
    async def rows(self, client, since, until):
        kind = self.options['kind']
        if kind == 'telegram':
            response = await self.request(client, 'https://t.me/s/'+self.options['channel'])
            parser = TelegramParser()
            parser.feed(response.text)
            return [observation(row['text'][:200], 'https://t.me/'+row['post'], row['text'], row['stamp'], until)
                    for row in parser.messages if disruption(row['text'])]
        if kind == 'reddit':
            subreddit = self.options['subreddit']
            path = 'search.json' if subreddit == 'india' else 'new.json'
            params = {'limit': 25, 'raw_json': 1}
            if path == 'search.json':
                params.update(q='traffic OR landslide OR waterlogging OR highway', sort='new', restrict_sr=1)
            response = await self.request(client, f'https://www.reddit.com/r/{subreddit}/{path}', params=params)
            result = []
            for entry in response.json()['data']['children']:
                row = entry['data']
                content = row['title']+' '+row.get('selftext', '')
                if disruption(content):
                    stamp = datetime.fromtimestamp(row['created_utc'], timezone.utc).isoformat()
                    result.append(observation(row['title'], 'https://www.reddit.com'+row['permalink'], content, stamp, until))
            return result
        response = await self.request(client, 'https://public.api.bsky.app/xrpc/app.bsky.feed.searchPosts', params={
            'q': 'Uttarakhand traffic landslide', 'sort': 'latest', 'limit': 25,
            'since': since.isoformat(), 'until': until.isoformat()})
        result = []
        for post in response.json()['posts']:
            text, uri = post['record']['text'], post['uri'].split('/')
            if disruption(text):
                result.append(observation(text[:200], f'https://bsky.app/profile/{uri[2]}/post/{uri[-1]}',
                                          text, post['record'].get('createdAt'), until))
        return result
