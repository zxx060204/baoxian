import asyncio
import json
import unittest
from pathlib import Path

import httpx
from baoxian_api.association import AssociationGateway, AssociationGatewayError, AssociationAdapter, SourceUnavailableError, RateLimitedAssociationGateway, encrypt_payload

FIXTURES = Path(__file__).resolve().parent / 'fixtures/association'


class AssociationGatewayTests(unittest.IsolatedAsyncioTestCase):
    async def test_recorded_page_is_decrypted_and_request_matches_contract(self):
        recorded = json.loads((FIXTURES / 'list-page-1.json').read_text(encoding='utf-8'))
        async def handler(request):
            self.assertEqual(request.method, 'POST')
            self.assertEqual(request.url.path, '/sinopipi/prodtermsinfo/selectConsumerList')
            self.assertEqual(request.headers['authorization'], 'fixed')
            payload = json.loads(request.content)
            self.assertEqual((payload['prodtypecode'], payload['cplbone'], payload['pageNum'], payload['pageSize']), ('PubNewProdTypeCode_00', 'PubNewProdTypeCode_00', 1, 10))
            return httpx.Response(200, json=recorded)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            gateway = AssociationGateway(client, authorization_factory=lambda: 'fixed')
            page = await gateway.list_product_page('PubNewProdTypeCode_00', 1, 10)
        self.assertEqual(page['total'], 14664)
        self.assertEqual(len(page['records']), 10)
        self.assertEqual(page['records'][0], {
            'companyName': '中信保诚人寿保险有限公司',
            'productName': '中信保诚「隽享瑞金玺耀版」终身寿险B3款（分红型）',
            'salesStatusRaw': '在售', 'saleDateRaw': '2026-11-06',
            'recordId': 'ef5a99a2-4027-4b24-b9ef-009e99cc6235',
        })
        self.assertEqual(page['sourceUrl'], 'https://tiaokuan.iachina.cn/#/index')

    async def test_categories_from_recording_and_node_compatible_encryption(self):
        recorded = json.loads((FIXTURES / 'categories.json').read_text(encoding='utf-8'))
        async def handler(request):
            self.assertEqual(request.url.params['codeType'], 'PubNewProdTypeCode')
            return httpx.Response(200, json=recorded)
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            result = await AssociationGateway(client).list_categories()
        self.assertEqual(len(result), 5)
        self.assertEqual(result[0], {'code': 'PubNewProdTypeCode_00', 'name': '人寿保险'})
        self.assertEqual(encrypt_payload('hello 世界'), 'LnYRC1dBCb/HAdpzRdcSFw==')

    async def test_errors_are_distinguished_from_empty_results(self):
        responses = [
            (httpx.Response(502, text='error'), 'http'),
            (httpx.Response(200, text='<html>', headers={'content-type': 'text/html'}), 'invalid_response'),
            (httpx.Response(200, json={'code': '401', 'msg': '请求错误'}), 'source'),
            (httpx.Response(200, json={'code': '200', 'data': 'bad ciphertext'}), 'decrypt'),
        ]
        for response, kind in responses:
            with self.subTest(kind=kind):
                async with httpx.AsyncClient(transport=httpx.MockTransport(lambda request: response)) as client:
                    with self.assertRaises(AssociationGatewayError) as raised:
                        await AssociationGateway(client).list_product_page('x', 1, 10)
                    self.assertEqual(raised.exception.kind, kind)
        async def timeout(request):
            raise httpx.ReadTimeout('timeout', request=request)
        async with httpx.AsyncClient(transport=httpx.MockTransport(timeout)) as client:
            with self.assertRaises(AssociationGatewayError) as raised:
                await AssociationGateway(client).list_product_page('x', 1, 10)
            self.assertEqual(raised.exception.kind, 'timeout')


class AssociationAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_maps_statuses_pagination_and_source_fields(self):
        class Gateway:
            async def list_product_page(self, category, page, page_size):
                self.input = (category, page, page_size)
                return {'total': 21, 'sourceUrl': 'https://example.test', 'records': [
                    {'companyName': '甲', 'productName': '一', 'salesStatusRaw': '在售', 'saleDateRaw': '2026-01-01', 'recordId': 'A'},
                    {'companyName': '乙', 'productName': '二', 'salesStatusRaw': ' 停售 '},
                    {'companyName': '丙', 'productName': '三', 'salesStatusRaw': '停用'},
                    {'companyName': '丁', 'productName': '四', 'salesStatusRaw': '暂停销售'},
                ]}
        gateway = Gateway()
        result = await AssociationAdapter(gateway).read_page('A01', '2')
        self.assertEqual(gateway.input, ('A01', 2, 10))
        self.assertEqual((result['nextCursor'], result['reportedTotal']), ('3', 21))
        self.assertEqual([p['salesStatus'] for p in result['products']], ['on_sale', 'off_sale', 'disabled', 'unknown'])
        first = result['products'][0]
        self.assertEqual((first['salesStatusRaw'], first['saleDateRaw'], first['sourceRecordId'], first['sourceUrl']), ('在售', '2026-01-01', 'A', 'https://example.test'))
        self.assertTrue(first['fetchedAt'].endswith('Z'))

    async def test_source_errors_are_wrapped_but_cancellation_propagates(self):
        class Gateway:
            async def list_product_page(self, *args):
                raise OSError('unavailable')
        with self.assertRaises(SourceUnavailableError):
            await AssociationAdapter(Gateway()).read_page('A01')
        class Cancelled:
            async def list_product_page(self, *args):
                raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await AssociationAdapter(Cancelled()).read_page('A01')


class AssociationRateLimitTests(unittest.IsolatedAsyncioTestCase):
    async def test_waits_before_categories_and_each_page(self):
        calls = []
        class Limiter:
            async def wait(self, host, interval): calls.append(('wait', host, interval))
        class Gateway:
            async def list_categories(self): calls.append(('categories',)); return []
            async def list_product_page(self, category, page, page_size):
                calls.append(('page', category, page, page_size))
                return {'records': [], 'total': 0, 'sourceUrl': 'https://example.test'}
        gateway = RateLimitedAssociationGateway(Gateway(), Limiter(), 'example.test', lambda: 500)
        await gateway.list_categories()
        await gateway.list_product_page('A', 2, 10)
        self.assertEqual(calls, [('wait', 'example.test', 500), ('categories',), ('wait', 'example.test', 500), ('page', 'A', 2, 10)])
