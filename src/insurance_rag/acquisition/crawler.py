"""v2 acquisition entry point. Full catalogs, failure-specific recovery, extra evidence."""
from __future__ import annotations
import argparse, collections, concurrent.futures, csv, hashlib, importlib.util, json, os, pathlib, re, shutil, subprocess, sys, threading, time
from .transport import Transport, FetchError, digest, now
from . import adapters

from ..paths import get_project_root

CODE=pathlib.Path(__file__).resolve().parent; PROJECT=get_project_root()
LEGACY=adapters.legacy
TYPES=json.loads((CODE/'material_types.json').read_text(encoding='utf8'))
KINDS=[r['kind'] for group in ['common','conditional','audit_optional'] for r in TYPES[group]]
LOCK=threading.RLock(); STATES={}; OUTPUT=None; SAMPLE_PRODUCTS=None; SAMPLE_FILES=None

def write_json(path,data):
    path.parent.mkdir(exist_ok=True,parents=True); temp=path.with_suffix(path.suffix+'.tmp')
    temp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8'); os.replace(temp,path)

def report():
    scope=('本轮为小规模测试，每家公司最多 '+str(SAMPLE_PRODUCTS or '不限')+' 个产品、'+str(SAMPLE_FILES or '不限')+' 条资料候选；样本不代表全目录覆盖。' if SAMPLE_PRODUCTS or SAMPLE_FILES else '目录不设险种或产品数量筛选。')
    lines=['# 扩展采集与失败恢复报告','','更新时间：'+now(),'',scope+'记录中 validated 表示相应级别的文件检查，未代表保险数值或正文版本核验。','',
           '| 公司 | 目录产品身份数 | 候选资料 | 通过检查 | 失败 | 未尝试 | 状态 |','|---|---:|---:|---:|---:|---:|---|']
    for company,state in STATES.items():
        lines.append('| '+' | '.join(map(str,[company,state.get('products','发现中'),state.get('candidates','发现中'),state.get('validated',0),state.get('failed',0),state.get('pending',0),state.get('phase','')]))+' |')
    lines+=['','每家公司：catalog.json 保存完整目录；checkpoint.jsonl 保存终态与恢复过程；requests/ 保存真实请求及重试证据；summary.json / 资料总览.csv 保存最终结果；资料获取清单.json 保存统一资料类型的发现/缺口及适用情况。','',
            '共用资料：产品目录与状态、条款、产品说明书、费率、现金价值、投保规则与告知、产品变更/停售公告。红利/利率/账户/医疗服务按险种需要收集，备案与声明为审计资料。未发现资料不等于官网没有，通用资料不自动关联至每个产品。','',
            '重试：暂时网络/SSL故障最多3次，SSL中断可切换系统curl；超时增大时限；5xx退避并尊重Retry-After；404重新核对源目录一次；大小限制只在空间足够时扩大一次；本地解析超时使用独立首尾页检查，不重新下载。证书错误、401/403/429及明确挑战不会绕过。','',
            'PDF全页读取与大文件/超时后的局部检查分开；Excel业务单元格尚未解析；HTML保存来源正文与附件链接；业务字段提取仅为匹配关键词段落线索，未确认产品版本或核保条件。']
    (OUTPUT/'验证报告.md').write_text('\n'.join(lines),encoding='utf8')

def progress(company,**changes):
    with LOCK:
        STATES.setdefault(company,{}).update(changes,updated_at=now())
        write_json(OUTPUT/'progress.json',dict(updated_at=now(),companies=STATES)); report()

def check_magic(body):
    return LEGACY.classify(body)

def challenge(body):
    text=body[:32768].decode('utf8',errors='ignore').lower()
    return any(word in text for word in ['captcha','人机验证','请输入验证码','访问过于频繁','access denied','verify you are human','cloudflare challenge','安全验证'])

