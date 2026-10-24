"""Official disclosure adapters; preserve source rows and association evidence."""
from __future__ import annotations
import hashlib, html, importlib.util, json, pathlib, re, urllib.parse
from .transport import FetchError, digest, now

from . import core_sources as legacy
CORE=['条款','产品说明书','费率表','现金价值表']
NEW={
 'CPIC太平洋人寿':dict(url='https://www.cpic.com.cn/xrsbx/gkxxpl/jbxx/gsgk/jydbxcpmljtk/',domains=['cpic.com.cn','cpiccdn.com']),
 'NewChina新华保险':dict(url='https://www.newchinalife.com/node/372',domains=['newchinalife.com']),
 'Taikang泰康人寿':dict(url='https://www.taikanglife.com/publicinfonew/basicnew/pubproduct/commonprodinfonew/list_542_1.html',domains=['taikanglife.com','taikang.com']),
}
COMPANIES={k:dict(url=v,domains=legacy.DOMAINS[k]) for k,v in legacy.TARGETS.items()}; COMPANIES.update(NEW)
EXPANDED=json.loads((pathlib.Path(__file__).parent/'expanded_companies.json').read_text(encoding='utf8'))
COMPANIES.update(EXPANDED)
DEFAULT_COMPANIES=list(COMPANIES)

def clean(text):return legacy.clean(text)
def normalize_name(text):return re.sub(r'(?:产品说明书|产品说明|产品条款|保险费率表|费率表|现金价值全表|现金价值表)$','',re.sub(r'\s+','',clean(text)))
def attrs(text):return {k.lower():html.unescape(v) for k,q,v in re.findall(r'([\w:-]+)\s*=\s*(["\'])(.*?)\2',text,re.S)}
def anchors(text,base):
    result=[]
    for attr,body in re.findall(r'<a\b([^>]*)>(.*?)</a>',text,re.I|re.S):
        a=attrs(attr); url=a.get('href','')
        if not url or url.startswith(('javascript:','#','mailto:')):continue
        result.append(dict(url=legacy.quote_url(urllib.parse.urljoin(base,url)),label=clean(body) or a.get('title') or a.get('aria-label',''),attributes=a))
    return result
def material_kind(label,url=''):
    text=clean(label)
    for kind,pattern in [
      ('产品变更与停售公告',r'停售|停止销售|终止销售|停用|变更.*公告|产品公告'),('投保规则与告知',r'投保须知|投保提示|健康告知|职业分类|投保规则'),
      ('红利实现率',r'红利实现率'),('结算利率公告',r'结算利率|累积利率|累积.*利率|保单贷款利率'),
      ('投资账户与风险信息',r'账户.*风险|风险等级|投资账户.*(?:价格|信息)|账户单位价格'),('医疗服务与增值权益',r'服务手册|直付医院|医院网络|增值服务|健康管理服务'),
      ('备案产品清单表',r'备案.*清单'),('总精算师声明书',r'精算师声明'),('法律责任人声明书',r'法律责任人声明'),
      ('现金价值表',r'现金价值'),('费率表',r'费率|保费表|基本保险金额表|保障费用表'),('产品说明书',r'说明书|产品说明|产品介绍'),('条款',r'条款|合同文本')]:
        if re.search(pattern,text):return kind
    return None

def make(company,name,kind,url,source,row=None,identity=None,scope='product',**extra):
    url=legacy.quote_url(url); name=clean(name)
    return dict(company=company,product_name=name,product_identity=str(identity or normalize_name(name)),category=kind,url=url,source_catalog=source,source_row=row or {},scope=scope,association_level='official_catalog_row' if scope=='product' else 'company_or_series_requires_product_linkage',**extra)

class Client:
    def __init__(self,company,transport,reuse_catalog=True):
        self.company=company; self.transport=transport; self.evidence=transport.evidence; self.reuse_catalog=reuse_catalog; self.catalog_cache={}
        for p in sorted(self.evidence.glob('request_*.json'),key=lambda p:p.stat().st_mtime):
            if not p.with_suffix('.bin').exists():continue
            try:
                meta=json.loads(p.read_text(encoding='utf8'))
                if meta.get('http_status')==200:self.catalog_cache[self.cache_key(meta['url'],meta.get('payload'),meta.get('form'))]=(p.with_suffix('.bin'),meta)
            except (ValueError,KeyError):continue
    @staticmethod
    def cache_key(url,payload=None,form=None):return json.dumps([url,payload,form],sort_keys=True,ensure_ascii=False)
    def get(self,url,**kwargs):
        key=self.cache_key(url,kwargs.get('payload'),kwargs.get('form'))
        if kwargs.get('catalog') and self.reuse_catalog and key in self.catalog_cache:
            p,meta=self.catalog_cache[key]; body=p.read_bytes()
            if digest(body)==meta['sha256'] and self.transport.allowed(meta['final_url']):return body,dict(meta,catalog_cache_source=str(p))
        return self.transport.fetch(url,**kwargs)
    def api(self,url,**kwargs):
        body,meta=self.get(url,catalog=True,**kwargs)
        try: data=json.loads(body.decode('utf-8-sig'))
        except Exception as exc:raise FetchError('non_json','CATALOG_EXPECTED_JSON; saved body requires review') from exc
        return data

