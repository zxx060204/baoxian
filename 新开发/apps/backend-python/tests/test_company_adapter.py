import asyncio
import unittest
from pathlib import Path

from baoxian_api.company_adapter import CompanyAdapter


class CompanyAdapterTests(unittest.IsolatedAsyncioTestCase):
    async def test_configured_document_entry_is_used_instead_of_company_homepage(self):
        calls = []
        class Gateway:
            async def fetch_page(self, url):
                calls.append(url)
                return {'url': url, 'contentType': 'text/html', 'html': '<table><tr><td>示例产品</td><td><a href="/terms.pdf">条款</a></td></tr></table>'}
        await CompanyAdapter('entry', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/', 'documentEntryUrl': 'https://official.example/disclosure'}, [{'id': 2, 'name': '示例产品'}])
        self.assertEqual(calls[0], 'https://official.example/disclosure')

    async def test_scans_sixth_disclosure_entry(self):
        root = 'https://official.example/'
        pages = {root: ''.join(f'<a href="/disclosure-{index}">产品信息目录{index}</a>' for index in range(1, 7))}
        pages.update({f'{root}disclosure-{index}': '<html></html>' for index in range(1, 6)})
        pages[f'{root}disclosure-6'] = '<table><tr><td>示例年金保险</td><td><a href="/sixth.pdf">条款</a></td></tr></table>'
        class Gateway:
            async def fetch_page(self, url): return {'url': url, 'html': pages[url], 'contentType': 'text/html'}
        result = await CompanyAdapter('sixth', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': root}, [{'id': 9, 'name': '示例年金保险'}])
        self.assertEqual(result['candidates'][0]['productIds'], [9])
        self.assertIn(f'{root}disclosure-6', result['searchedPages'])

    async def test_follows_list_until_third_page_contains_candidate(self):
        pages = {
            'https://official.example/disclosure': '<a href="/page2">下一页</a>',
            'https://official.example/page2': '<a href="/page3">下一页</a>',
            'https://official.example/page3': '<table><tr><td>产品甲</td><td><a href="/third.pdf">条款</a></td></tr></table>',
        }
        class Gateway:
            async def fetch_page(self, url): return {'url': url, 'html': pages[url], 'contentType': 'text/html'}
        result = await CompanyAdapter('third-page', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/disclosure'}, [{'id': 3, 'name': '产品甲'}])
        self.assertEqual(result['searchedPages'], list(pages))
        self.assertEqual((result['candidates'][0]['productIds'], result['candidates'][0]['type']), ([3], 'clause'))

    async def test_missing_official_site_raises_instead_of_returning_empty_results(self):
        with self.assertRaisesRegex(ValueError, '未配置官网'):
            await CompanyAdapter('missing', object()).discover_documents({'id': 1, 'name': '示例'}, [])

    async def test_gateway_cancellation_propagates(self):
        class Gateway:
            async def fetch_page(self, url): raise asyncio.CancelledError()
        with self.assertRaises(asyncio.CancelledError):
            await CompanyAdapter('cancel', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/'}, [])

    async def test_each_link_in_product_row_keeps_its_own_type(self):
        class Gateway:
            async def fetch_page(self, url):
                return {'url': url, 'contentType': 'text/html', 'html': '<table><tr><td>示例年金保险</td><td><a href="/a.pdf">条款</a><a href="/b.pdf">费率表</a><a href="/c.pdf">现金价值全表</a><a href="/d.pdf">产品说明书</a></td></tr></table>'}
        result = await CompanyAdapter('table', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/'}, [{'id': 1, 'name': '示例年金保险'}])
        self.assertEqual([(item['title'], item['type'], item['productIds']) for item in result['candidates']], [('条款','clause',[1]), ('费率表','rate_table',[1]), ('现金价值全表','cash_value_table',[1]), ('产品说明书','prospectus',[1])])
        self.assertEqual(result['pageFailures'], [])

    async def test_ambiguous_download_type_is_reported_without_guessing(self):
        class Gateway:
            async def fetch_page(self, url):
                return {'url': url, 'contentType': 'text/html', 'html': '<table><tr><td>示例年金保险</td><td>产品条款、现金价值表 <a href="/file.pdf">下载</a></td></tr></table>'}
        result = await CompanyAdapter('ambiguous', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/'}, [{'id': 1, 'name': '示例年金保险'}])
        self.assertEqual(result['candidates'], [])
        self.assertIn('资料类型无法确定', result['pageFailures'][0]['message'])

    async def test_fixtures_preserve_ambiguous_candidates_and_product_source(self):
        fixtures = Path(__file__).resolve().parent / 'fixtures/company-example'
        entry = 'https://official.example/company/disclosure'
        product = 'https://official.example/products/a.html'
        pages = {entry: (fixtures / 'entry.html').read_text(encoding='utf-8'), product: (fixtures / 'product-a.html').read_text(encoding='utf-8')}
        class Gateway:
            async def fetch_page(self, url): return {'url': url, 'html': pages[url], 'contentType': 'text/html'}
        result = await CompanyAdapter('example', Gateway()).discover_documents({'id': 1, 'name': '示例人寿', 'officialSiteUrl': entry}, [{'id': 10, 'name': '示例终身寿险'}])
        self.assertEqual(result['searchedPages'], [entry, product])
        shared = next(item for item in result['candidates'] if item['title'] == '共用费率表')
        self.assertEqual(shared['productIds'], [])
        clause = next(item for item in result['candidates'] if item['title'] == '示例终身寿险条款')
        self.assertEqual((clause['productIds'], clause['officialProductUrl'], clause['downloadUrl']), ([10], product, 'https://official.example/files/a-clause.pdf'))
        self.assertIn('示例终身寿险', clause['snapshotHtml'])

    async def test_navigation_pagination_and_page_failure_are_reported(self):
        pages = {'https://official.example/': '<a href="/disclosure">公开信息披露</a>', 'https://official.example/disclosure': '<a href="/page2">下一页</a>', 'https://official.example/page2': '<table><tr><td>产品甲条款</td><td><a href="/a.pdf">下载</a></td></tr></table><a href="/page3">下一页</a>'}
        class Gateway:
            async def fetch_page(self, url):
                if url.endswith('/page3'): raise OSError('连接超时')
                return {'url': url, 'html': pages[url], 'contentType': 'text/html'}
        result = await CompanyAdapter('pages', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/'}, [{'id': 1, 'name': '产品甲'}])
        self.assertEqual(result['candidates'][0]['productIds'], [1])
        self.assertEqual(result['candidates'][0]['type'], 'clause')
        self.assertIn('https://official.example/page2', result['searchedPages'])
        self.assertEqual(result['pageFailures'], [{'url': 'https://official.example/page3', 'message': '连接超时'}])

    async def test_homepage_without_directory_reports_incomplete_scope(self):
        class Gateway:
            async def fetch_page(self, url): return {'url': url, 'html': '<html>首页</html>', 'contentType': 'text/html'}
        result = await CompanyAdapter('home', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/'}, [{'id': 1, 'name': '产品甲'}])
        self.assertIn('资料目录', result['pageFailures'][0]['message'])

    async def test_recorded_real_company_pages_extract_product_clause(self):
        fixtures = Path(__file__).resolve().parent / 'fixtures'
        samples = [
            ('citic-prudential/product-page.html', 'https://www.citic-prudential.com.cn/productinformation/info_itemid_109.html', '中信保诚「隽享承金私享版」终身寿险B3款（分红型）', 18),
            ('taikang/list.html', 'https://www.taikanglife.com/publicinfonew/specialinfo/internetinfonew/protermsnew/list_423_1.html', '泰康乐享福养老年金保险（互联网）', 46),
        ]
        for path, url, name, ident in samples:
            with self.subTest(path=path):
                page_html = (fixtures / path).read_text(encoding='utf-8')
                class Gateway:
                    async def fetch_page(self, requested): return {'url': url, 'html': page_html, 'contentType': 'text/html'}
                result = await CompanyAdapter('recorded', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': url}, [{'id': ident, 'name': name}])
                self.assertTrue(any(item['productIds'] == [ident] and item['type'] == 'clause' for item in result['candidates']))

    async def test_explicit_shared_candidates_keep_all_product_ids(self):
        class Gateway:
            async def fetch_page(self, url): return {'url': url, 'html': '<a href="/shared.pdf">共用条款（适用于产品甲、产品乙）</a>', 'contentType': 'text/html'}
        result = await CompanyAdapter('shared', Gateway()).discover_documents({'id': 1, 'name': '示例', 'officialSiteUrl': 'https://official.example/disclosure'}, [{'id': 1, 'name': '产品甲'}, {'id': 2, 'name': '产品乙'}])
        self.assertEqual(result['candidates'][0]['productIds'], [1, 2])
        self.assertTrue(result['candidates'][0]['shared'])
