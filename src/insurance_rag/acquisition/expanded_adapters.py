"""Additional official insurers: contextual HTML, static JSON and public APIs.

Sample discovery is explicitly partial. Unknown sale status and body versions stay
unknown; footer, governance and company reports never become product documents.
"""
from __future__ import annotations
import collections,html,json,pathlib,re,urllib.parse
from html.parser import HTMLParser
from .transport import FetchError,now,digest

REGISTRY=json.loads((pathlib.Path(__file__).parent/'expanded_companies.json').read_text(encoding='utf8'))
DOC_RE=re.compile(r'\.(?:pdf|xlsx?|zip)(?:[?#]|$)',re.I)
PRODUCT_RE=re.compile(r'保险|寿险|年金')
EXCLUDE_RE=re.compile(r'隐私|个人信息|信息共享|使用条款|免责声明|营业执照|许可证|偿付能力|财务报告|精算报告|社会责任|关联交易|资金运用|公司治理')

def decode(body):
    if body.startswith(b'\x1f\x8b'):
        import gzip,io
        with gzip.GzipFile(fileobj=io.BytesIO(body)) as stream:body=stream.read(128*1024**2+1)
        if len(body)>128*1024**2:raise FetchError('size_limit','DECOMPRESSED_BODY_LIMIT')
    head=body[:4096].decode('ascii',errors='ignore');charset=re.search(r'charset\s*=\s*["\']?([\w-]+)',head,re.I)
    enc=charset.group(1) if charset else 'utf8'
    try:return body.decode(enc)
    except (UnicodeError,LookupError):
        try:return body.decode('utf8')
        except UnicodeError:return body.decode('gb18030',errors='replace')

def absolute(base,url):
    from . import adapters
    url=html.unescape(url.strip())
    p=urllib.parse.urlsplit(urllib.parse.urljoin(base,url))
    # Upgrade public HTTP links to HTTPS; never downgrade transport security.
    if p.scheme=='http':p=p._replace(scheme='https',netloc=p.netloc.removesuffix(':80'))
    query=urllib.parse.parse_qs(p.query)
    if p.path.endswith(('/pdf.html','/viewer.html')) and query.get('file'):
        return absolute(base,query['file'][0].split('?oldName=',1)[0])
    return adapters.legacy.quote_url(urllib.parse.urlunsplit(p))

class LinkParser(HTMLParser):
    def __init__(self,base):super().__init__(convert_charrefs=True);self.base=base;self.links=[];self.stack=[];self.iframes=[];self.scripts=[];self.refresh=[]
    def handle_starttag(self,tag,attributes):
        a=dict(attributes)
        if tag in ('a','option'):
            url=a.get('href') if tag=='a' else a.get('value');url=url or ''
            if not url or url.startswith('javascript:'):
                action=a.get('onclick','')
                found=re.search(r'["\']([^"\']+\.(?:pdf|xlsx?|zip)(?:\?[^"\']*)?)["\']',action,re.I)
                url=found.group(1) if found else ''
            self.stack.append(dict(tag=tag,url=url,label='',attributes=a))
        elif tag=='iframe' and a.get('src'):self.iframes.append(absolute(self.base,a['src']))
        elif tag=='script' and a.get('src'):self.scripts.append(absolute(self.base,a['src']))
        elif tag=='meta' and (a.get('http-equiv') or '').lower()=='refresh':
            m=re.search(r'url\s*=\s*(.+)',a.get('content',''),re.I)
            if m:self.refresh.append(absolute(self.base,m.group(1).strip('"\'')))
    def handle_data(self,data):
        for item in self.stack:item['label']+=data+' '
    def handle_endtag(self,tag):
        if not self.stack or self.stack[-1]['tag']!=tag:return
        item=self.stack.pop();item['label']=' '.join(item['label'].split()) or item['attributes'].get('title','')
        if item['url'] and not item['url'].startswith(('javascript:','#','mailto:','tel:')):
            item['url']=absolute(self.base,item['url']);self.links.append(item)