def validation_worker(path,out,fast=False,reason='parser_timeout'):
    if path.suffix.lower() in ('.jpg','.png'):
        from PIL import Image
        body=path.read_bytes()
        with Image.open(path) as picture:
            width,height=picture.size;format=picture.format
            if format not in ('JPEG','PNG') or width*height>100_000_000:raise ValueError('UNSUPPORTED_OR_OVERSIZED_IMAGE')
            picture.verify()
        write_json(out,dict(status='validated',validation_level='retained_image_no_ocr',files=[dict(file=str(path),file_type=path.suffix[1:],bytes=len(body),sha256=digest(body),image_validation=dict(format=format,width=width,height=height,note='Image structure checked; OCR and business content not verified'))]))
        return
    if path.suffix.lower()=='.json':
        body=path.read_bytes();data=json.loads(body.decode('utf-8-sig'))
        if not isinstance(data,(list,dict)) or not data:raise ValueError('EMPTY_JSON_ATTACHMENT')
        textpath=out.with_suffix('.txt');textpath.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
        write_json(out,dict(status='validated',validation_level='retained_json',files=[dict(file=str(path),file_type='json',bytes=len(body),sha256=digest(body),text_file=str(textpath),json_validation='syntax_and_nonempty_container_only; business_rows_not_verified')]))
        return
    if not fast and path.suffix.lower()!='.html':
        LEGACY.validate_file(path,out); return
    if path.suffix.lower()=='.html':
        body=path.read_bytes(); text=body.decode('utf8',errors='replace')
        if challenge(body):raise ValueError('CHALLENGE_HTML')
        content=adapters.clean(re.sub(r'<(?:script|style)\b[^>]*>.*?</(?:script|style)>','',text,flags=re.I|re.S))
        if len(content)<30:raise ValueError('EMPTY_HTML_CONTENT')
        textpath=out.with_suffix('.txt'); textpath.parent.mkdir(exist_ok=True,parents=True); textpath.write_text(content,encoding='utf8')
        write_json(out,dict(status='validated',validation_level='retained_html',files=[dict(file=str(path),file_type='html',bytes=len(body),sha256=digest(body),text_file=str(textpath),html_validation='decoded_content_retained_not_business_verified')]))
        return
    # Retry parsing in an isolated native parser, rather than downloading again.
    import pypdfium2 as pdfium
    from . import material_checks as checks
    body=path.read_bytes(); kind=check_magic(body)
    parts=checks.safe_archive(body)[1] if kind=='zip' else [('direct',body,kind)]
    files=[]
    for i,(member,data,ftype) in enumerate(parts):
        p=path if kind!='zip' else path.parent/(path.stem+f'_native_{i}.'+ftype)
        if p!=path:p.write_bytes(data)
        item=dict(file=str(p),member=member,file_type=ftype,bytes=len(data),sha256=digest(data))
        if ftype=='pdf':
            if not data.startswith(b'%PDF-') or b'%%EOF' not in data[-1024:]:raise ValueError('PDF_MAGIC_OR_EOF_FAILED')
            pdf=pdfium.PdfDocument(data); count=len(pdf)
            if not count:raise ValueError('ZERO_PAGES')
            texts=[]
            indexes=list(range(count)) if reason=='strict_xref_error' and count<=100 else sorted(set([0,count-1]))
            for index in indexes:
                page=pdf[index]; textpage=page.get_textpage(); texts.append(textpage.get_text_range()); textpage.close(); page.close()
            pdf.close(); textpath=out.parent/(out.stem+f'_native_{i}.txt'); textpath.write_text('\n'.join(texts),encoding='utf8')
            level='native_after_strict_xref_error' if reason=='strict_xref_error' else 'partial_after_parser_timeout'
            item.update(text_file=str(textpath),pdf_validation=dict(structural_status=level,pages=count,text_scope='all_pages_native' if len(indexes)==count else 'first_and_last_pages_only',parser='pypdfium2',strict_validation_passed=False,note='Independent parser recovery; original strict parser did not pass. Original bytes retained.'))
        else:item['spreadsheet_validation']='file_retained_after_parser_timeout; cells_not_checked'
        files.append(item)
    write_json(out,dict(status='validated',validation_level='native_after_strict_xref_error' if reason=='strict_xref_error' else 'partial_after_parser_timeout',files=files))

