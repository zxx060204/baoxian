import json
import unittest

from baoxian_api.aia_adapter import AiaAdapter


class AiaAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_signed_request_maps_four_documents_and_preserves_scope(self):
        requests = []
        class Gateway:
            async def fetch_page(self, url, request=None):
                requests.append(request)
                status = json.loads(request['body'])['status']
                records = [
                    {'productName': '示例终身寿险（分红型）', 'productStatus': '在售', 'productItem': 'terms/a', 'productInstruction': 'intro/a', 'ratesTable': 'prem/a', 'cashValueTable': 'cash/a'},
                    {'productName': '示例终身寿险B款（分红型）', 'productStatus': '在售', 'productItem': 'terms/b'},
                ] if status == '在售' else []
                body = {'success': True, 'product_publish': {'totalPage': 1, 'records': records}}
                return {'url': url, 'contentType': 'application/json', 'html': json.dumps(body, ensure_ascii=False)}
        result = await AiaAdapter(Gateway(), request_id_factory=lambda: '00000000-0000-4000-8000-000000000000').discover_documents({'id': 1, 'name': '友邦', 'officialSiteUrl': 'https://www.aia.com.cn'}, [{'id': 11, 'name': '示例终身寿险（分红型）', 'salesStatus': 'on_sale'}, {'id': 12, 'name': '历史产品', 'salesStatus': 'stopped'}])
        self.assertEqual(requests[0]['headers']['x-crp-sign'], '43C338110A122C2A39FA45B1A909D974')
        self.assertEqual(json.loads(requests[0]['body'])['page'], {'pageSize': 100, 'index': 0})
        self.assertEqual({item['type'] for item in result['candidates']}, {'clause','prospectus','rate_table','cash_value_table'})
        self.assertTrue(all(item['productIds'] == [11] and item['snapshotKind'] == 'api_response' and item['downloadUrl'].endswith('.pdf') for item in result['candidates']))
        self.assertEqual(result['searchedProductIds'], [11, 12])
        self.assertEqual(result['notFoundProductIds'], [12])
        self.assertEqual(result['unsearchedProductIds'], [])
        self.assertEqual(result['pageFailures'], [])

    async def test_on_sale_disclosure_does_not_match_same_name_stopped_product(self):
        name = '友邦传世颐年养老年金保险（分红型）'
        class Gateway:
            async def fetch_page(self, url, request=None):
                body = {'success': True, 'product_publish': {'totalPage': 1, 'records': [
                    {'productName': name, 'productStatus': '在售', 'productItem': 'terms/current'}
                ]}}
                return {'url': url, 'contentType': 'application/json', 'html': json.dumps(body, ensure_ascii=False)}

        result = await AiaAdapter(Gateway()).discover_documents(
            {'id': 1, 'name': '友邦', 'officialSiteUrl': 'https://www.aia.com.cn'},
            [
                {'id': 24942, 'name': name, 'salesStatus': 'on_sale'},
                {'id': 25851, 'name': name, 'salesStatus': 'stopped'},
            ],
        )

        self.assertEqual([candidate['productIds'] for candidate in result['candidates']], [[24942]])
        self.assertEqual(result['notFoundProductIds'], [25851])

    async def test_stopped_disclosures_use_the_shared_stopped_product_id(self):
        name = '友邦传世颐年养老年金保险（分红型）'
        requests = []
        class Gateway:
            async def fetch_page(self, url, request=None):
                status = json.loads(request['body'])['status']
                requests.append(status)
                records = [
                    {'productName': name, 'productStatus': '在售', 'productItem': 'terms/current'}
                ] if status == '在售' else [
                    {'productName': name, 'productStatus': '停售', 'productItem': 'terms/stopped-a'},
                    {'productName': name, 'productStatus': '停售', 'productItem': 'terms/stopped-b'},
                ]
                body = {'success': True, 'product_publish': {'totalPage': 1, 'records': records}}
                return {'url': url, 'contentType': 'application/json', 'html': json.dumps(body, ensure_ascii=False)}

        result = await AiaAdapter(Gateway()).discover_documents(
            {'id': 1, 'name': '友邦', 'officialSiteUrl': 'https://www.aia.com.cn'},
            [
                {'id': 24942, 'name': name, 'salesStatus': 'on_sale'},
                {'id': 25851, 'name': name, 'salesStatus': 'stopped'},
            ],
        )

        self.assertEqual(requests, ['在售', '停售'])
        self.assertEqual([candidate['productIds'] for candidate in result['candidates']], [[24942], [25851], [25851]])

    async def test_completed_directory_without_product_is_not_a_fetch_failure(self):
        class Gateway:
            async def fetch_page(self, url, request=None):
                body = {'success': True, 'product_publish': {'totalPage': 1, 'records': []}}
                return {'url': url, 'contentType': 'application/json', 'html': json.dumps(body)}

        result = await AiaAdapter(Gateway()).discover_documents(
            {'id': 1, 'name': '友邦', 'officialSiteUrl': 'https://www.aia.com.cn'},
            [{'id': 14, 'name': '目录没有的产品', 'salesStatus': 'on_sale'}],
        )

        self.assertEqual(result['notFoundProductIds'], [14])
        self.assertEqual(result['pageFailures'], [])

    async def test_page_failure_is_scoped_to_the_requested_sales_status(self):
        class Gateway:
            async def fetch_page(self, url, request=None):
                status = json.loads(request['body'])['status']
                if status == '停售':
                    raise RuntimeError('停售目录不可用')
                body = {'success': True, 'product_publish': {'totalPage': 1, 'records': []}}
                return {'url': url, 'contentType': 'application/json', 'html': json.dumps(body)}

        result = await AiaAdapter(Gateway()).discover_documents({'id': 1}, [
            {'id': 10, 'name': '在售产品', 'salesStatus': 'on_sale'},
            {'id': 20, 'name': '停售产品', 'salesStatus': 'stopped'},
        ])

        self.assertEqual(result['searchedProductIds'], [10])
        self.assertEqual(result['notFoundProductIds'], [10])
        self.assertEqual(result['pageFailures'][0]['productIds'], [20])

    async def test_all_failed_status_queries_keep_unknown_products_unsearched(self):
        class Gateway:
            async def fetch_page(self, url, request=None):
                raise RuntimeError('目录不可用')

        result = await AiaAdapter(Gateway()).discover_documents({'id': 1}, [
            {'id': 10, 'name': '在售产品', 'salesStatus': 'on_sale'},
            {'id': 20, 'name': '未知产品', 'salesStatus': 'unknown'},
        ])

        self.assertEqual(result['searchedProductIds'], [])
        self.assertEqual(result['notFoundProductIds'], [])
        self.assertEqual(result['unsearchedProductIds'], [20])
        self.assertEqual(result['pageFailures'][0]['productIds'], [10])