def parse_links(text,base):
    parser=LinkParser(base);parser.feed(text);return parser

def category(label):
    from . import adapters
    if EXCLUDE_RE.search(label):return None
    if '投保告知' in label:return '投保规则与告知'
    if '风险提示' in label:return '投资账户与风险信息'
    if re.search(r'药店清单|药品清单|医疗机构清单',label):return '医疗服务与增值权益'
    if '产品分类' in label or '风险等级' in label:return '产品分类与风险等级'
    if '产品目录' in label or '产品清单' in label or '互联网保险产品信息' in label:return '产品目录与销售状态'
    return adapters.material_kind(label)

def product_name(text):
    from . import adapters
    text=adapters.clean(text);text=re.sub(r'\s+P[1-5]\s+20\d{2}.*$','',text)
    return re.sub(r'\s+','',re.sub(r'\s*\.{2,}\s*$','',text)).rstrip('.')

def candidate(company,name,kind,url,base,link,**extra):
    from . import adapters
    name=product_name(name)
    scope='company' if kind in ('产品目录与销售状态','产品变更与停售公告') else 'product'
    r=adapters.make(company,name,kind,absolute(base,url),base,link,identity=name,scope=scope,fetch_mode='file',sale_status=extra.pop('sale_status','not_verified'),**extra)
    r.update(body_version_status='not_verified',product_label_complete=not bool(re.search(r'\.\.\.|…',name)))
    return r

def parse_html(company,text,base):
    from . import adapters
    text=re.sub(r'<!--.*?-->','',text,flags=re.S);items=[];table_urls=set()
    if company=='工银安盛人寿':
        start=text.find('在售保险产品');end=text.find('停售及变更产品',max(start,0))
        if start>=0:text=text[start:end if end>=0 else None]
    for table in re.findall(r'<table\b[^>]*>(.*?)</table>',text,re.I|re.S):
        headers=[adapters.clean(t) for t in re.findall(r'<th\b[^>]*>(.*?)</th>',table,re.I|re.S)]
        for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table,re.I|re.S):
            cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.I|re.S)
            if not cells:continue
            names=[product_name(c) for c in cells if PRODUCT_RE.search(adapters.clean(c)) and len(adapters.clean(c))<230 and not EXCLUDE_RE.search(adapters.clean(c))]
            name=next((n for n in names if len(n)>5 and n not in ('保险产品','保险产品名称','保险名称','产品条款','产品说明书','保险条款')),None)
            if not name:continue
            # Name cells occasionally include dropdown labels; keep separate identity.
            name=re.split(r'请选择|产品条款|条款PDF|产品费率表|产品说明书|产品现金价值表',name)[0].strip()
            if not PRODUCT_RE.search(name):continue
            for index,cell in enumerate(cells):
                if index<len(headers) and '历史版本' in headers[index]:
                    table_urls.update(link['url'] for link in parse_links(cell,base).links)
                    continue
                for link in parse_links(cell,base).links:
                    if not DOC_RE.search(link['url']):continue
                    label=link['label'];kind=category(label) or (category(headers[index]) if index<len(headers) else None)
                    if not kind and ('条款链接' in ''.join(headers) or '条款名称' in ''.join(headers)):kind='条款'
                    if not kind:continue
                    state='在售' if re.search(r'在售|在销售',adapters.clean(tr)) else 'not_verified'
                    # Explicit discontinued rows stay outside the current candidate set.
                    status_text=adapters.clean(' '.join(cells[:2])) if company=='工银安盛人寿' else adapters.clean(tr)
                    if re.search(r'停售|已停用|停用日期|已注销',status_text):continue
                    row=dict(label=label,cells=[adapters.clean(c) for c in cells],headers=headers)
                    items.append(candidate(company,name,kind,link['url'],base,row,sale_status=state));table_urls.add(link['url'])
    parser=parse_links(text,base)
    for link in parser.links:
        label=link['label']
        if not DOC_RE.search(link['url']) or link['url'] in table_urls:continue
        kind=category(label)
        if not kind:continue
        if label in ('保险产品条款信息','保险产品条款','保险产品','产品条款','保险条款'):
            continue
        if kind in ('条款','产品说明书','费率表','现金价值表') and not PRODUCT_RE.search(label):continue
        name=re.sub(r'(?:产品说明书|产品说明|费率表|现金价值表|条款)$','',label).replace('保险条款','保险')
        items.append(candidate(company,name,kind,link['url'],base,link))
    return items,parser

