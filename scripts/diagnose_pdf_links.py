"""Local HTTP PDF-link diagnosis; never invokes a model."""
import sys,json,argparse
from pathlib import Path
from urllib.parse import quote
import httpx
root=Path(__file__).resolve().parents[1];sys.path.insert(0,str(root/'src'))
from insurance_rag.services.bootstrap import open_services
from insurance_rag.onboarding.store import IntakeStore

def main():
 parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--base-url',default='http://127.0.0.1:8793');parser.add_argument('--output',default='tmp/pdf_link_checks.json');args=parser.parse_args()
 results=[]
 with open_services(load_model=False) as app,httpx.Client(base_url=args.base_url,trust_env=False,timeout=120) as c:
  print('page',c.get('/').status_code,flush=True)
  jobs=IntakeStore(app.store).list('preparation_job',owner='local-product-administrator',limit=100)
  seen=set()
  for j in jobs:
   bid=(j['content'].get('result') or {}).get('bundle_id')
   if not bid or bid in seen:continue
   seen.add(bid)
   detail=c.get('/v1/product-ingestion/bundles/'+quote(bid,safe=''))
   if detail.status_code!=200:print('bundle',bid,detail.status_code,detail.text[:200],flush=True);continue
   d=detail.json();ids={x['document_version_id'] for x in d['documents']}
   print('product',d['identity']['product_name'],'documents',len(ids),flush=True)
   for doc in d['documents']:
    url='/v1/product-ingestion/bundles/'+quote(bid,safe='')+'/documents/'+quote(doc['document_version_id'],safe='')+'/pdf'
    r=c.get(url)
    result={'url':url,'status':r.status_code,'bytes':len(r.content),'pdf':r.content.startswith(b'%PDF-'),'headers':{k:v for k,v in r.headers.items() if k in ('content-type','content-disposition','content-security-policy')}}
    assert r.status_code==200 and result['pdf'],result
    assert r.headers['content-disposition'].startswith('inline;'),result
    part=c.get(url,headers={'Range':'bytes=0-4'});assert part.status_code==206 and part.content==b'%PDF-'
    assert httpx.get(str(c.base_url).rstrip('/')+url,trust_env=False).status_code==401
    result.update(range_status=part.status_code,unauthenticated_status=401);results.append(result)
    print(json.dumps(result),flush=True)
   if len(seen)>=2:break
  report=root/args.output;report.parent.mkdir(parents=True,exist_ok=True);report.write_text(json.dumps({'status':'passed','documents':results},ensure_ascii=False,indent=2),encoding='utf8')
if __name__=='__main__':main()
