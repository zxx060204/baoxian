import json
import unittest

import httpx
from baoxian_api.association import encrypt_payload
from baoxian_api.disclosure import CompanyDisclosureGateway


class DisclosureGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_exact_name_finds_official_site_with_session_and_limiter(self):
        calls = []
        async def handler(request):
            calls.append(request)
            if request.url.path == '/':
                return httpx.Response(200, text='首页', headers={'set-cookie': 'session=test; Path=/'})
            self.assertIn('session=test', request.headers.get('cookie', ''))
            if request.url.path.endswith('captchaCheck.do'):
                return httpx.Response(200, json={'msg': encrypt_payload(json.dumps({'clientIpStatus': 'success'}))})
            if request.url.path.endswith('leafColComType.do'):
                return httpx.Response(200, text="<a onclick=\"company0('A','B','C');\">国民养老保险股份有限公司</a>")
            return httpx.Response(200, text='<div>公司官网链接： www.guominpension.com</div><div>机构信息披露网站地址： https://other.example/info</div>')
        waits = []
        async def wait(): waits.append(True)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await CompanyDisclosureGateway(client, wait_for_request=wait).find('国民养老保险股份有限公司')
        self.assertEqual(result['kind'], 'found')
        self.assertEqual(result['url'], 'https://www.guominpension.com')
        self.assertIn('getInterNetCompanyInfo.do', result['sourceUrl'])
        self.assertEqual(len(calls), 4)
        self.assertEqual(len(waits), 4)

    async def test_missing_duplicate_or_invalid_official_link_stays_unavailable(self):
        entry = "<a onclick=\"company0('A','B','C');\">某某人寿保险股份有限公司</a>"
        async def lookup(company, listing, detail):
            async def handler(request):
                if request.url.path.endswith('captchaCheck.do'):
                    return httpx.Response(200, json={'msg': encrypt_payload(json.dumps({'clientIpStatus': 'success'}))})
                if request.url.path.endswith('leafColComType.do'):
                    return httpx.Response(200, text=listing)
                if request.url.path.endswith('getInterNetCompanyInfo.do'):
                    return httpx.Response(200, text=detail)
                return httpx.Response(200, text='首页')
            async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
                return await CompanyDisclosureGateway(client).find(company)
        unmatched = await lookup('某某养老保险股份有限公司', entry, '')
        duplicate = await lookup('某某人寿保险股份有限公司', entry + entry, '')
        invalid = await lookup('某某人寿保险股份有限公司', entry, '<div>公司官网链接：https://icidp.iachina.cn/detail</div>')
        missing = await lookup('某某人寿保险股份有限公司', entry, '<div>机构信息披露网站地址：https://other.example/info</div>')
        self.assertIn('未找到', unmatched['reason'])
        self.assertIn('多个同名', duplicate['reason'])
        self.assertIn('未提供', invalid['reason'])
        self.assertIn('未提供', missing['reason'])

    async def test_human_challenge_and_incomplete_directory_stop_lookup(self):
        async def challenge(request):
            if request.url.path.endswith('captchaCheck.do'):
                return httpx.Response(200, json={'msg': encrypt_payload(json.dumps({'clientIpStatus': 'challenge'}))})
            return httpx.Response(200, text='首页')
        async with httpx.AsyncClient(transport=httpx.MockTransport(challenge)) as client:
            with self.assertRaisesRegex(RuntimeError, '人机验证'):
                await CompanyDisclosureGateway(client).find('某某保险')
        async def malformed(request):
            if request.url.path.endswith('captchaCheck.do'):
                return httpx.Response(200, json={'msg': encrypt_payload(json.dumps({'clientIpStatus': 'success'}))})
            if request.url.path.endswith('leafColComType.do'):
                return httpx.Response(200, text="<a onclick=\"company0('bad');\">公司</a>")
            return httpx.Response(200, text='首页')
        async with httpx.AsyncClient(transport=httpx.MockTransport(malformed)) as client:
            with self.assertRaisesRegex(RuntimeError, '目录解析不完整'):
                await CompanyDisclosureGateway(client).find('某某保险')