def parse_cpic(text,base,company='CPIC太平洋人寿'):
    items=[]; products=[]
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',text,re.I|re.S):
        cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.I|re.S)
        if len(cells)<5:continue
        first=anchors(cells[1],base)
        if not first:continue
        name=first[0]['attributes'].get('title') or first[0]['label']
        if not any(word in name for word in ('保险','寿险','年金')):continue
        row=dict(product_name=name,product_identity=name,source_row={'cells':[clean(c) for c in cells],'sale_status':'current_sale_iframe'})
        products.append(row)
        for index,kind in [(1,'条款'),(2,'费率表'),(3,'现金价值表'),(4,'产品说明书')]:
            for link in anchors(cells[index],base):
                wrapper=link['url']; query=urllib.parse.parse_qs(urllib.parse.urlsplit(wrapper).query)
                target=(query.get('file') or [wrapper])[0]
                # PDF viewers wrap a file URL; oldName is presentation metadata.
                target=urllib.parse.urljoin(base,target).split('?oldName=',1)[0]
                if re.search(r'\.(pdf|xlsx?|zip)(?:[?#]|$)',target,re.I): items.append(make(company,name,kind,target,base,{'label':link['label'],'viewer_url':wrapper},identity=name))
    return products,items

def parse_taikang(text,base):
    company='Taikang泰康人寿'; items=[]; products={}
    match=re.search(r'<table\b[^>]*id=["\']tb_tab_1["\'][^>]*>(.*?)</table>',text,re.I|re.S)
    if not match:raise ValueError('TAIKANG_CURRENT_SALE_TABLE_NOT_FOUND')
    kinds={'clause':'条款','direction':'产品说明书','rate':'费率表','cashValue':'现金价值表'}
    for attr,label in re.findall(r'<option\b([^>]*)>(.*?)</option>',match.group(1),re.I|re.S):
        a=attrs(attr); name=a.get('productname'); url=a.get('value'); kind=kinds.get(a.get('filetype'))
        if not (name and url and kind) or a.get('enabled')!='Y':continue
        key=a.get('name') or name; products[key]=dict(product_name=name,product_identity=key,source_row=a)
        items.append(make(company,name,kind,urllib.parse.urljoin(base,url),base,a,identity=key))
    return list(products.values()),items

def parse_newchina(text,base):
    company='NewChina新华保险'; items=[]; products=[]
    for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',text,re.I|re.S):
        cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.I|re.S)
        if len(cells)<5 or clean(cells[3])!='在售':continue
        name=clean(cells[1]); links=anchors(cells[4],base)
        if not name or not links:continue
        key=None
        for link in links:
            key=(urllib.parse.parse_qs(urllib.parse.urlsplit(link['url']).query).get('riskCode') or [None])[0]
            if key:break
        key=key or name; products.append(dict(product_name=name,product_identity=key,source_row={'cells':[clean(c) for c in cells],'riskCode':key,'sale_status':'在售'}))
        for link in links:
            kind=material_kind(link['label'])
            if kind not in CORE:continue
            mode='file' if re.search(r'\.(pdf|xlsx?|zip)(?:[?#]|$)',link['url'],re.I) else 'detail_page'
            items.append(make(company,name,kind,link['url'],base,link,identity=key,fetch_mode=mode))
    return products,items


