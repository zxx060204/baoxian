import asyncio
from urllib.parse import urlsplit


class CompanyHttpError(RuntimeError):
    def __init__(self, status):
        super().__init__(f'官网页面读取失败 HTTP {status}')
        self.status = status


class CompanyHttpGateway:
    def __init__(self, client, settings, wait_for_domain):
        self.client = client
        self.settings = settings
        self.wait_for_domain = wait_for_domain

    async def fetch_page(self, url, request=None):
        settings = await asyncio.to_thread(self.settings)
        attempts = max(1, settings['retryAttempts'])
        for attempt in range(attempts):
            try:
                async with asyncio.timeout(settings['requestTimeoutMs'] / 1000):
                    await self.wait_for_domain(urlsplit(url).netloc, settings['domainIntervalMs'])
                    headers = {'user-agent': 'Mozilla/5.0 (compatible; baoxian-doc-crawler/0.1)'}
                    if request:
                        headers.update(request.get('headers', {}))
                    response = await self.client.request(request.get('method', 'GET') if request else 'GET', url, content=request.get('body') if request else None, headers=headers, follow_redirects=True)
                if not response.is_success:
                    raise CompanyHttpError(response.status_code)
                return {'url': str(response.url) or url, 'html': response.text, 'contentType': response.headers.get('content-type', '')}
            except CompanyHttpError as error:
                if error.status < 500 or attempt == attempts - 1:
                    raise
            except Exception:
                if attempt == attempts - 1:
                    raise
