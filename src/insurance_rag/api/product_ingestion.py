"""Shared private ingestion endpoints for backend and authenticated Agent API."""
import hashlib,os,uuid,time
from pathlib import Path
from fastapi import Request,Depends,HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel,ConfigDict,Field
from typing import Literal
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.ingestion.store import asset_folder,register_asset,create_job,public_job,bundle,get_asset
from insurance_rag.ingestion.service import launch,analyze
from insurance_rag.agent.store import Conflict
class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class AssetInput(Strict):asset_id:str;document_type:Literal['terms','brochure','premium_rate','cash_value','other']
class PrepareInput(Strict):
 refresh:bool=False
 request_id:str=Field(min_length=1,max_length=100)
 assets:list[AssetInput]=Field(min_length=1,max_length=12)
 product_name:str=Field(default='',max_length=300)
 company:str=Field(default='',max_length=300)
 version_label:str=Field(default='',max_length=300)
class PublicationInput(Strict):published:bool=True
class AskInput(Strict):question:str=Field(min_length=1,max_length=2000)
class IllustrationInput(Strict):
 inputs:dict
 metrics:list[str]|None=Field(default=None,max_length=128)

def install(api,application,principal,*,start_workers=True,library_admin=False):
 from .current_source_modules import install as install_current_modules
 install_current_modules(api,application,principal)
 if library_admin:
  from insurance_rag.services.material_metadata import MaterialDates
  @api.get('/v1/product-ingestion/bundles/{id}/material-metadata')
  def read_material_dates(id:str,owner=Depends(principal)):
   from insurance_rag.services.material_metadata import metadata
   with application() as app:
    parent=bundle(app,owner,id);return metadata(app,parent['owner_id'],parent['product_version_id'])
  @api.put('/v1/product-ingestion/bundles/{id}/material-metadata')
  def save_material_dates(id:str,body:MaterialDates,owner=Depends(principal)):
   from insurance_rag.services.material_metadata import maintain
   from insurance_rag.agent.store import Forbidden
   with application() as app:
    parent=bundle(app,owner,id)
    if parent['owner_id']!=owner:raise Forbidden('owned_product_required')
    return maintain(app,owner,parent['product_version_id'],body.model_dump(mode='json'))
  @api.put('/v1/product-ingestion/bundles/{id}/publication')
  def publish_product(id:str,body:PublicationInput,owner=Depends(principal)):
   from insurance_rag.ingestion.library import publish
   with application() as app:
    parent=bundle(app,owner,id)
    if parent['content'].get('scope')=='current_source_business':
     from insurance_rag.ingestion.source_publication import publish_source
     row=publish_source(app,owner,id,body.published)
    else:raise ValueError('current_source_bundle_required')
    return {'bundle_id':id,'state':row['state']}
 @api.get('/v1/product-library')
 def product_library(owner=Depends(principal)):
  from insurance_rag.ingestion.library import candidates
  with application() as app:return {'products':candidates(app)}
 if start_workers:
  def recover_preparations():
   from insurance_rag.parsing.recovery import process_alive
   from insurance_rag.ingestion.source_package import PREPARATION_POLICY
   from insurance_rag.ingestion.source_workflow import POLICY as WORKFLOW_POLICY
   with application() as app:
    for row in IntakeStore(app.store).list('preparation_job',limit=500):
     c=row['content']
     if c.get('preparation_policy')!=PREPARATION_POLICY or c.get('workflow_policy')!=WORKFLOW_POLICY or c.get('attempts',0)>=3:continue
     if row['state']=='queued' or row['state']=='running' and process_alive(c.get('worker_pid')) is False:launch(app.root,row['owner_id'],row['id'])
  from contextlib import asynccontextmanager
  previous_lifespan=api.router.lifespan_context
  @asynccontextmanager
  async def lifespan(app):
   async with previous_lifespan(app) as state:
    recover_preparations()
    yield state
  api.router.lifespan_context=lifespan
 @api.post('/v1/product-ingestion/uploads')
 async def upload(request:Request,owner=Depends(principal)):
  with application() as app:folder=asset_folder(app.root,owner)
  folder.mkdir(parents=True,exist_ok=True);temp=folder/(uuid.uuid4().hex+'.upload');h=hashlib.sha256();size=0;head=b''
  try:
   with temp.open('wb') as f:
    async for block in request.stream():
     size+=len(block)
     if size>64*1024*1024:raise HTTPException(413,'pdf_exceeds_64mb')
     head=(head+block)[:1024] if len(head)<1024 else head;h.update(block);f.write(block)
   if not head.startswith(b'%PDF-'):raise ValueError('invalid_pdf_header')
   dest=folder/(h.hexdigest()+'.pdf')
   if not dest.exists():os.replace(temp,dest)
   from insurance_rag.parsing.common import file_hash
   if file_hash(dest)!=h.hexdigest():raise Conflict('uploaded_asset_changed')
   with application() as app:
    row=register_asset(app,owner,dest,request.headers.get('x-filename','document.pdf'));return {'asset_id':row['id'],'sha256':row['content']['sha256'],'bytes':size}
  finally:
   if temp.exists():temp.unlink()
 @api.post('/v1/product-ingestion/jobs',status_code=202)
 def prepare(body:PrepareInput,owner=Depends(principal)):
  with application() as app:
   row=create_job(app,owner,body.request_id,[a.model_dump() for a in body.assets],product_name=body.product_name,company=body.company,version_label=body.version_label,refresh=body.refresh)
   if start_workers and row['state']=='queued':launch(app.root,owner,row['id'])
   return public_job(row)
 @api.get('/v1/product-ingestion/jobs')
 def listing(owner=Depends(principal)):
  with application() as app:return {'jobs':[public_job(r) for r in IntakeStore(app.store).list('preparation_job',owner=owner)]}
 @api.get('/v1/product-ingestion/jobs/{id}')
 def status(id:str,owner=Depends(principal)):
  with application() as app:return public_job(IntakeStore(app.store).get('preparation_job',id,owner))
 @api.post('/v1/product-ingestion/jobs/{id}/retry',status_code=202)
 def retry(id:str,owner=Depends(principal)):
  with application() as app:
   row=IntakeStore(app.store).get('preparation_job',id,owner)
   from insurance_rag.parsing.recovery import process_alive
   if row['state'] not in ('failed','queued','partial') and not (row['state']=='running' and (process_alive(row['content'].get('worker_pid')) is False or row['content'].get('lease_expires',0)<time.time())):raise Conflict('job_not_retryable')
   if row['content']['attempts']>=3:raise ValueError('preparation_attempt_limit')
   if start_workers:launch(app.root,owner,id)
   return {'job_id':id,'state':'retry_accepted'}
 @api.get('/v1/product-ingestion/bundles/{id}')
 def detail(id:str,owner=Depends(principal)):
  with application() as app:
   from insurance_rag.ingestion.assessment import read_rules
   row=bundle(app,owner,id);c=row['content']
   from insurance_rag.ingestion.readiness import current_capabilities
   if c.get('scope')=='current_source_business':return {'bundle_id':id,'scope':c['scope'],'identity':c['identity'],'documents':[{'document_type':d['document_type'],'document_version_id':d['document_version_id'],'pages':d.get('pages')} for d in c['documents']],'capabilities':current_capabilities(app,c),'source_package_ref':c['source_package_ref'],'result_refs':c['result_refs'],'metrics':[],'profile_id':None,'table_facts':[],'extraction':{'coverage':{'status':'source_results_prepared' if c.get('result_refs',{}).get('rules') or c.get('result_refs',{}).get('examples') else 'formula_and_cases_not_prepared'}}}
   raise ValueError('retired_bundle_requires_migration')

 @api.post('/v1/product-ingestion/bundles/{id}/analyze')
 def ask(id:str,body:AskInput,owner=Depends(principal)):
  with application() as app:
   row=analyze(app,owner,id,body.question);c=row['content'];return {'analysis_id':row['id'],'content_hash':row['content_hash'],**{k:c[k] for k in ('bundle_id','product_name','question','text','status','retrieval','capabilities')},'claims':[{'quote':q['quote'],'document_version_id':q['source']['document_version_id'],'page_start':q['page_start'],'page_end':q['page_end']} for q in c['claims']]}
 @api.get('/v1/product-ingestion/bundles/{id}/rules')
 def saved_rules(id:str,include_evidence:bool=False,owner=Depends(principal)):
  from insurance_rag.ingestion.assessment import read_rules
  with application() as app:
   return read_rules(app,owner,id,include_evidence=include_evidence)
 @api.get('/v1/product-ingestion/bundles/{id}/illustration-examples')
 def illustration_examples(id:str,case_id:str|None=None,table_id:str|None=None,row_start:int=0,row_count:int=20,column_start:int=0,column_count:int=20,owner=Depends(principal)):
  with application() as app:
   parent=bundle(app,owner,id)
   if parent['content'].get('scope')=='current_source_business':
    from insurance_rag.ingestion.source_rule_reading import example_packet
    return example_packet(app,parent['content'],case_id=case_id,table_id=table_id,row_start=row_start,row_count=row_count,column_start=column_start,column_count=column_count)
   raise ValueError('retired_bundle_requires_migration')

 @api.post('/v1/product-ingestion/bundles/{id}/illustrate')
 def illustrate(id:str,body:IllustrationInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_execution import execute_collection
  with application() as app:return execute_collection(app,owner,id,body.inputs,metrics=body.metrics)

 @api.get('/v1/product-ingestion/bundles/{id}/documents/{docid}/pdf')
 def source(id:str,docid:str,owner=Depends(principal)):
  with application() as app:
   parent=bundle(app,owner,id);c=parent['content'];doc=next((d for d in c['documents'] if d['document_version_id']==docid),None)
   if doc is None:raise HTTPException(404,'source_unavailable')
   asset=get_asset(app,parent['owner_id'],doc['asset_id']);return FileResponse(Path(app.root)/asset['content']['path'],media_type='application/pdf',filename=doc['document_type']+'.pdf',content_disposition_type='inline',headers={'X-Content-Type-Options':'nosniff','Content-Security-Policy':"frame-ancestors 'self'"})
