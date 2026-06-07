from http.client import responses

from apify_client import ApifyClientAsync
from dotenv import load_dotenv
import logging
import asyncio
import os

load_dotenv()
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

class Scraper:
    def __init__(self,run_config):
        self.run_config = run_config
        self.client = ApifyClientAsync(os.getenv("APIFY_KEY"))
    async def scrape(self, actor):
        try:
            data={}
            jobs=[]
            search =[]
            for term in self.run_config['searchTerms']:
                config = {i:v for i,v in self.run_config.items() if i != 'searchTerms'}
                config['searchTerm'] = term
                search.append(term)
                jobs.append(self.client.actor(actor).call(run_input= config))
            responses = await asyncio.gather(*jobs, return_exceptions=True)
            for searchTerm,response in zip(search,responses):
                if isinstance(responses, Exception):
                    continue
                data[searchTerm] = await self._get_data(response)
            return data
        except Exception as e:
            logging.error(e)

    async def _get_data(self, run):
        data = await self.client.dataset(run["defaultDatasetId"]).list_items()
        return data.items





