import re
import unicodedata
from urllib.parse import urljoin, urlsplit

from bs4 import BeautifulSoup


def normalize_text(value):
    return re.sub(r'\s+', '', unicodedata.normalize('NFKC', value)).lower()


def detect_type(text):
    value = normalize_text(text)
    kinds = []
    if '现金价值' in value: kinds.append('cash_value_table')
    if '费率' in value: kinds.append('rate_table')
    if '说明书' in value or '产品说明' in value: kinds.append('prospectus')
    if '条款' in value: kinds.append('clause')
    return kinds[0] if len(kinds) == 1 else None


def matching_products(text, products):
    value = normalize_text(text)
    return [product for product in products if normalize_text(product['name']) and normalize_text(product['name']) in value]


def looks_like_product_page(url):
    path = urlsplit(url).path.lower()
    if re.search(r'list|disclosure|category|column|search|index|product(s)?/?$', path):
        return False
    return bool(re.search(r'info_itemid|/cpzx/|/products?/[^/]+\.(html|shtml)', path))


class CompanyAdapter:
    def __init__(self, key, gateway):
        self.key = key
        self.gateway = gateway

    async def discover_documents(self, company, products):
        entry_url = company.get('documentEntryUrl') or company.get('officialSiteUrl')
        if not entry_url:
            raise ValueError('公司未配置官网')
        entry = await self.gateway.fetch_page(entry_url)
        searched, candidates, failures = [entry['url']], [], []
        visited, candidate_keys, product_pages = set(), set(), {}

        def candidate(page, title, href, kind, matches, official=None, shared=False):
            url = urljoin(page['url'], href)
            if urlsplit(url).scheme not in ('http', 'https'):
                return
            ids = [product['id'] for product in matches]
            key = (url, page['url'], kind, tuple(ids))
            if key in candidate_keys:
                return
            candidate_keys.add(key)
            item = {'productIds': ids, 'type': kind, 'title': title.strip() or f'{kind} 文件', 'downloadUrl': url, 'sourcePageUrl': page['url'], 'snapshotHtml': page['html']}
            if official: item['officialProductUrl'] = official
            if shared: item['shared'] = True
            if title.strip(): item['sourceText'] = title.strip()
            candidates.append(item)

        async def scan_list(page):
            if page['url'] in visited:
                return
            visited.add(page['url'])
            soup = BeautifulSoup(page['html'], 'html.parser')
            next_url = None
            for element in soup.select('a[href]'):
                href = element.get('href')
                url = urljoin(page['url'], href)
                if urlsplit(url).scheme not in ('http', 'https'):
                    continue
                text = element.get_text()
                row = element.find_parent('tr')
                row_text = row.get_text() if row else ''
                context = text + ' ' + (row_text or element.parent.get_text())
                if re.search(r'下一页|下页|next|›|»', text, re.I) and url != page['url']:
                    next_url = url
                    continue
                kind = detect_type(text) or detect_type(context)
                if not kind and re.search(r'\.pdf(?:[?#]|$)', url, re.I) and re.search(r'条款|现金价值|费率|说明书|产品说明', context):
                    failures.append({'url': page['url'], 'message': f'资料类型无法确定：{url}'})
                    continue
                matches = matching_products(row_text or text, products)
                if kind:
                    if row_text and not matches:
                        continue
                    candidate(page, text, href, kind, matches, page['url'] if len(matches) == 1 and looks_like_product_page(page['url']) else None, len(matches) > 1 and bool(re.search(r'共用|通用|适用于', context)))
                elif len(matches) == 1 and not url.lower().endswith('.pdf') and url not in visited:
                    product_pages[url] = matches[0]
            if next_url and next_url not in visited:
                try:
                    next_page = await self.gateway.fetch_page(next_url)
                    searched.append(next_page['url'])
                    await scan_list(next_page)
                except Exception as error:
                    failures.append({'url': next_url, 'message': str(error)})

        await scan_list(entry)
        nav = []
        for element in BeautifulSoup(entry['html'], 'html.parser').select('a[href]'):
            url = urljoin(entry['url'], element.get('href'))
            text = element.get_text()
            if url == entry['url'] or url in visited or url.lower().endswith('.pdf') or not re.search(r'信息披露|公开信息|产品信息|产品目录|产品中心|资料下载|保险产品|产品列表|条款', text + ' ' + url):
                continue
            if url not in nav: nav.append(url)
        for url in nav:
            try:
                page = await self.gateway.fetch_page(url)
                searched.append(page['url'])
                await scan_list(page)
            except Exception as error:
                failures.append({'url': url, 'message': str(error)})
        for url, product in list(product_pages.items()):
            try:
                page = await self.gateway.fetch_page(url)
            except Exception as error:
                failures.append({'url': url, 'message': str(error)})
                continue
            searched.append(page['url'])
            for element in BeautifulSoup(page['html'], 'html.parser').select('a[href]'):
                title = element.get_text()
                kind = detect_type(title)
                if kind:
                    candidate(page, title, element.get('href'), kind, [product], page['url'] if looks_like_product_page(page['url']) else None)
        if not company.get('documentEntryUrl') and urlsplit(entry['url']).path == '/' and len(searched) == 1 and not candidates and not failures:
            failures.append({'url': entry['url'], 'message': '未找到可确认的产品资料目录，搜索范围未确认'})
        return {'candidates': candidates, 'searchedPages': searched, 'pageFailures': failures}
