import unittest

import httpx
from baoxian_api.downloader import download_pdf


class DocumentDownloaderTests(unittest.IsolatedAsyncioTestCase):
    async def test_pdf_magic_and_final_url_are_preserved(self):
        async def handler(request):
            self.assertIn('application/pdf', request.headers['accept'])
            if request.url.path == '/old':
                return httpx.Response(302, headers={'Location': '/file.pdf'})
            return httpx.Response(200, content=b'%PDF-1.4 sample', headers={'content-type': 'application/octet-stream'})
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await download_pdf('https://example.test/old', client=client)
        self.assertEqual(result, {'ok': True, 'body': b'%PDF-1.4 sample', 'contentType': 'application/octet-stream', 'finalUrl': 'https://example.test/file.pdf'})

    async def test_failure_reasons_cover_http_empty_format_size_and_timeout(self):
        samples = [
            (httpx.Response(404), 'http'),
            (httpx.Response(200, content=b''), 'empty'),
            (httpx.Response(200, content=b'<html>', headers={'content-type': 'application/pdf'}), 'format'),
            (httpx.Response(200, content=b'%PDF-too-large'), 'too_large'),
        ]
        for response, reason in samples:
            with self.subTest(reason=reason):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                    result = await download_pdf('https://example.test/a', client=client, max_bytes=8)
                self.assertEqual((result['ok'], result['reason']), (False, reason))
        async def timeout(request): raise httpx.ReadTimeout('timeout', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            result = await download_pdf('https://example.test/a', client=client)
        self.assertEqual(result['reason'], 'timeout')
