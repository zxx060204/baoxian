from urllib.parse import urlsplit

from .aia_adapter import AiaAdapter
from .chinalife_adapter import ChinaLifeAdapter
from .company_adapter import CompanyAdapter


def domain_matches(host, domain):
    return host == domain or host.endswith('.' + domain)


class CompanyRegistry:
    key = 'company-registry'

    def __init__(self, gateway):
        self.fallback = CompanyAdapter('company-official-site', gateway)
        self.adapters = {'aia': AiaAdapter(gateway), 'chinalife': ChinaLifeAdapter(gateway)}

    async def discover_documents(self, company, products):
        host = (urlsplit(company.get('officialSiteUrl') or '').hostname or '').lower()
        domains = (('aia', 'aia.com.cn'), ('chinalife', 'e-chinalife.com'),
                   ('chinalife', 'chinalife.com.cn'))
        key = next((name for name, domain in domains if domain_matches(host, domain)), None)
        adapter = self.adapters.get(key, self.fallback)
        result = await adapter.discover_documents(company, products)
        if adapter is not self.fallback and 'searchedProductIds' not in result and not result.get('pageFailures'):
            covered = {product_id for candidate in result['candidates'] for product_id in candidate['productIds']}
            missing = [product['name'] for product in products if product['id'] not in covered]
            if missing:
                pages = result.get('searchedPages') or []
                result['pageFailures'] = [{'url': pages[0] if pages else company.get('officialSiteUrl') or '',
                                           'message': '官网目录未确认覆盖以下产品的资料：' + '、'.join(missing)}]
        return result
