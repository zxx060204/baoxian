import unittest

import httpx
from baoxian_api.company_gateway import CompanyHttpGateway


class CompanyGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_retry_server_error_and_limit_each_attempt(self):
        calls, waits = [], []
        async def handler(request):
            calls.append(request)
            return httpx.Response(503, text='busy') if len(calls) == 1 else httpx.Response(200, text='官网', headers={'content-type': 'text/html; charset=utf-8'})
        async def wait(host, interval): waits.append((host, interval))
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = CompanyHttpGateway(client, lambda: {'retryAttempts': 2, 'domainIntervalMs': 500, 'requestTimeoutMs': 3000}, wait)
            page = await gateway.fetch_page('https://example.test/path')
        self.assertEqual((page['url'], page['html']), ('https://example.test/path', '官网'))
        self.assertEqual(waits, [('example.test', 500), ('example.test', 500)])
        self.assertEqual(len(calls), 2)
        self.assertIn('baoxian-doc-crawler', calls[0].headers['user-agent'])

    async def test_client_error_is_not_retried(self):
        calls = []
        async def handler(request): calls.append(request); return httpx.Response(404)
        async def wait(host, interval): pass
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = CompanyHttpGateway(client, lambda: {'retryAttempts': 3, 'domainIntervalMs': 500, 'requestTimeoutMs': 3000}, wait)
            with self.assertRaisesRegex(RuntimeError, 'HTTP 404'):
                await gateway.fetch_page('https://example.test/path')
        self.assertEqual(len(calls), 1)
