import html
import json
import re
import secrets
import time
import unicodedata
from urllib.parse import urlencode, urlsplit

from .association import decrypt_payload

BASE = 'https://icidp.iachina.cn'
COLUMN = '2015111318230001'


def name_key(value):
    return re.sub(r'[\s·•・]', '', unicodedata.normalize('NFKC', value))


def official_origin(value):
    try:
        parsed = urlsplit(value if re.match(r'^https?://', value, re.I) else 'https://' + value)
        host = (parsed.hostname or '').lower()
        if parsed.scheme not in ('http', 'https') or parsed.username or parsed.password or '.' not in host or re.match(r'^(localhost|\d+\.|\[)', host) or host.endswith('.local') or host == 'iachina.cn' or host.endswith('.iachina.cn'):
            return None
        port = parsed.port
        netloc = host + (f':{port}' if port is not None and not (parsed.scheme == 'http' and port == 80 or parsed.scheme == 'https' and port == 443) else '')
        return parsed.scheme + '://' + netloc
    except ValueError:
        return None


class CompanyDisclosureGateway:
    def __init__(self, client, wait_for_request=None, base_url=BASE):
        self.client = client
        self.wait_for_request = wait_for_request
        self.base_url = base_url.rstrip('/')

    async def _wait(self):
        if self.wait_for_request is not None:
            await self.wait_for_request()

    async def find(self, company_name):
        client_id = secrets.token_hex(16) + str(int(time.time() * 1000))
        await self._wait()
        home = await self.client.get(self.base_url + '/?columnid_url=201509301401', timeout=15)
        if not home.is_success:
            raise RuntimeError(f'协会披露首页请求失败：HTTP {home.status_code}')
        headers = {'Content-Type': 'application/x-www-form-urlencoded; charset=UTF-8', 'Accept-Encodings': client_id, 'timestamp': str(int(time.time() * 1000)), 'Referer': str(home.url), 'X-Requested-With': 'XMLHttpRequest'}

        async def post(path, data=None):
            await self._wait()
            response = await self.client.post(self.base_url + path, headers=headers, data=data or {}, timeout=20)
            if not response.is_success:
                raise RuntimeError(f'协会披露请求失败：HTTP {response.status_code}')
            encoding = 'gb18030' if 'gbk' in response.headers.get('content-type', '').lower() else 'utf-8'
            return response.content.decode(encoding, errors='replace')

        check = json.loads(await post('/front/captchaCheck.do', {'clientIdCard': client_id}))
        if not check.get('msg'):
            raise RuntimeError('协会披露验证状态无法解析')
        decoded = json.loads(decrypt_payload(check['msg']))
        if decoded.get('clientIpStatus') != 'success':
            raise RuntimeError('协会披露系统要求人机验证，请处理后重试')
        page = await post(f'/front/leafColComType.do?columnid={COLUMN}')
        pattern = r"<a\s+[^>]*onclick=\"company0\('([^']+)','([^']+)','([^']+)'\);\"[^>]*>([^<]+)</a>"
        entries = list(re.finditer(pattern, page))
        total = len(re.findall(r'onclick=\"company0\(', page))
        if not total or len(entries) != total:
            raise RuntimeError('协会人身险披露目录解析不完整')
        matches = [entry for entry in entries if name_key(entry.group(4)) == name_key(company_name)]
        if len(matches) != 1:
            return {'kind': 'unavailable', 'reason': '协会人身险披露目录存在多个同名公司，需人工核实' if matches else '协会人身险披露目录未找到该公司全称'}
        match = matches[0]
        params = urlencode({'columnid': COLUMN, 'comCode': match.group(1), 'attr': '03', 'informationno': match.group(2), 'zj': match.group(3)})
        path = '/front/getInterNetCompanyInfo.do?' + params
        detail = await post(path)
        detail = re.sub(r'<(script|style)\b[\s\S]*?</\1>', ' ', detail, flags=re.I)
        text = re.sub(r'\s+', ' ', html.unescape(re.sub(r'<[^>]+>', ' ', detail))).strip()
        found = re.search(r'公司官网链接\s*[:：]\s*(\S+)', text)
        url = official_origin(found.group(1)) if found else None
        return {'kind': 'found', 'url': url, 'sourceUrl': self.base_url + path} if url else {'kind': 'unavailable', 'reason': '协会披露详情未提供可用的公司官网链接'}
