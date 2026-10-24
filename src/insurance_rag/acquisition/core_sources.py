"""Official public APIs/static disclosure for AIA, PingAn, Allianz and HSBC."""
import datetime,hashlib,html,io,json,os,pathlib,re,time,urllib.parse,uuid,zipfile
NOW=lambda:datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
SHA=lambda b:hashlib.sha256(b).hexdigest()
KINDS=['条款','产品说明书','费率表','现金价值表']
def update(*args,**kwargs):pass
TARGETS={'AIA友邦': 'https://www.aia.com.cn/zh-cn/gongkaixinxipilou/jibenxinxi/chanpinjibenxinxi/zaishouchanpin', 'PingAn平安': 'https://life.pingan.com/gongkaixinxipilu/baoxianchanpinmulujitiaokuan.jsp', 'Allianz安联': 'https://www.allianz.com.cn/public/publicDisclosure/AZ0070208', 'HSBC汇丰': 'https://www.hsbcinsurance.com.cn/about-us/information-disclosure/basic-information/'}
DOMAINS={'AIA友邦': ['aia.com.cn'], 'PingAn平安': ['pingan.com', 'pingan.com.cn'], 'Allianz安联': ['allianz.com.cn'], 'HSBC汇丰': ['hsbcinsurance.com.cn']}

