# @file: backend/ingestion/news_api_client.py
# @exports: NewsAPIClient, NEWS_QUERY
# @depends_on: backend/ingestion/source.py
# @purpose: Independent authenticated Serper, Tavily, NewsData, Currents, GNews and YouTube adapters.
from backend.ingestion.source import HTTPSource, observation

NEWS_QUERY = '(traffic OR landslide OR accident OR waterlogging OR highway OR "railway crossing") India'
SOCIAL_QUERY = ('(site:x.com OR site:twitter.com OR site:instagram.com) '
                '(traffic OR landslide OR accident OR "highway blocked" OR waterlogging OR "railway crossing") '
                '(Uttarakhand OR Dehradun OR Rishikesh OR NH7 OR India OR uttarakhandcops OR NHAI_Official OR dtptraffic)')


class NewsAPIClient(HTTPSource):
    async def rows(self, client, since, until):
        kind = self.options['kind']
        key = getattr(self.settings, kind+'_api_key').get_secret_value().strip()
        if kind == 'serper':
            response = await self.request(client, 'https://google.serper.dev/search', 'POST',
                headers={'X-API-KEY': key}, json={'q': SOCIAL_QUERY, 'gl': 'in', 'tbs': 'qdr:d', 'num': 20})
            rows = response.json()['organic']
        elif kind == 'tavily':
            response = await self.request(client, 'https://api.tavily.com/search', 'POST',
                json={'api_key': key, 'query': NEWS_QUERY, 'topic': 'news', 'days': 1, 'max_results': 20})
            rows = response.json()['results']
        elif kind == 'newsdata':
            response = await self.request(client, 'https://newsdata.io/api/1/latest', params={
                'apikey': key, 'country': 'in', 'language': 'en,hi',
                'q': 'traffic OR landslide OR accident OR waterlogging OR highway'})
            rows = response.json()['results']
        elif kind == 'currents':
            response = await self.request(client, 'https://api.currentsapi.services/v1/search', params={
                'apiKey': key, 'keywords': 'traffic landslide highway waterlogging', 'country': 'IN',
                'start_date': since.isoformat(), 'end_date': until.isoformat()})
            rows = response.json()['news']
        elif kind == 'gnews':
            response = await self.request(client, 'https://gnews.io/api/v4/search', params={
                'apikey': key, 'q': 'traffic OR landslide OR waterlogging OR highway', 'country': 'in',
                'from': since.isoformat(), 'to': until.isoformat(), 'max': 10})
            rows = response.json()['articles']
        elif kind == 'youtube':
            response = await self.request(client, 'https://www.googleapis.com/youtube/v3/search', params={
                'key': key, 'part': 'snippet', 'type': 'video', 'order': 'date', 'regionCode': 'IN',
                'publishedAfter': since.isoformat(), 'publishedBefore': until.isoformat(), 'maxResults': 25,
                'q': '(ANI|PTI|"ABP Ganga"|"News18 Uttarakhand") (landslide|"highway blocked"|waterlogging)'})
            return [observation(row['snippet']['title'],
                'https://www.youtube.com/watch?v='+row['id']['videoId'], row['snippet']['description'],
                row['snippet']['publishedAt'], until) for row in response.json()['items']]
        else:
            raise ValueError('Unsupported news API')
        result = []
        for row in rows:
            stamp = row.get('publishedAt') or row.get('published_date') or row.get('pubDate') or row.get('published')
            if kind == 'newsdata' and stamp and len(stamp) == 19:
                stamp += '+00:00'  # NewsData documents pubDate in UTC.
            result.append(observation(row.get('title', ''), row.get('url') or row.get('link'),
                row.get('content') or row.get('description') or row.get('snippet'), stamp, until))
        return result
