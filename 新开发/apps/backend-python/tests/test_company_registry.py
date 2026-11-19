import json
import unittest

from baoxian_api.company_registry import CompanyRegistry


class CompanyRegistryTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_group_subsidiaries_use_their_own_site(self):
        for site in ('https://yl.pingan.com', 'https://health.pingan.com', 'https://health.cpic.com.cn'):
            with self.subTest(site=site):
                urls = []
                class Gateway:
                    async def fetch_page(self, url, request=None):
                        urls.append(url); return {'url': url, 'html': '<html></html>'}
                await CompanyRegistry(Gateway()).discover_documents({'id': 1, 'name': '集团其他公司', 'officialSiteUrl': site}, [{'id': 1, 'name': '示例保险'}])
                self.assertEqual(urls, [site])

    async def test_aia_successfully_read_empty_directory_is_not_a_page_failure(self):
        class Gateway:
            async def fetch_page(self, url, request=None): return {'url': url, 'html': json.dumps({'success': True, 'product_publish': {'totalPage': 1, 'records': []}})}
        result = await CompanyRegistry(Gateway()).discover_documents({'id': 1, 'name': '友邦', 'officialSiteUrl': 'https://www.aia.com.cn'}, [{'id': 7, 'name': '历史产品', 'salesStatus': 'on_sale'}])
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['pageFailures'], [])

    async def test_chinalife_adapter_keeps_registry_coverage_failure(self):
        class Gateway:
            async def fetch_page(self, url, request=None): return {'url': url, 'html': json.dumps({'count': 0, 'list': []})}
        result = await CompanyRegistry(Gateway()).discover_documents({'id': 1, 'name': '中国人寿', 'officialSiteUrl': 'https://www.e-chinalife.com'}, [{'id': 7, 'name': '历史产品'}])
        self.assertEqual(result['candidates'], [])
        self.assertIn('未确认覆盖', result['pageFailures'][0]['message'])

    async def test_aia_domain_uses_aia_endpoint(self):
        class Gateway:
            async def fetch_page(self, url, request=None):
                self.url = url
                return {'url': url, 'html': json.dumps({'success': True, 'product_publish': {'totalPage': 1, 'records': [{'productName': '示例保险', 'productStatus': '在售', 'productItem': 'terms/a'}]}}, ensure_ascii=False)}
        gateway = Gateway()
        result = await CompanyRegistry(gateway).discover_documents({'id': 1, 'name': '友邦', 'officialSiteUrl': 'https://www.aia.com.cn'}, [
            {'id': 7, 'name': '示例保险', 'salesStatus': 'on_sale'},
            {'id': 8, 'name': '示例保险', 'salesStatus': 'stopped'},
        ])
        self.assertIn('cws.aia.com.cn', gateway.url)
        self.assertEqual(result['candidates'][0]['productIds'], [7])
        self.assertEqual(result['candidates'][0]['type'], 'clause')
        self.assertEqual(result['notFoundProductIds'], [8])
        self.assertEqual(result['unsearchedProductIds'], [])
        self.assertEqual(result['pageFailures'], [])

    async def test_other_insurers_use_generic_adapter_for_now(self):
        urls = []
        class Gateway:
            async def fetch_page(self, url, request=None):
                urls.append(url)
                return {'url': url, 'html': '<html></html>'}
        await CompanyRegistry(Gateway()).discover_documents({'id': 1, 'name': '平安', 'officialSiteUrl': 'https://life.pingan.com'}, [{'id': 7, 'name': '产品'}])
        self.assertEqual(urls, ['https://life.pingan.com'])