def validate(path,out):
    out.parent.mkdir(exist_ok=True,parents=True); attempts=[];reason='parser_timeout'
    for fast,timeout in [(False,120),(True,90)]:
        cmd=[sys.executable,'-X','utf8','-m','insurance_rag.acquisition.crawler','--validate-file',str(path),'--validation-output',str(out)]
        if fast:cmd+=['--fast-validation','--fast-reason',reason]
        try:
            env=os.environ.copy()
            source_root=str(CODE.parents[1])
            env['PYTHONPATH']=os.pathsep.join(filter(None,[source_root,env.get('PYTHONPATH')]))
            result=subprocess.run(cmd,capture_output=True,timeout=timeout,env=env)
            if result.returncode:raise ValueError(result.stderr.decode('utf8',errors='replace')[-2000:])
            quality=json.loads(out.read_text(encoding='utf8')); attempts.append(dict(parser='native_first_last' if fast else 'normal',timeout_seconds=timeout,status='success'))
            quality['validation_attempts']=attempts; write_json(out,quality); return quality
        except subprocess.TimeoutExpired:
            attempts.append(dict(parser='native_first_last' if fast else 'normal',status='timeout',timeout_seconds=timeout))
            if fast:raise FetchError('parse_timeout','ALL_LOCAL_PARSE_ATTEMPTS_TIMED_OUT',attempts=attempts)
        except Exception as exc:
            attempts.append(dict(parser='native_first_last' if fast else 'normal',status='failed',error=repr(exc)))
            if not fast and 'Broken xref table' in str(exc):
                reason='strict_xref_error'
                continue
            raise FetchError('invalid_file','LOCAL_VALIDATION_FAILED: '+str(exc),attempts=attempts) from exc
    raise AssertionError('unreachable')

def field_clues(record):
    # Persist traceable paragraph leads, never automatically assert them as conditions.
    result=[]
    for file in record.get('files',[]):
        path=file.get('text_file')
        if not path:continue
        text=pathlib.Path(path).read_text(encoding='utf8')
        for kind,regex in [('投保规则与告知',r'投保年龄|投保范围|健康告知|职业类别|承保地区|投保条件'),('保障与免责',r'责任免除|保险责任|等待期|犹豫期'),('缴费与期限',r'缴费期间|交费期间|保险期间|保障期限')]:
            snippets=[dict(offset=m.start(),text=text[max(0,m.start()-50):m.end()+180]) for m in list(re.finditer(regex,text))[:12]]
            if snippets:result.append(dict(kind=kind,source_file=file['file'],text_file=path,text_scope=file.get('pdf_validation',{}).get('text_scope','retained_text'),snippets=snippets,status='pending_field_and_page_review'))
    return result

def existing_cache():
    # Resume/cache evidence is confined to the selected output's checkpoint.
    return {},[]

def fetch_supplements(company,client,cat):
    source=adapters.COMPANIES[company]['url']; extras=[]
    try:
        body,meta=client.get(source,catalog=True); text=body.decode('utf8',errors='replace')
        entries=adapters.supplement_entrypoints(company,text,source)
        # The entry page itself is also retained as catalog/status evidence.
        extras.append(adapters.make(company,'公司产品目录与状态','产品目录与销售状态',source,source,scope='company',fetch_mode='json' if source.endswith('.json') else 'html'))
        for entry in entries:
            extras.append(adapters.make(company,entry['label'],entry['category'],entry['url'],source,entry,scope='company',fetch_mode='file' if re.search(r'\.(pdf|xlsx?|zip)(?:[?#]|$)',entry['url'],re.I) else 'html'))
        cat['supplement_discovery']=dict(source=source,entries=len(entries),status='inspected',scope='labelled links on disclosure entry page; deeper materials discovered when HTML pages are fetched')
    except FetchError as exc:
        cat['supplement_discovery']=dict(status='failed',failure_kind=exc.kind,error=str(exc),attempts=exc.attempts)
        if exc.stop:raise
    return extras