def finish(company,items,pages,limit=None,errors=None):
    unique={};products={}
    for r in items:
        key=(r['product_identity'],r['category'],r['url']);unique.setdefault(key,r)
    items=list(unique.values())
    for r in items:
        if r.get('scope')=='product':products.setdefault(r['product_identity'],dict(product_name=r['product_name'],product_identity=r['product_identity'],sale_status=r.get('sale_status'),source_row=r['source_row']))
    if limit:
        chosen=set(list(products)[:limit]);items=[r for r in items if r['product_identity'] in chosen or r.get('scope')=='company'];products={k:v for k,v in products.items() if k in chosen}
    return dict(company=company,products=list(products.values()),candidates=items,pages=pages,discovery_errors=errors or [],catalog_scope='sample_partial' if limit else 'inspected_sources_not_guaranteed_exhaustive',discovery_complete=False,sample_product_limit=limit,scope_note='Product documents only; ordinary webpage access is not download success. Sale status and body versions remain unverified unless explicit.')


def discover(company,client):
    cfg=REGISTRY[company];limit=getattr(client,'sample_products',None)
    if company=='中国人寿':
        from .public_api_adapters import discover as api_discover
        return api_discover(company,client,limit)
    queue=collections.deque(cfg.get('seeds') or [cfg['url']]);seen=set();items=[];pages=[];errors=[]
    while queue:
        url=queue.popleft()
        if url in seen or not client.transport.allowed(url):continue
        seen.add(url)
        try:
            body,meta=client.get(url,catalog=True);base=meta['final_url'];text=decode(body)
            if body.startswith(b'%PDF-'):
                # Explicit catalog-document seeds preserve company scope, not fake products.
                label=cfg.get('seed_labels',{}).get(url)
                if label:items.append(candidate(company,label,category(label) or '产品目录与销售状态',url,url,dict(configured_document_seed=True)))
                continue
            from . import crawler
            if crawler.challenge(body):raise FetchError('challenge','CHALLENGE_ON_DISCLOSURE_PAGE',stop=True)
            found,parser=parse_html(company,text,base)
            items+=found;pages.append(dict(url=url,final_url=base,document_candidates=len(found),bytes=len(body)))
            if limit and len({r['product_identity'] for r in items if r['scope']=='product'})>=limit:break
            for nxt in parser.refresh+parser.iframes:
                if '/tscp/' not in nxt and '/tbcp/' not in nxt:queue.append(nxt)
            routes=cfg.get('route_patterns') or [r'产品基本信息|保险产品目录|产品目录|公司产品基本信息|在售产品|产品条款|条款PDF|^下一页$|^>$']
            for link in parser.links:
                if DOC_RE.search(link['url']) or EXCLUDE_RE.search(link['label']):continue
                if any(re.search(p,link['label']) for p in routes):queue.append(link['url'])
            # Sampling bounds page exploration, not production product counts.
            if limit and len(pages)>=cfg.get('sample_page_budget',12):break
        except FetchError as exc:
            errors.append(dict(url=url,kind=exc.kind,error=str(exc),attempts=exc.attempts))
            if exc.stop:raise
    return finish(company,items,pages,limit,errors)

