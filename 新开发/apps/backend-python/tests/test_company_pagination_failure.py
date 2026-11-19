import json
import unittest
from urllib.parse import parse_qs, urlsplit

from baoxian_api.aia_adapter import AiaAdapter
from baoxian_api.chinalife_adapter import ChinaLifeAdapter


class PaginationFailureTests(unittest.IsolatedAsyncioTestCase):
    async def test_later_page_failure_preserves_candidates_and_reports_incomplete_scope(self):
        for company in ('aia', 'chinalife'):
            for failure in ('network', 'invalid_response'):
                with self.subTest(company=company, failure=failure):
                    class Gateway:
                        def __init__(self): self.calls = 0
                        async def fetch_page(self, url, request=None):
                            self.calls += 1
                            if self.calls == 2:
                                if failure == 'network': raise RuntimeError('目录网络中断')
                                return {'url': url, 'html': '{invalid'}
                            if company == 'aia':
                                data = {'success': True, 'product_publish': {'totalPage': 2, 'records': [{'productName': '测试产品', 'productStatus': '在售', 'productItem': 'terms/example'}]}}
                            else:
                                data = {'count': 16, 'list': [{'productname': '测试产品', 'clause': '/terms.pdf'}]}
                            return {'url': url, 'html': json.dumps(data, ensure_ascii=False)}
                    gateway = Gateway()
                    adapter = AiaAdapter(gateway) if company == 'aia' else ChinaLifeAdapter(gateway)
                    result = await adapter.discover_documents({'id': 1}, [{'id': 10, 'name': '测试产品', 'salesStatus': 'on_sale'}] if company == 'aia' else [{'id': 10, 'name': '测试产品'}])
                    self.assertEqual(gateway.calls, 2)
                    self.assertEqual(len(result['candidates']), 1)
                    self.assertEqual(result['candidates'][0]['productIds'], [10])
                    self.assertEqual(len(result['searchedPages']), 1)
                    self.assertEqual(len(result['pageFailures']), 1)
                    self.assertIn('2', result['pageFailures'][0]['message'])
                    if company == 'chinalife':
                        self.assertEqual(parse_qs(urlsplit(result['pageFailures'][0]['url']).query)['page'], ['2'])

    async def test_premature_empty_page_preserves_results_and_reports_incomplete_scope(self):
        for company in ('aia', 'chinalife'):
            with self.subTest(company=company):
                class Gateway:
                    def __init__(self): self.calls = 0
                    async def fetch_page(self, url, request=None):
                        self.calls += 1
                        if company == 'aia':
                            rows = [{'productName': '测试产品', 'productStatus': '在售', 'productItem': 'terms/a'}] if self.calls == 1 else []
                            data = {'success': True, 'product_publish': {'totalPage': 3, 'records': rows}}
                        else:
                            rows = [{'productname': '测试产品', 'clause': '/terms.pdf'}] if self.calls == 1 else []
                            data = {'count': 45, 'list': rows}
                        return {'url': url, 'html': json.dumps(data, ensure_ascii=False)}
                gateway = Gateway()
                adapter = AiaAdapter(gateway) if company == 'aia' else ChinaLifeAdapter(gateway)
                result = await adapter.discover_documents({'id': 1}, [{'id': 10, 'name': '测试产品', 'salesStatus': 'on_sale'}] if company == 'aia' else [{'id': 10, 'name': '测试产品'}])
                self.assertEqual(gateway.calls, 2)
                self.assertEqual(len(result['candidates']), 1)
                self.assertEqual(len(result['pageFailures']), 1)
                self.assertIn('2', result['pageFailures'][0]['message'])

    async def test_empty_directory_with_no_results_is_not_a_page_failure(self):
        for company in ('aia', 'chinalife'):
            with self.subTest(company=company):
                class Gateway:
                    async def fetch_page(self, url, request=None):
                        data = {'success': True, 'product_publish': {'totalPage': 1, 'records': []}} if company == 'aia' else {'count': 0, 'list': []}
                        return {'url': url, 'html': json.dumps(data)}
                adapter = AiaAdapter(Gateway()) if company == 'aia' else ChinaLifeAdapter(Gateway())
                result = await adapter.discover_documents({'id': 1}, [])
                self.assertEqual(result['candidates'], [])
                self.assertEqual(result['pageFailures'], [])

    async def test_first_page_failure_is_scoped_for_aia_and_raises_for_chinalife(self):
        class Gateway:
            async def fetch_page(self, *args, **kwargs): raise RuntimeError('目录不可用')
        result = await AiaAdapter(Gateway()).discover_documents({'id': 1}, [{'id': 10, 'name': '测试产品', 'salesStatus': 'on_sale'}])
        self.assertEqual(result['searchedPages'], [])
        self.assertEqual(result['pageFailures'][0]['productIds'], [10])
        with self.assertRaisesRegex(RuntimeError, '目录不可用'):
            await ChinaLifeAdapter(Gateway()).discover_documents({'id': 1}, [{'id': 10, 'name': '测试产品'}])