def html_children(company,c,raw,trans,candidates):
    if raw.suffix!='.html' or c['category']=='产品目录与销售状态':return []
    children=[]; existing={r['id'] for r in candidates}
    for link in adapters.anchors(raw.read_text(encoding='utf8',errors='replace'),c['url']):
        if not trans.allowed(link['url']):continue
        kind=adapters.material_kind(link['label'])
        if kind and re.search(r'\.(pdf|xlsx?|zip)(?:[?#]|$)',link['url'],re.I):
            child=adapters.make(company,link['label'],kind,link['url'],c['url'],link,scope='company',parent_id=c['id'])
        elif link['label'] in ('下一页','>','下页') and link['url']!=c['url'] and pathlib.PurePosixPath(adapters.urllib.parse.urlsplit(link['url']).path).parent==pathlib.PurePosixPath(adapters.urllib.parse.urlsplit(c['url']).path).parent:
            child=adapters.make(company,c['product_name'],c['category'],link['url'],c['source_catalog'],link,scope='company',parent_id=c['id'],fetch_mode='html')
        else:continue
        child['id']=candidate_id(child)
        if child['id'] not in existing:children.append(child); existing.add(child['id'])
    return children

def finalize_manifest(company,cat,records,folder):
    kinds=collections.defaultdict(list)
    for r in records:kinds[r['category']].append(dict(id=r['id'],scope=r.get('scope'),product_identity=r['product_identity'],status=r['status'],url=r['url']))
    company_inventory={kind:dict(status='discovered' if kinds[kind] else 'not_found_in_inspected_sources',records=kinds[kind]) for kind in KINDS}
    product_inventory=[]
    for product in cat['products']:
        key=str(product.get('product_identity') or product.get('identity') or product['product_name']); name=product['product_name']; fields={}
        for kind in KINDS:
            linked=[r for r in records if r['product_identity']==key and r.get('scope','product')=='product' and r['category']==kind]
            applicable=True
            # Missing type information stays unknown; not_found never means verified absent.
            state='discovered' if linked else ('not_applicable' if not applicable else 'not_found_in_inspected_sources')
            if not linked and kinds[kind] and applicable:state='requires_product_linkage'
            clues=[r for r in records if r['product_identity']==key and any(c['kind']==kind for c in r.get('field_clues',[]))]
            if not linked and clues:state='embedded_in_core_document_pending_extraction'
            fields[kind]=dict(status=state,record_ids=[r['id'] for r in linked],clue_record_ids=[r['id'] for r in clues])
            if kind=='产品目录与销售状态':
                fields[kind]=dict(status='embedded_in_official_catalog',record_ids=[],clue_record_ids=[],source_file=str(folder/'catalog.json'),source_row=product.get('source_row',{}),catalog_product_identity=key)
        product_inventory.append(dict(product_identity=key,product_name=name,materials=fields,body_version_status='not_verified'))
    write_json(folder/'资料获取清单.json',dict(company=company,generated_at=now(),common_schema=TYPES,company_materials=company_inventory,products=product_inventory,coverage_note='Unified desired categories; does not assert every product offers every standalone file'))