def discover(company,client,refresh=False):
    source=COMPANIES[company]['url']; pages=[]
    if company in legacy.TARGETS:
        cat=legacy.discover(company,client)
        for p in cat['products']:
            p.setdefault('product_identity',p.get('identity') or p['product_name'])
        if company=='PingAn平安':
            for product in cat['products']:
                row=product['source_row']; code=row.get('actualPlanCode') or row.get('planCode'); version=row.get('versionNo')
                for flag,typ,kind in [('recordList','4','备案产品清单表'),('actuaryInstruction','5','总精算师声明书'),('legalPerson','6','法律责任人声明书'),('approveList','8','审批产品清单表及申请表')]:
                    if str(row.get(flag))=='1' and '/' not in str(code):
                        url='https://life.pingan.com/ilife-home/product/getPlanClausePdf?'+urllib.parse.urlencode(dict(planCode=code,versionNo=version,attachmentType=typ))
                        cat['candidates'].append(make(company,row['clauseName'],kind,url,source,row,identity=str(code)+'@'+str(version),product_code=code,versionNo=version))
        if company=='Allianz安联':
            for p in cat['products']:
                row=p['source_row']
                if row.get('suspensionNoticeUrl'):cat['candidates'].append(make(company,p['product_name'],'产品变更与停售公告',urllib.parse.urljoin(source,row['suspensionNoticeUrl']),source,row,identity=p['product_identity']))
        if company=='HSBC汇丰':
            # Some reused panel identifiers pointed to cash-value links twice.
            # The official attachment label has precedence over positional panel IDs.
            for item in cat['candidates']:
                labelled=material_kind(item.get('source_row',{}).get('label',''))
                if labelled in CORE and labelled!=item['category']:
                    item['category_correction']=dict(previous=item['category'],reason='official_link_label_over_panel_position')
                    item['category']=labelled
    elif company=='Taikang泰康人寿':
        body,meta=client.get(source,catalog=True); text=body.decode('utf8',errors='replace'); products,items=parse_taikang(text,source)
        cat=dict(products=products,candidates=items,pages=[dict(url=source,rows=len(products),status='all_current_sale_table_options')])
    elif company=='CPIC太平洋人寿':
        body,meta=client.get(source,catalog=True); text=body.decode('utf8',errors='replace')
        roots=list(dict.fromkeys(urllib.parse.urljoin(source,u) for u in re.findall(r'<iframe\b[^>]*src=["\']([^"\']*/zbcp/[^"\']+)["\']',text,re.I)))
        if not roots:raise ValueError('CPIC_CURRENT_SALE_IFRAMES_NOT_FOUND')
        products=[]; items=[]
        for first in roots:
            url=first; seen=set()
            while url and url not in seen:
                seen.add(url); body,meta=client.get(url,catalog=True); text=body.decode('utf8',errors='replace')
                found,docs=parse_cpic(text,url); products+=found; items+=docs; pages.append(dict(url=url,rows=len(found)))
                nxt=[a['url'] for a in anchors(text,url) if a['label']=='下一页' and a['url']!=url]
                url=nxt[0] if nxt else None
        cat=dict(products=products,candidates=items,pages=pages)
    elif company=='NewChina新华保险':
        products=[]; items=[]; url=source; seen=set()
        while url and url not in seen:
            seen.add(url); body,meta=client.get(url,catalog=True); text=body.decode('utf8',errors='replace')
            found,docs=parse_newchina(text,url); products+=found; items+=docs; pages.append(dict(url=url,rows=len(found)))
            nextlinks=[a['url'] for a in anchors(text,url) if a['label']=='>' and '/node/372' in a['url']]
            url=nextlinks[0] if nextlinks else None
        cat=dict(products=products,candidates=items,pages=pages,scope='all official directory pages inspected, only rows explicitly 在售 selected')
    elif company in EXPANDED:
        from . import expanded_adapters
        cat=expanded_adapters.discover(company,client)
    else:raise ValueError('UNKNOWN_COMPANY')
    products={str(p.get('product_identity') or p.get('identity') or p['product_name']):p for p in cat['products']}
    items={}
    for row in cat['candidates']:
        row.setdefault('scope','product'); row.setdefault('association_level','official_catalog_row')
        row['id']=digest(json.dumps([company,row['product_identity'],row['category'],row['url'],row.get('version_or_date'),row.get('versionNo')],ensure_ascii=False).encode())[:24]
        items.setdefault(row['id'],row)
    cat.update(company=company,discovered_at=now(),products=list(products.values()),candidates=list(items.values()),product_limit=None,products_per_kind_limit=0,scope_note='Returned and inspected official source scope; not a guarantee of all products or body versions')
    return cat

def supplement_entrypoints(company,text,base):
    # Deduplicate identical kind/URL routes; no material count limit.
    result={}
    for a in anchors(text,base):
        kind=material_kind(a['label'])
        if kind and kind not in CORE and ('information' in a['url'] or 'publicinfo' in a['url'] or 'xinxi' in a['url'] or 'docs/' in a['url'] or re.search(r'\.(pdf|xlsx?)',a['url']) or '/node/' in a['url'] or 'products/' in a['url']):
            result.setdefault((kind,a['url']),a)
    return [dict(a,category=k) for (k,u),a in result.items()]

def resolve_detail(text,base):
    # NewChina details contain version tables: only the row with empty retirement date
    # is current; retain historical rows in HTML evidence without mixing versions.
    links=[]
    for table in re.findall(r'<table\b[^>]*>(.*?)</table>',text,re.I|re.S):
        if '停用时间' not in table:continue
        for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',table,re.I|re.S):
            cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.I|re.S)
            if len(cells)<3 or clean(cells[-1]):continue
            for a in anchors(cells[1],base):
                if re.search(r'\.(pdf|xlsx?|zip)(?:[?#]|$)',a['url'],re.I):links.append(a['url'])
    return list(dict.fromkeys(links))
