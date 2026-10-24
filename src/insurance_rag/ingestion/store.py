"""Owner-scoped uploads, immutable artifacts and durable preparation jobs."""
import json,re,time,uuid
from pathlib import Path
from insurance_rag.agent.store import Forbidden,Conflict
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import stable_hash,file_hash,workspace_path
KINDS={'terms','brochure','premium_rate','cash_value','other'}
from .source_package import PREPARATION_POLICY

def owner_key(owner):
 if not isinstance(owner,str) or not owner.strip():raise Forbidden('owner_required')
 return stable_hash(['private-product-space-v1',owner])

def asset_folder(root,owner):return Path(root)/'data/product_ingestion'/owner_key(owner)/'uploads'

def register_asset(app,owner,path,filename='document.pdf'):
 path=Path(path).resolve();folder=asset_folder(app.root,owner).resolve()
 if path.parent!=folder or not path.is_file():raise Forbidden('owned_upload_required')
 if path.stat().st_size>64*1024*1024:raise ValueError('pdf_exceeds_64mb')
 with path.open('rb') as f:
  if not f.read(1024).startswith(b'%PDF-'):raise ValueError('invalid_pdf_header')
 sha=file_hash(path);id='private-asset:'+stable_hash([owner_key(owner),sha]);store=IntakeStore(app.store)
 content={'path':path.relative_to(app.root).as_posix(),'sha256':sha,'bytes':path.stat().st_size,'filename':Path(filename.replace(chr(92),'/')).name[:200],'acquired_at':time.time()}
 try:
  prior=store.get('private_asset',id,owner)
  if prior['content']['sha256']!=sha:raise Conflict('asset_changed')
  return prior
 except Forbidden:return store.create('private_asset',id,owner,'',content,'stored')

def get_asset(app,owner,id):
 row=IntakeStore(app.store).get('private_asset',id,owner);c=row['content'];p=workspace_path(c['path'],app.root,must_exist=True)
 if p.parent!=asset_folder(app.root,owner).resolve() or file_hash(p)!=c['sha256']:raise Conflict('private_asset_changed')
 return row

def create_job(app,owner,request_id,assets,*,product_name='',company='',version_label='',refresh=False):
 if not isinstance(request_id,str) or not 1<=len(request_id)<=100:raise ValueError('request_id_required')
 if not isinstance(assets,list) or not 1<=len(assets)<=12:raise ValueError('one_to_twelve_pdfs_required')
 verified=[];seen=set()
 for entry in assets:
  if not isinstance(entry,dict) or set(entry)!={'asset_id','document_type'} or entry['document_type'] not in KINDS:raise ValueError('invalid_document_slot')
  asset=get_asset(app,owner,entry['asset_id'])
  if asset['id'] in seen:raise ValueError('duplicate_asset')
  seen.add(asset['id']);verified.append({**entry,'sha256':asset['content']['sha256']})
 for value in (product_name,company,version_label):
  if not isinstance(value,str) or len(value)>300:raise ValueError('invalid_product_hint')
 if type(refresh) is not bool:raise ValueError('invalid_refresh')
 from .source_workflow import POLICY as WORKFLOW_POLICY
 content={'schema_version':5,'workflow_policy':WORKFLOW_POLICY,'preparation_revision':request_id if refresh else None,'preparation_policy':PREPARATION_POLICY,'assets':verified,'hints':{'product_name':product_name.strip(),'company':company.strip(),'version_label':version_label.strip()},'stage':'queued','completed_steps':{},'attempts':0,'result':None,'space_id':owner_key(owner),'visibility':'private'}
 fingerprint=stable_hash(content);content['fingerprint']=fingerprint;id='preparation:'+stable_hash([owner_key(owner),request_id,PREPARATION_POLICY,WORKFLOW_POLICY]);store=IntakeStore(app.store)
 try:
  row=store.get('preparation_job',id,owner)
  if row['content'].get('preparation_revision')!=content['preparation_revision']:raise Conflict('request_refresh_conflict')
  if row['content']['hints']!=content['hints'] or sorted(row['content']['assets'],key=lambda e:(e['document_type'],e['asset_id']))!=sorted(verified,key=lambda e:(e['document_type'],e['asset_id'])):raise Conflict('request_input_conflict')
  return row
 except Forbidden:pass
 for row in store.list('preparation_job',owner=owner,limit=500):
  if not refresh and row['content'].get('preparation_policy')==PREPARATION_POLICY and row['content'].get('workflow_policy')==WORKFLOW_POLICY and row['content']['hints']==content['hints'] and sorted(row['content']['assets'],key=lambda e:(e['document_type'],e['asset_id']))==sorted(verified,key=lambda e:(e['document_type'],e['asset_id'])) and row['state'] in ('queued','running','ready','partial'):return row
 return store.create('preparation_job',id,owner,'',content,'queued')

def bundle(app,owner,id):
 if not id.startswith('product-bundle:'):raise Forbidden('source_bundle_required')
 store=IntakeStore(app.store);kind='product_bundle'
 try:row=store.get(kind,id,owner)
 except Forbidden:
  from .library import publication
  shared=publication(app,id)['content'];row=store.get(kind,id,shared['source_owner'])
  if row['content_hash']!=shared['bundle_content_hash']:raise Conflict('library_product_version_changed')
 c=row['content']
 for d in c['documents']:
  if file_hash(workspace_path(d['source_file'],app.root,must_exist=True))!=d['source_sha256'] or file_hash(workspace_path(d['layout_file'],app.root,must_exist=True))!=d['layout_sha256']:raise Conflict('bundle_source_changed')
 return row

def public_job(row):
 from .readiness import task_summary,preparation_result
 c=row['content'];return {**task_summary(row),'job_id':row['id'],'state':row['state'],'revision':row['revision'],'stage':c['stage'],'completed_steps':list(c['completed_steps']),'error_code':c.get('error_code'),'attempts':c['attempts'],'result':preparation_result(c),'created_at':row['created_at'],'updated_at':row['updated_at']}