def run_company(company,args,caches,oldrecords):
    folder=OUTPUT/company; folder.mkdir(exist_ok=True,parents=True)
    trans=Transport(adapters.COMPANIES[company]['domains'],folder/'requests',adapters.COMPANIES[company]['url'],prefer=args.transport)
    client=adapters.Client(company,trans,reuse_catalog=not args.refresh_catalog); journal=folder/'checkpoint.jsonl'; catalogpath=folder/'catalog.json'; latest={}
    client.sample_products=getattr(args,'sample_products',None)
    if journal.exists():
        for line in journal.read_text(encoding='utf8').splitlines():
            row=json.loads(line); latest[row['id']]=row
    def checkpoint(row):
        row['updated_at']=now()
        with journal.open('a',encoding='utf8') as f:f.write(json.dumps(row,ensure_ascii=False)+'\n'); f.flush(); os.fsync(f.fileno())
        latest[row['id']]=row
    try:
        progress(company,phase='目录发现')
        if catalogpath.exists() and not args.refresh_catalog:cat=json.loads(catalogpath.read_text(encoding='utf8'))
        else:
            cat=adapters.discover(company,client,args.refresh_catalog)
            cat['candidates']+=fetch_supplements(company,client,cat)
            # Unique material identities preserve source scope, product identity and versions.
            cat['candidates']=list({candidate_id(c):dict(c,id=candidate_id(c)) for c in cat['candidates']}.values())
            write_json(catalogpath,cat)
        for saved in list(latest.values()):
            if saved.get('status')!='validated' or not saved.get('raw_file'):continue
            raw=pathlib.Path(saved['raw_file'])
            if raw.exists() and digest(raw.read_bytes())==saved.get('sha256'):
                cat['candidates']+=html_children(company,saved,raw,trans,cat['candidates'])
        write_json(catalogpath,cat)
        selection=cat['candidates']
        if getattr(args,'sample_products',None):
            identities=list(dict.fromkeys(c['product_identity'] for c in selection if c.get('scope','product')=='product'))[:args.sample_products]
            selection=[c for c in selection if c.get('scope')=='company' or c['product_identity'] in identities]
        candidates=interleave(selection)
        if getattr(args,'sample_files',None):candidates=candidates[:args.sample_files]
        progress(company,phase='目录完成',products=len(cat['products']),candidates=len(candidates),pending=len(candidates))
        finalize_manifest(company,cat,[latest.get(c['id'],dict(c,status='pending')) for c in candidates],folder)
        if args.phase=='catalog':return
        restricted=(folder/'access_stop.json').exists(); consecutive_network=0; refresh={}
        for index,c in enumerate(candidates,1):
            old=latest.get(c['id'])
            if old and old['status']=='validated':
                if old.get('files') and all(pathlib.Path(f['file']).exists() and digest(pathlib.Path(f['file']).read_bytes())==f['sha256'] for f in old['files']):continue
                raise FetchError('cache_corruption','VALIDATED_CACHE_HASH_MISMATCH')
            if old and old['status']=='failed' and not args.retry_failed:continue
            if old and old['status']=='failed' and old.get('failure_kind') in ('access_restriction','challenge','tls_certificate'):continue
            row=dict(c,status='pending',recovery_of=old.get('error') if old else None)
            progress(company,phase='获取与检查',current=index,current_product=c['product_name'],current_kind=c['category'])
            try:
                if shutil.disk_usage(OUTPUT).free<2*1024**3:raise FetchError('disk_space','DISK_SPACE_BELOW_2GiB')
                cached=None if c.get('request_payload') is not None else caches.get(c['url']); raw=None
                if old and old.get('raw_file') and old.get('sha256'):cached=old
                if cached and pathlib.Path(cached['raw_file']).exists() and digest(pathlib.Path(cached['raw_file']).read_bytes())==cached['sha256']:
                    raw=pathlib.Path(cached['raw_file']); row.update(acquisition='verified_cache',raw_file=str(raw),file_type=cached.get('file_type') or raw.suffix.lstrip('.'),sha256=cached['sha256'],bytes=raw.stat().st_size,previous_status=cached.get('status'),source_http=cached.get('http_status'),source_final_url=cached.get('final_url'))
                else:
                    if restricted:raise FetchError('company_stopped','COMPANY_STOP_MARKER_RETAINED')
                    if consecutive_network>=5:raise FetchError('network_circuit_open','FIVE_CONSECUTIVE_TRANSIENT_FAILURES; resume later with retry-failed')
                    kwargs={'payload':c['request_payload']} if c.get('request_payload') is not None else {}
                    body,meta=client.get(c['url'],**kwargs);row.update(meta,acquisition='network')
                    kind=check_magic(body)
                    if c.get('fetch_mode')=='image':
                        if body.startswith(b'\xff\xd8\xff'):kind='jpg'
                        elif body.startswith(b'\x89PNG\r\n\x1a\n'):kind='png'
                    if c.get('fetch_mode')=='json':
                        from .expanded_adapters import decode
                        data=json.loads(decode(body))
                        if not isinstance(data,(dict,list)) or not data:raise FetchError('invalid_file','EMPTY_JSON_ATTACHMENT')
                        kind='json'
                    if challenge(body):raise FetchError('challenge','EXPLICIT_CHALLENGE_RESPONSE',attempts=meta['attempts'],stop=True)
                    if kind=='unsupported' and c.get('fetch_mode')=='detail_page':
                        # HTML version-selector pages are not downloaded PDFs.
                        detail=folder/'detail_pages'/(c['id']+'.html'); detail.parent.mkdir(exist_ok=True); detail.write_bytes(body)
                        urls=adapters.resolve_detail(body.decode('utf8',errors='replace'),c['url']); row['detail_file']=str(detail); row['resolved_file_urls']=urls
                        if len(urls)!=1:raise FetchError('detail_resolution','CURRENT_VERSION_FILE_COUNT_'+str(len(urls)))
                        body,filemeta=client.get(urls[0]); row['detail_request_meta']=meta; row.update(filemeta); kind=check_magic(body)
                    if kind=='unsupported' and c.get('fetch_mode')=='html':kind='html'
                    if kind=='unsupported':
                        errbody=folder/'invalid_responses'/(c['id']+'.bin'); errbody.parent.mkdir(exist_ok=True); errbody.write_bytes(body)
                        row['invalid_response_file']=str(errbody)
                        raise FetchError('non_file_response','NON_FILE_RESPONSE_SAVED_FOR_REVIEW',attempts=meta['attempts'])
                    raw=folder/'raw'/(c['id']+'.'+kind); raw.parent.mkdir(exist_ok=True)
                    if raw.exists() and digest(raw.read_bytes())!=digest(body):raw=raw.with_name(raw.stem+'_'+digest(body)[:8]+raw.suffix)
                    partial=raw.with_suffix(raw.suffix+'.part'); partial.write_bytes(body); os.replace(partial,raw)
                    row.update(raw_file=str(raw),file_type=kind,bytes=len(body),sha256=digest(body))
                consecutive_network=0; row['status']='downloaded'; checkpoint(dict(row)); caches[c['url']]=dict(row)
                out=folder/'validation'/(c['id']+'.json')
                reusable=(cached and cached.get('status')=='validated' and cached.get('files') and all(pathlib.Path(f['file']).exists() and digest(pathlib.Path(f['file']).read_bytes())==f['sha256'] for f in cached['files']))
                if reusable:
                    quality=dict(files=cached['files'],validation_attempts=[dict(status='reused_verified_hash',source_validation=cached.get('validation_file'))],validation_level=cached.get('validation_level','historical_recorded_file_checks'))
                    write_json(out,quality)
                else:quality=validate(raw,out)
                row.update(status='validated',validation_file=str(out),files=quality['files'],validation_attempts=quality['validation_attempts'],validation_level=quality.get('validation_level','recorded_file_checks'))
                row['field_clues']=field_clues(row); row['body_version_status']='not_verified'; checkpoint(row); caches[c['url']]=dict(row)
                # Discover explicitly labelled attachments on extra HTML disclosure pages.
                children=html_children(company,c,raw,trans,candidates)
                if getattr(args,'sample_files',None):children=children[:max(0,args.sample_files-len(candidates))]
                if children:candidates+=children; cat['candidates']+=children; write_json(catalogpath,cat)
            except FetchError as exc:
                row.update(status='not_attempted' if exc.kind in ('company_stopped','network_circuit_open','disk_space') else 'failed',failure_kind=exc.kind,error=str(exc),failure_attempts=exc.attempts)
                if exc.stop:
                    restricted=True; write_json(folder/'access_stop.json',dict(reason=exc.kind,error=str(exc),url=c['url'],time=now()))
                if exc.kind in ('tls_connection','timeout','network_transient','server_transient'):consecutive_network+=1
                if exc.kind=='not_found' and not restricted:
                    # Refresh matching official row only; never invent an alternative URL.
                    try:
                        source=c['source_catalog']
                        if source not in refresh:refresh[source]=refresh_catalog_for_candidate(c,client)
                        alternatives=[x for x in refresh[source] if x['category']==c['category'] and x['product_identity']==c['product_identity'] and x['url']!=c['url'] and x.get('versionNo')==c.get('versionNo') and x.get('version_or_date')==c.get('version_or_date')]
                        row['catalog_recheck']=dict(time=now(),replacement_urls=[x['url'] for x in alternatives],status='official_replacement_found' if alternatives else 'no_matching_replacement')
                        for replacement in alternatives:
                            if getattr(args,'sample_files',None) and len(candidates)>=args.sample_files:
                                row['catalog_recheck']['replacement_disposition']='deferred_by_sample_file_limit'
                                break
                            replacement['id']=candidate_id(replacement); replacement['replacement_for']=c['id']
                            if not any(x['id']==replacement['id'] for x in candidates):candidates.append(replacement); cat['candidates'].append(replacement); write_json(catalogpath,cat)
                    except Exception as refresh_exc:row['catalog_recheck']=dict(status='failed',error=repr(refresh_exc))
                checkpoint(row)
            except Exception as exc:row.update(status='failed',failure_kind='unexpected_error',error=repr(exc)); checkpoint(row)
            counts=collections.Counter(r['status'] for r in latest.values()); pending=len(candidates)-len(latest)
            progress(company,validated=counts['validated'],failed=counts['failed'],pending=max(pending,0),candidates=len(candidates),current=index)
            print(json.dumps(dict(company=company,index=index,total=len(candidates),category=c['category'],status=row['status'],failure_kind=row.get('failure_kind')),ensure_ascii=False),flush=True)
        records=[latest.get(c['id'],dict(c,status='not_attempted')) for c in candidates]
        counts=dict(collections.Counter(r['status'] for r in records)); bytype={k:dict(discovered=sum(r['category']==k for r in records),validated=sum(r['category']==k and r['status']=='validated' for r in records)) for k in KINDS}
        summary=dict(company=company,generated_at=now(),products=len(cat['products']),candidates=len(candidates),counts=counts,by_type=bytype,records=records,retry_recoveries=sum(r['status']=='validated' and r.get('retry_count',0)>0 for r in records))
        write_json(folder/'summary.json',summary); finalize_manifest(company,cat,records,folder)
        with (folder/'资料总览.csv').open('w',encoding='utf-8-sig',newline='') as f:
            writer=csv.writer(f); writer.writerow(['公司','产品','资料类别','关联范围','状态','检查级别','重试次数','官方链接','文件','错误'])
            for r in records:writer.writerow([company,r['product_name'],r['category'],r.get('scope'),r['status'],r.get('validation_level'),r.get('retry_count',0),r['url'],r.get('raw_file'),r.get('error')])
        product_documents=sum(r['status']=='validated' and r.get('scope','product')=='product' for r in records)
        progress(company,phase='未取得产品资料' if not product_documents else ('完成（有缺口）' if counts.get('failed') or counts.get('not_attempted') else '完成'),validated=counts.get('validated',0),failed=counts.get('failed',0),pending=counts.get('not_attempted',0),validated_product_documents=product_documents)
    except Exception as exc:
        error=dict(error=repr(exc),failure_kind=getattr(exc,'kind',None),attempts=getattr(exc,'attempts',[]),time=now()); write_json(folder/'run_error.json',error); progress(company,phase='目录或运行失败',error=error)

