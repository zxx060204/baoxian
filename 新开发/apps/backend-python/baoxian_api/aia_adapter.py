import hashlib
import html
import json
import re
import uuid
from urllib.parse import urljoin, urlsplit

from .company_adapter import normalize_text

ENDPOINT = 'https://cws.aia.com.cn/mypage/productpublish/list'
DOCUMENTS = (('productItem', 'clause'), ('productInstruction', 'prospectus'), ('ratesTable', 'rate_table'), ('cashValueTable', 'cash_value_table'))
STATUS_QUERIES = (('在售', {'on_sale'}), ('停售', {'stopped', 'off_sale', 'disabled'}))


def _row_matches_status(row, requested_status):
    source_status = row.get('productStatus')
    if not source_status:
        return True
    if source_status == '在售':
        return requested_status == '在售'
    if source_status in ('停售', '停用'):
        return requested_status == '停售'
    return False


class AiaAdapter:
    key = 'aia'

    def __init__(self, gateway, request_id_factory=None):
        self.gateway = gateway
        self.request_id_factory = request_id_factory or (lambda: str(uuid.uuid4()))

    async def discover_documents(self, company, products):
        candidates, searched, found = [], [], set()
        searchable = [product for product in products if any(product.get('salesStatus') in statuses for _, statuses in STATUS_QUERIES)]
        searchable_ids = {product['id'] for product in searchable}
        unsearched = [product for product in products if product['id'] not in searchable_ids]
        searched_product_ids = set()
        page_failures = []
        searched_statuses = []
        for source_status, accepted_product_statuses in STATUS_QUERIES:
            status_products = [product for product in products if product.get('salesStatus') in accepted_product_statuses]
            if not status_products:
                continue
            status_product_ids = [product['id'] for product in status_products]
            index = 0
            while True:
                body = json.dumps({'like': '', 'status': source_status, 'page': {'pageSize': 100, 'index': index}}, ensure_ascii=False, separators=(',', ':'))
                request_id = self.request_id_factory()
                signature = hashlib.md5(('1qaz@WSX#$%&#' + ''.join(sorted(body)) + '##' + request_id).encode('utf-8')).hexdigest().upper()
                request = {'method': 'POST', 'body': body, 'headers': {'content-type': 'application/json', 'origin': 'https://www.aia.com.cn', 'x-request-id': request_id, 'x-crp-sign': signature}}
                try:
                    page = await self.gateway.fetch_page(ENDPOINT, request)
                    parsed = json.loads(page['html'])
                    published = parsed.get('product_publish')
                    if str(parsed.get('success')).lower() != 'true' or not isinstance(published, dict) or not isinstance(published.get('records'), list):
                        raise ValueError('友邦资料目录响应无效')
                    total_pages = int(published.get('totalPage') or 1)
                    rows = published['records']
                except Exception as error:
                    page_failures.append({'url': ENDPOINT, 'message': f'友邦{source_status}目录第 {index + 1} 页读取失败：{error}', 'productIds': status_product_ids})
                    break
                searched.append(page['url'])
                if source_status not in searched_statuses:
                    searched_statuses.append(source_status)
                searched_product_ids.update(status_product_ids)
                if not rows and total_pages > 1 and index < total_pages:
                    page_failures.append({'url': ENDPOINT, 'message': f'友邦{source_status}目录第 {index + 1} 页为空，但目录显示仍有结果', 'productIds': status_product_ids})
                    break
                snapshot = '<pre>' + html.escape(page['html'], quote=False) + '</pre>'
                for row in rows:
                    if not isinstance(row, dict) or not isinstance(row.get('productName'), str) or not _row_matches_status(row, source_status):
                        continue
                    matched = [product for product in status_products if normalize_text(product['name']) == normalize_text(row['productName'])]
                    if not matched:
                        continue
                    found.update(product['id'] for product in matched)
                    for field, kind in DOCUMENTS:
                        raw = row.get(field)
                        if not isinstance(raw, str) or not raw.strip(): continue
                        value = raw.strip()
                        if not re.search(r'\.pdf(?:[?#]|$)', value, re.I): value += '.pdf'
                        url = urljoin('https://www.aia.com.cn/content/dam/cn/zh-cn/docs/public-disclosure/', value)
                        if urlsplit(url).scheme not in ('http', 'https'): continue
                        candidates.append({'productIds': [product['id'] for product in matched], 'type': kind, 'title': f"{row['productName']} {kind}", 'downloadUrl': url, 'sourcePageUrl': page['url'], 'snapshotKind': 'api_response', 'sourceText': f"{row['productName']} ({source_status}): {field}", 'snapshotHtml': snapshot})
                index += 1
                if not rows or index >= total_pages: break
        return {
            'candidates': candidates,
            'searchedPages': searched,
            'searchedSalesStatuses': searched_statuses,
            'searchedProductIds': [product['id'] for product in searchable if product['id'] in searched_product_ids],
            'notFoundProductIds': [product['id'] for product in searchable if product['id'] in searched_product_ids and product['id'] not in found and all(product['id'] not in failure['productIds'] for failure in page_failures)],
            'unsearchedProductIds': [product['id'] for product in unsearched],
            'pageFailures': page_failures,
        }
