import html
import json
import math
from urllib.parse import urlencode, urljoin, urlsplit

from .company_adapter import normalize_text

ENDPOINT = 'https://www.e-chinalife.com/jw/web/json/app/product_info_publish/plugin/com.chinalife.yunque.ProductInfoPublish/service'
FIELDS = (('clause', 'clause'), ('productdescription', 'prospectus'), ('ratestable', 'rate_table'), ('cashvalue', 'cash_value_table'))


class ChinaLifeAdapter:
    key = 'chinalife'

    def __init__(self, gateway): self.gateway = gateway

    async def discover_documents(self, company, products):
        candidates, searched, number = [], [], 1
        page_failures = []
        while True:
            url = ENDPOINT + '?' + urlencode({'pageSize': 15, 'page': number, 'type': 1})
            try:
                page = await self.gateway.fetch_page(url)
                data = json.loads(page['html'])
                try:
                    count = float(data.get('count'))
                except (TypeError, ValueError):
                    raise ValueError('中国人寿资料目录响应无效')
                if not isinstance(data.get('list'), list) or not math.isfinite(count):
                    raise ValueError('中国人寿资料目录响应无效')
            except Exception as error:
                if number == 1:
                    raise
                page_failures.append({'url': url, 'message': f'第 {number} 页读取失败：{error}'})
                break
            searched.append(page['url'])
            if not data['list'] and (number - 1) * 15 < count:
                page_failures.append({'url': url, 'message': f'第 {number} 页为空，但目录显示仍有结果'})
                break
            snapshot = '<pre>' + html.escape(page['html'], quote=False) + '</pre>'
            for row in data['list']:
                if not isinstance(row, dict) or not isinstance(row.get('productname'), str): continue
                matches = [product for product in products if normalize_text(product['name']) == normalize_text(row['productname'])]
                if not matches: continue
                for field, kind in FIELDS:
                    raw = row.get(field)
                    if not isinstance(raw, str) or not raw.strip() or raw == '/': continue
                    target = urljoin(page['url'], raw.strip())
                    if urlsplit(target).scheme not in ('http', 'https'): continue
                    candidates.append({'productIds': [product['id'] for product in matches], 'type': kind, 'title': f"{row['productname']} {kind}", 'downloadUrl': target, 'sourcePageUrl': page['url'], 'snapshotKind': 'api_response', 'sourceText': f"{row['productname']}: {field}", 'snapshotHtml': snapshot})
            if not data['list'] or number * 15 >= count: break
            number += 1
        return {'candidates': candidates, 'searchedPages': searched, 'pageFailures': page_failures}