def candidate_id(c):return digest(json.dumps([c['company'],c['product_identity'],c['category'],c['url'],c.get('scope','product'),c.get('version_or_date'),c.get('versionNo')],ensure_ascii=False).encode())[:24]
def interleave(candidates):
    kinds=list(dict.fromkeys(c['category'] for c in candidates)); buckets=[[c for c in candidates if c['category']==k] for k in kinds]
    return [b[i] for i in range(max(map(len,buckets),default=0)) for b in buckets if i<len(b)]
def refresh_catalog_for_candidate(c,client):
    client.reuse_catalog=False
    company=c['company']; source=c['source_catalog']
    if company in adapters.NEW:
        body,meta=client.get(source,catalog=True); text=body.decode('utf8',errors='replace')
        parser={'CPIC太平洋人寿':adapters.parse_cpic,'NewChina新华保险':adapters.parse_newchina,'Taikang泰康人寿':adapters.parse_taikang}[company]
        return parser(text,source)[1]
    return adapters.discover(company,client,refresh=True)['candidates']

def main():
    global OUTPUT,SAMPLE_PRODUCTS,SAMPLE_FILES
    ap=argparse.ArgumentParser(); ap.add_argument('--output'); ap.add_argument('--companies',nargs='+',choices=list(adapters.COMPANIES)); ap.add_argument('--phase',choices=['catalog','all'],default='all'); ap.add_argument('--retry-failed',action='store_true'); ap.add_argument('--refresh-catalog',action='store_true'); ap.add_argument('--transport',choices=['urllib','curl'],default='urllib'); ap.add_argument('--workers',type=int,default=2); ap.add_argument('--sample-products',type=int); ap.add_argument('--sample-files',type=int); ap.add_argument('--validate-file'); ap.add_argument('--validation-output'); ap.add_argument('--fast-validation',action='store_true'); ap.add_argument('--fast-reason',choices=['parser_timeout','strict_xref_error'],default='parser_timeout'); args=ap.parse_args()
    if any(v is not None and v<1 for v in (args.sample_products,args.sample_files)):ap.error('Sample limits must be positive')
    SAMPLE_PRODUCTS=args.sample_products;SAMPLE_FILES=args.sample_files
    if args.validate_file:validation_worker(pathlib.Path(args.validate_file),pathlib.Path(args.validation_output),args.fast_validation,args.fast_reason); return
    OUTPUT=pathlib.Path(args.output) if args.output else PROJECT/'data'/'acquisition'/('运行_'+datetime_stamp())
    OUTPUT=OUTPUT.resolve()
    if not OUTPUT.is_relative_to(PROJECT.resolve()):raise ValueError('OUTPUT_MUST_STAY_IN_PROJECT')
    OUTPUT.mkdir(exist_ok=True,parents=True)
    companies=args.companies or adapters.DEFAULT_COMPANIES
    runlock=OUTPUT/'active.lock'
    try:handle=runlock.open('x',encoding='utf8')
    except FileExistsError:raise SystemExit('RUN_LOCK_EXISTS; verify prior process ended before removing stale lock')
    with handle:handle.write(str(os.getpid()))
    try:
        cfg=dict(schema_version=2,started_at=now(),pid=os.getpid(),companies=companies,product_limit=args.sample_products,sample_files=args.sample_files,products_per_kind_limit=0,max_attempts=3,ssl_fallback='system_curl_without_disabling_TLS_verification',workers=max(1,min(args.workers,3)),phase=args.phase,retry_failed=args.retry_failed)
        write_json(OUTPUT/('config_'+str(time.time_ns())+'.json'),cfg); write_json(OUTPUT/'material_types.json',TYPES)
        caches,old=existing_cache()
        with concurrent.futures.ThreadPoolExecutor(max_workers=cfg['workers']) as pool:
            futures=[pool.submit(run_company,c,args,caches,old) for c in companies]
            for f in concurrent.futures.as_completed(futures):f.result()
        with LOCK:report()
        write_json(OUTPUT/'run_complete.json',dict(completed_at=now(),companies=STATES))
    finally:runlock.unlink(missing_ok=True)

def datetime_stamp():
    import datetime
    return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).strftime('%Y%m%d_%H%M%S')

if __name__=='__main__':main()
