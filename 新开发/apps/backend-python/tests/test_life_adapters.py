import json
import unittest
from urllib.parse import parse_qs, urlsplit

from baoxian_api.chinalife_adapter import ChinaLifeAdapter


class LifeAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_chinalife_paginates_and_matches_exact_product_name(self):
        class Gateway:
            def __init__(self): self.pages = []
            async def fetch_page(self, url):
                page = int(parse_qs(urlsplit(url).query)['page'][0]); self.pages.append(page)
                rows = [{'productname': '示例年金保险', 'clause': '/a.pdf', 'productdescription': '/b.pdf', 'ratestable': '/c.pdf', 'cashvalue': '/d.pdf'}] if page == 2 else [{'productname': '其他'}]
                return {'url': url, 'html': json.dumps({'count': 16, 'list': rows}, ensure_ascii=False)}
        gateway = Gateway()
        result = await ChinaLifeAdapter(gateway).discover_documents({'id': 1, 'name': '中国人寿'}, [{'id': 10, 'name': '示例年金保险'}, {'id': 11, 'name': '示例年金保险B款'}])
        self.assertEqual(gateway.pages, [1, 2])
        self.assertEqual({item['type'] for item in result['candidates']}, {'clause', 'prospectus', 'rate_table', 'cash_value_table'})
        self.assertTrue(all(item['productIds'] == [10] and item['snapshotKind'] == 'api_response' for item in result['candidates']))
