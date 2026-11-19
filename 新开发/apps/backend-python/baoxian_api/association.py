import base64
import json
import secrets
import time
from datetime import datetime, timezone

import httpx
from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes


KEY = b'0d36c68466e06b99'
IV = b'0840e274812143f5'


class AssociationGatewayError(Exception):
    def __init__(self, kind, message):
        super().__init__(message)
        self.kind = kind


def encrypt_payload(value):
    raw = value.encode('utf-8')
    raw += bytes((-len(raw)) % 16)
    encryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).encryptor()
    return base64.b64encode(encryptor.update(raw) + encryptor.finalize()).decode('ascii').replace('+', '_')


def authorization_token():
    return encrypt_payload(str(int(time.time() * 1000)) + ''.join(str(secrets.randbelow(10)) for _ in range(13)))


def decrypt_payload(value):
    decryptor = Cipher(algorithms.AES(KEY), modes.CBC(IV)).decryptor()
    encrypted = base64.b64decode(value.replace('_', '+'))
    return (decryptor.update(encrypted) + decryptor.finalize()).rstrip(b'\0').decode('utf-8')


class AssociationGateway:
    def __init__(self, client, base_url='https://tiaokuan.iachina.cn/sinopipi', site_url='https://tiaokuan.iachina.cn/#/index', timeout=120, authorization_factory=authorization_token):
        self.client = client
        self.base_url = base_url.rstrip('/')
        self.site_url = site_url
        self.timeout = timeout
        self.authorization_factory = authorization_factory

    async def _request(self, method, path, **kwargs):
        try:
            response = await self.client.request(method, self.base_url + path, timeout=self.timeout, **kwargs)
        except httpx.TimeoutException as error:
            raise AssociationGatewayError('timeout', '协会请求超时') from error
        except httpx.RequestError as error:
            raise AssociationGatewayError('network', '协会请求失败') from error
        if not response.is_success:
            raise AssociationGatewayError('http', f'协会接口返回 HTTP {response.status_code}')
        if 'application/json' not in response.headers.get('content-type', ''):
            raise AssociationGatewayError('invalid_response', '协会接口返回的不是 JSON（可能是错误网页）')
        try:
            body = response.json()
            if not isinstance(body, dict):
                raise ValueError('not an object')
        except ValueError as error:
            raise AssociationGatewayError('invalid_response', '协会接口返回无法解析的响应') from error
        if str(body.get('code')) != '200':
            raise AssociationGatewayError('source', f"协会接口返回错误：{body.get('msg', body.get('code'))}")
        return body

    async def list_categories(self):
        body = await self._request('GET', '/syscodeEntity/queryCodeType?codeType=PubNewProdTypeCode', headers={'accept': 'application/json'})
        if not isinstance(body.get('data'), list):
            raise AssociationGatewayError('invalid_response', '协会类别响应缺少 data 数组')
        categories = []
        for row in body['data']:
            if not isinstance(row, dict) or not isinstance(row.get('codeKey'), str) or not isinstance(row.get('value'), str):
                raise AssociationGatewayError('invalid_response', '协会类别项缺少 codeKey 或 value')
            categories.append({'code': row['codeKey'], 'name': row['value']})
        return categories

    async def list_product_page(self, category, page, page_size):
        payload = {key: '' for key in ('insperdtype', 'processnode', 'instype', 'prodpaytype', 'cplbtow', 'cplbthree', 'salestatus', 'cplbfour', 'sjlxone', 'sjlxtow', 'sjlxthree', 'proddesicode', 'prodname', 'inscomname', 'insitemcode', 'filltype', 'specialattri', 'insperd')}
        payload.update({'tagdata': '03', 'cplbone': category, 'prodtypecode': category, 'pageNum': page, 'pageSize': page_size})
        body = await self._request('POST', '/prodtermsinfo/selectConsumerList', headers={'content-type': 'application/json;charset=utf-8', 'accept': 'application/json', 'Authorization': self.authorization_factory()}, json=payload)
        if not isinstance(body.get('data'), str):
            raise AssociationGatewayError('invalid_response', '协会列表响应缺少加密 data')
        try:
            decoded = json.loads(decrypt_payload(body['data']))
        except (ValueError, UnicodeError, TypeError) as error:
            raise AssociationGatewayError('decrypt', '协会列表响应解密失败') from error
        if not isinstance(decoded, dict) or not isinstance(decoded.get('records'), list) or not isinstance(decoded.get('total'), (int, float)) or isinstance(decoded.get('total'), bool):
            raise AssociationGatewayError('invalid_response', '协会列表响应缺少 records 或 total')
        records = []
        for row in decoded['records']:
            if not isinstance(row, dict) or not isinstance(row.get('inscomname'), str) or not isinstance(row.get('prodname'), str):
                raise AssociationGatewayError('invalid_response', '协会列表记录缺少公司名或产品名')
            record = {'companyName': row['inscomname'], 'productName': row['prodname'], 'salesStatusRaw': row['salestatus'] if isinstance(row.get('salestatus'), str) else None}
            if isinstance(row.get('saledate'), str):
                record['saleDateRaw'] = row['saledate']
            if isinstance(row.get('termsno'), str):
                record['recordId'] = row['termsno']
            records.append(record)
        return {'records': records, 'total': decoded['total'], 'sourceUrl': self.site_url}


class SourceUnavailableError(Exception):
    code = 'source_unavailable'


class AssociationAdapter:
    def __init__(self, gateway, page_size=10):
        self.gateway = gateway
        self.page_size = page_size

    async def list_categories(self):
        try:
            return await self.gateway.list_categories()
        except Exception as error:
            raise SourceUnavailableError('读取协会分类失败') from error

    async def read_page(self, category, cursor=None):
        page = int(cursor) if cursor is not None else 1
        try:
            source = await self.gateway.list_product_page(category, page, self.page_size)
        except Exception as error:
            raise SourceUnavailableError('读取协会产品列表失败') from error
        fetched_at = datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')
        products = []
        statuses = {'在售': 'on_sale', '停售': 'off_sale', '停用': 'disabled'}
        for record in source['records']:
            raw = record['salesStatusRaw']
            product = {'companyName': record['companyName'], 'productName': record['productName'], 'salesStatus': statuses.get((raw or '').strip(), 'unknown'), 'salesStatusRaw': raw, 'sourceUrl': source['sourceUrl'], 'fetchedAt': fetched_at}
            for source_key, target_key in (('saleDateRaw', 'saleDateRaw'), ('recordId', 'sourceRecordId'), ('officialProductCode', 'officialProductCode'), ('filingNumber', 'filingNumber')):
                if source_key in record:
                    product[target_key] = record[source_key]
            products.append(product)
        result = {'products': products, 'reportedTotal': source['total']}
        if page * self.page_size < source['total']:
            result['nextCursor'] = str(page + 1)
        return result


class RateLimitedAssociationGateway:
    def __init__(self, gateway, limiter, host, interval_ms):
        self.gateway = gateway
        self.limiter = limiter
        self.host = host
        self.interval_ms = interval_ms

    async def list_categories(self):
        await self.limiter.wait(self.host, self.interval_ms())
        return await self.gateway.list_categories()

    async def list_product_page(self, category, page, page_size):
        await self.limiter.wait(self.host, self.interval_ms())
        return await self.gateway.list_product_page(category, page, page_size)
