# @file: backend/ingestion/rss_client.py
# @exports: NewsRSSClient
# @depends_on: backend/ingestion/source.py
# @purpose: One-hour/six-hour Google and Bing corridor/national or X-specific RSS searches.
from backend.ingestion.source import HTTPSource

QUERY = ('(Dehradun OR Rishikesh OR Haridwar OR Mussoorie OR NH-7 OR India) '
         '(landslide OR "highway blocked" OR traffic OR waterlogging OR "railway crossing" OR "bridge closed")')


class NewsRSSClient(HTTPSource):
    async def rows(self, client, since, until):
        query = self.options.get('query', QUERY)
        if self.options.get('engine') == 'bing':
            response = await self.request(client, 'https://www.bing.com/news/search',
                params={'q': query, 'format': 'rss', 'sortby': 'date'})
        else:
            response = await self.request(client, 'https://news.google.com/rss/search', params={
                'q': query+' when:'+self.options.get('recency', '1h'),
                'hl': 'en-IN', 'gl': 'IN', 'ceid': 'IN:en'})
        return response.text