def write_json(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    os.replace(temp, path)


def quote_url(url):
    p=urllib.parse.urlsplit(html.unescape(url))
    return urllib.parse.urlunsplit((p.scheme,p.netloc,urllib.parse.quote(p.path,safe='/%:@!$&\'()*+,;=-._~'),urllib.parse.quote(p.query,safe='=&%/:?@!$\'()*+,;~-._'),''))


def clean(text): return re.sub(r'\s+',' ',html.unescape(re.sub(r'<[^>]*>',' ',str(text or '')))).strip()


def candidate(company, product, category, url, row=None, **extra):
    return dict(company=company,product_name=clean(product),category=category,url=quote_url(url),source_catalog=TARGETS[company],source_row=row or {},**extra)


def discover(company, client):
    links=[]; missing=[]; products=[]; totals=[]; pages=[]
    def add_row(row,name,key,fields,build):
        products.append({'product_name':clean(name),'identity':key,'source_row':row})
        for field,kind in fields:
            value=row.get(field)
            if value:
                url=build(value,field)
                if url: links.append(candidate(company,name,kind,url,row,product_identity=key))
            else: missing.append(dict(product_name=clean(name),identity=key,category=kind,status='not_advertised_in_catalog'))
    if company=='AIA友邦':
        index=0
        while True:
            payload={'like':'','status':'在售','page':{'pageSize':100,'index':index}}
            rid=str(uuid.uuid4()); s=json.dumps(payload,ensure_ascii=False,separators=(',',':'))
            signature=hashlib.md5(('1qaz@WSX#$%&#'+''.join(sorted(s))+'##'+rid).encode()).hexdigest().upper()
            data=client.api('https://cws.aia.com.cn/mypage/productpublish/list',payload=payload,headers={'x-request-id':rid,'x-crp-sign':signature,'Origin':'https://www.aia.com.cn'})
            if str(data.get('success')).lower()!='true': raise ValueError('AIA_API_ERROR '+str(data)[:500])
            pub=data.get('product_publish') or {}; rows=pub.get('records') or []; total=int(pub.get('totalPage') or 0)
            pages.append({'index':index,'rows':len(rows),'total_pages':total}); totals.append(pub.get('total'))
            for row in rows:
                def build(v,f):
                    v=str(v).strip()
                    if not v: return ''
                    if v.startswith('http'): return v
                    return urllib.parse.urljoin('https://www.aia.com.cn/content/dam/cn/zh-cn/docs/public-disclosure/', v if v.lower().endswith('.pdf') else v+'.pdf')
                add_row(row,row.get('productName'),str(row.get('id') or row.get('productName')),[('productItem','条款'),('productInstruction','产品说明书'),('ratesTable','费率表'),('cashValueTable','现金价值表')],build)
            update(company,phase='catalog',catalog_rows=len(products),catalog_pages=len(pages))
            if not rows or index+1>=total: break
            index+=1
    elif company=='PingAn平安':
        data=client.api('https://life.pingan.com/ilife-home/product/getProductList',payload={'isOrNotSale':'Y','planSalesStatus':'Y','sourceCode':'ilife-core','isOnlyNew':'Y'},headers={'Origin':'https://life.pingan.com'})
        if data.get('CODE')!='00': raise ValueError('PINGAN_API_ERROR '+str(data)[:500])
        rows=data.get('DATA') or []; pages.append({'rows':len(rows),'pagination':'single returned DATA; official total unavailable'})
        for row in rows:
            name=row.get('clauseName'); code=str(row.get('actualPlanCode') or row.get('planCode') or '')
            key=code+'@'+str(row.get('versionNo') or '')
            products.append(dict(product_name=clean(name),identity=key,source_row=row))
            for field,typ,kind in [('clauseContent','1','条款'),('productInstruction','7','产品说明书'),('premAttachment','2','费率表'),('existResvAttachment','12','现金价值表')]:
                if str(row.get(field))!='1': missing.append(dict(product_name=clean(name),identity=key,category=kind,status='not_advertised_in_catalog')); continue
                if '/' in code:
                    missing.append(dict(product_name=clean(name),identity=key,category=kind,status='ambiguous_combination_code_requires_review')); continue
                url='https://life.pingan.com/ilife-home/product/getPlanClausePdf?'+urllib.parse.urlencode({'planCode':code,'versionNo':row.get('versionNo',''),'attachmentType':typ})
                links.append(candidate(company,name,kind,url,row,product_identity=key,product_code=code,versionNo=row.get('versionNo'),filing_code=row.get('sccode')))
    elif company=='Allianz安联':
        page=1
        while True:
            ts=str(int(time.time())); sig=hashlib.md5(('timeStamp='+ts+'&clientId=ffadac099ec8204c').encode()).hexdigest()
            data=client.api('https://www.allianz.com.cn/dev-api/azFile/list',payload={'timeStamp':ts,'sign':sig,'fileCategory':'AZ0070208','fileTitle':'','pageNum':page,'fileState':'1'},headers={'Origin':'https://www.allianz.com.cn'})
            if data.get('code')!=200: raise ValueError('ALLIANZ_API_ERROR '+str(data)[:500])
            rows=data.get('rows') or []; total=int(data.get('pages') or 0)
            pages.append({'page':page,'rows':len(rows),'total_pages':total}); totals.append(data.get('total'))
            for row in rows:
                add_row(row,row.get('fileTitle'),str(row.get('zid') or row.get('id') or row.get('fileTitle')),[('fileUrl','条款'),('extraFileUrl','产品说明书'),('rateScheduleUrl','费率表'),('statementValueUrl','现金价值表')],lambda v,f:urllib.parse.urljoin('https://www.allianz.com.cn',str(v).strip()))
            update(company,phase='catalog',catalog_rows=len(products),catalog_pages=len(pages))
            if not rows or page>=total: break
            page+=1
    else:
        body,meta=client.get(TARGETS[company],catalog=True)
        encoding=re.search(br'charset=["\s]*([a-zA-Z0-9_-]+)',body[:8192])
        text=body.decode(encoding.group(1).decode() if encoding else 'utf-8',errors='replace')
        # Split by observed tab-panel boundaries, keeping only the official current-sale table
        # and supplementary tables for products listed in that table. Historical tabs are separate.
        panels=list(re.finditer(r'<div\s+id="([^"]+)"[^>]*role="tabpanel"[^>]*>',text,re.I))
        sections={m.group(1):text[m.end():panels[i+1].start() if i+1<len(panels) else len(text)] for i,m in enumerate(panels)}
        current=sections.get('03',''); names=set()
        for tr in re.findall(r'<tr\b[^>]*>(.*?)</tr>',current,re.I|re.S):
            cells=re.findall(r'<td\b[^>]*>(.*?)</td>',tr,re.I|re.S)
            if len(cells)<4: continue
            name=clean(cells[0])
            if not name.startswith('汇丰') or re.search(r'<a\b',cells[0],re.I): continue
            names.add(name); products.append(dict(product_name=name,identity=name,source_row={'cells':[clean(c) for c in cells],'sale_status':'current_sale_tab'}))
            for cell in cells[1:]:
                for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',cell,re.I|re.S):
                    url,label=match.groups(); label=clean(label)
                    kind='产品说明书' if '说明' in label else '条款' if '条款' in label else None
                    if kind: links.append(candidate(company,name,kind,urllib.parse.urljoin(TARGETS[company],url),{'label':label,'panel':'03'},product_identity=name))
        excluded=[]
        for panel,kind in [('tab-panel-tab_998111038_copy-16305','现金价值表'),('tab-panel-tab_1274274584_copy-16357','费率表')]:
            for match in re.finditer(r'<a\b[^>]*href=["\']([^"\']+)["\'][^>]*>(.*?)</a>',sections.get(panel,''),re.I|re.S):
                url,label=match.groups(); label=clean(label)
                if not re.search(r'\.(pdf|xlsx?|zip)(?:[?#]|$)',url,re.I): continue
                # Accessible download anchors repeat their labels. Match exact table product
                # prefix followed by its material label, and prefer the longest product name.
                matches=[n for n in names if label.startswith(n) and re.match(r'(?:保险)?(?:现金价值|费率)',label[len(n):])]
                if matches:
                    name=max(matches,key=len)
                    links.append(candidate(company,name,kind,urllib.parse.urljoin(TARGETS[company],url),{'label':label,'panel':panel},product_identity=name))
                else: excluded.append({'url':url,'label':label,'reason':'not_matched_to_current_sale_product_table'})
        write_json(client.evidence/'excluded_supplements.json',excluded)
        for name in names:
            for kind in KINDS:
                if not any(c['product_name']==name and c['category']==kind for c in links): missing.append(dict(product_name=name,category=kind,status='not_advertised_or_not_matched_in_current_sale_scope'))
        pages.append(dict(rows=len(products),scope='official current sale panel 03; cash/rate attachments matched to those products; historical/stopped/unoffered tabs excluded'))
        if not links: raise ValueError('HSBC_NO_MATERIAL_LINKS_IN_STATIC_HTML')
    # Keep all associations; only identical company/product/category/URL rows collapse.
    unique={}
    for row in links:
        row['id']=SHA(json.dumps([row['company'],row['product_identity'],row['category'],row['url']],ensure_ascii=False).encode())[:24]
        unique.setdefault(row['id'],row)
    unique_products={p['identity']:p for p in products}
    return dict(company=company,discovered_at=NOW(),products=list(unique_products.values()),raw_catalog_rows=len(products),candidates=list(unique.values()),unavailable_materials=missing,pages=pages,reported_totals=totals,product_limit=None,products_per_kind_limit=0,coverage_note='Returned official disclosure scope; not a proof of every historical or currently sold policy',status='catalog_discovered')


def classify(body):
    if body.startswith(b'%PDF-'): return 'pdf'
    if body.startswith(b'PK'):
        with zipfile.ZipFile(io.BytesIO(body)) as z: return 'xlsx' if 'xl/workbook.xml' in z.namelist() else 'zip'
    if body.startswith(bytes.fromhex('D0CF11E0A1B11AE1')): return 'xls'
    return 'unsupported'


def validate_file(path, output):
    from . import material_checks as checks
    body=path.read_bytes(); kind=classify(body)
    if kind=='unsupported': raise ValueError('UNSUPPORTED_FILE')
    parts=checks.safe_archive(body)[1] if kind=='zip' else [('direct',body,kind)]
    files=[]; alltext=[]; output.parent.mkdir(exist_ok=True,parents=True)
    for index,(member,data,ftype) in enumerate(parts):
        material=path
        if kind=='zip':
            material=path.parent/(path.stem+f'_member_{index}.'+ftype)
            if not material.exists(): material.write_bytes(data)
            elif SHA(material.read_bytes())!=SHA(data): raise ValueError('MEMBER_HASH_MISMATCH')
        r=dict(file=str(material),member=member,sha256=SHA(data),bytes=len(data),file_type=ftype)
        if ftype=='pdf':
            quality,texts=checks.inspect_pdf(data); r['pdf_validation']=quality
            # Retain all inspected text; large files retain first and last pages only.
            text='\n'.join(texts); alltext.append(text)
            textpath=output.parent/(output.stem+f'_{index}.txt'); textpath.write_text(text,encoding='utf-8'); r['text_file']=str(textpath)
            guide_pages=[i+1 for i,t in enumerate(texts[:5]) if '阅读指引' in checks.norm(t)] if quality['text_scope']=='all_pages' else []
            r['reading_guide_page_numbers']=guide_pages
        elif ftype=='xlsx':
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                if z.testzip() is not None: raise ValueError('XLSX_CRC_FAILED')
                if 'xl/workbook.xml' not in z.namelist(): raise ValueError('XLSX_WORKBOOK_MISSING')
            r['spreadsheet_validation']='package_crc_only; business cells not parsed'
        else: r['spreadsheet_validation']='OLE_magic_only; workbook cells not parsed'
        files.append(r)
    write_json(output,dict(status='validated',files=files,text_preview='\n'.join(alltext)[:1500]))

