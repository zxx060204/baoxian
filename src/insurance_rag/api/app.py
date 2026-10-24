"""Authenticated local API; event reads never trigger graph execution."""
import hmac,json,os,time,secrets
from contextlib import contextmanager
from typing import Any
from fastapi import FastAPI,Depends,Header,HTTPException,BackgroundTasks,Request
from fastapi.responses import StreamingResponse,FileResponse,HTMLResponse
from pathlib import Path
from pydantic import BaseModel,ConfigDict,Field
from insurance_rag.services.material_metadata import MaterialDates
from insurance_rag.agent.loop_bootstrap import open_loop_application as open_application
from insurance_rag.agent.store import Forbidden,Conflict
from insurance_rag.agent.runtime_errors import RuntimeSourceChanged,RuntimeBindingMismatch
from insurance_rag.reliability.runtime import error_code
from insurance_rag.reliability.diagnostics import OperationFailure
from insurance_rag.parsing.common import workspace_path,file_hash

class Input(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class RunInput(Input):
 attachments:list[dict[str,str]]=Field(default_factory=list,max_length=12)
 message:str=Field(min_length=1,max_length=4000)
 request_id:str=Field(min_length=1,max_length=100)
 product_version_id:str|None=None
 conditions:dict[str,Any]=Field(default_factory=dict)
class ResumeInput(Input):
 response:dict[str,Any]
 resume_id:str=Field(min_length=1,max_length=100)
 interrupt_id:str
 revision:int=Field(ge=0)

def create_app(factory=open_application,authenticate=None,*,local_test_session=False,local_admin_session=False):
 if local_admin_session and not local_test_session:raise ValueError('local_admin_requires_local_test_session')
 api=FastAPI(title='Insurance Agent',version='0.1.0')
 session_token=secrets.token_urlsafe(32) if local_test_session else None
 admin_session_token=secrets.token_urlsafe(32) if local_admin_session else None
 def local_request(request,*,navigation=False):
  host=request.url.hostname
  origin=request.headers.get('origin')
  top_level_get=navigation and request.method=='GET' and not origin and request.headers.get('sec-fetch-mode')=='navigate' and request.headers.get('sec-fetch-dest')=='document'
  return bool(request.client and request.client.host in ('127.0.0.1','::1') and host in ('127.0.0.1','localhost','::1') and (request.headers.get('sec-fetch-site') not in ('cross-site','same-site') or top_level_get) and (not origin or origin==str(request.base_url).rstrip('/')))
 def page(request,name):
  html=Path(__file__).with_name(name).read_text(encoding='utf8')
  if session_token:
   if not local_request(request,navigation=True):raise HTTPException(403,'local_session_requires_same_origin_loopback')
   html=html.replace('data-local-session="false"','data-local-session="true"')
  response=HTMLResponse(html,headers={'Cache-Control':'no-store','X-Frame-Options':'DENY'})
  if session_token:response.set_cookie('insurance_local_session',session_token,httponly=True,samesite='strict',path='/')
  return response
 def principal(request:Request,authorization:str=Header(default='')):
  if authenticate:return authenticate(authorization)
  if session_token and local_request(request) and hmac.compare_digest(request.cookies.get('insurance_local_session',''),session_token):return 'local-user'
  token=os.environ.get('INSURANCE_AGENT_API_TOKEN','')
  if not token:raise HTTPException(503,'api_auth_not_configured')
  if not hmac.compare_digest(authorization,'Bearer '+token):raise HTTPException(401,'unauthorized')
  return 'local-user'
 def administrator(request:Request,owner=Depends(principal),x_admin_token:str=Header(default='')):
  if admin_session_token and local_request(request) and hmac.compare_digest(request.cookies.get('insurance_admin_session',''),admin_session_token):return owner
  expected=os.environ.get('INSURANCE_ADMIN_API_TOKEN','')
  if expected and hmac.compare_digest(expected,x_admin_token):return owner
  raise HTTPException(403,'admin_authorization_required')
 @contextmanager
 def application():
  try:
   with factory() as app:yield app
  except HTTPException:raise
  except OperationFailure as exc:
   code=exc.diagnostic['error_code'];raise HTTPException(422 if code=='contract_or_quality_violation' else 503,exc.diagnostic) from None
  except Forbidden:raise HTTPException(404,'object_unavailable') from None
  except RuntimeSourceChanged:raise HTTPException(503,'runtime_source_changed_restart_required') from None
  except RuntimeBindingMismatch:raise HTTPException(409,'runtime_binding_mismatch') from None
  except Conflict:raise HTTPException(409,'execution_conflict') from None
  except ValueError:raise HTTPException(422,'invalid_request') from None
  except Exception as exc:raise HTTPException(503,error_code(exc)) from None
 def advance(rid,owner,**kw):
  try:
   if factory is open_application or getattr(factory,'__name__','')=='open_loop_application':
    from insurance_rag.agent.supervisor import supervise
    supervise(rid,owner,**kw)
   else:
    with factory() as app:app.drive(rid,owner,**kw)
  except Exception:pass # Public run state/lease supports an explicit recovery.
 @api.get('/',response_class=HTMLResponse)
 def index(request:Request):return page(request,'agent_loop.html')
 @api.get('/agent',response_class=HTMLResponse)
 def loop_page(request:Request):return page(request,'agent_loop.html')
 @api.get('/analysis',response_class=HTMLResponse)
 def analysis_page(request:Request):return page(request,'analysis.html')
 @api.get('/admin',response_class=HTMLResponse)
 def admin_page(request:Request):
  response=page(request,'product_ingestion.html')
  if admin_session_token:
   if not local_request(request):raise HTTPException(403,'local_admin_requires_loopback')
   response.set_cookie('insurance_admin_session',admin_session_token,httponly=True,samesite='strict',path='/')
  return response
 @api.post('/v1/threads')
 def thread(owner=Depends(principal)):
  with application() as app:return {'thread_id':app.new_thread(owner)}
 @api.post('/v1/threads/{tid}/runs',status_code=202)
 def run(tid:str,body:RunInput,background:BackgroundTasks,owner=Depends(principal)):
  with application() as app:
   extra={}
   if body.attachments:extra['attachments']=body.attachments
   r=app.submit(tid,owner,body.message,request_id=body.request_id,product_version_id=body.product_version_id,conditions=body.conditions,**extra)
  if r['state']=='queued':background.add_task(advance,r['run_id'],owner)
  return r
 @api.get('/v1/runs/{rid}')
 def status(rid:str,owner=Depends(principal)):
  with application() as app:return app.public(app.store.get(rid,owner))
 @api.post('/v1/runs/{rid}/resume',status_code=202)
 def resume(rid:str,body:ResumeInput,background:BackgroundTasks,owner=Depends(principal)):
  with application() as app:
   r=app.store.get(rid,owner);op,cached=app._resume_record(rid,body.resume_id,body.response)
   if cached:return app.public(app.store.get(rid,owner))
   app.validate_resume(r,body.response)
   if r['state']!='waiting' or r['revision']!=body.revision or r['clarification']['interrupt_id']!=body.interrupt_id:raise Conflict('stale_resume')
  background.add_task(advance,rid,owner,response=body.response,resume_id=body.resume_id,revision=body.revision,interrupt_id=body.interrupt_id)
  return {'run_id':rid,'state':'resume_accepted'}
 @api.post('/v1/runs/{rid}/cancel')
 def cancel(rid:str,owner=Depends(principal)):
  with application() as app:return app.cancel(rid,owner)
 @api.post('/v1/runs/{rid}/recover',status_code=202)
 def recover(rid:str,background:BackgroundTasks,owner=Depends(principal)):
  with application() as app:
   r=app.store.get(rid,owner)
   if hasattr(app,'validate_runtime'):app.validate_runtime(r)
   if r['state']=='failed':app.store.retry_failed(rid,owner)
  if r['state']=='running' and r['lease_expires_at']>time.time():raise HTTPException(409,'worker_still_active')
  background.add_task(advance,rid,owner);return {'run_id':rid,'state':'recovery_accepted'}
 @api.get('/v1/runs/{rid}/events')
 def events(rid:str,request:Request,after:int=0,last_event_id:str=Header(default=''),owner=Depends(principal)):
  if last_event_id:
   try:after=int(last_event_id)
   except ValueError:raise HTTPException(422,'invalid_event_id')
  if after<0:raise HTTPException(422,'invalid_event_id')
  with application() as app:app.store.get(rid,owner)
  def stream():
   cursor=after
   for _ in range(240):
    with factory() as app:
     run=app.store.get(rid,owner);rows=app.store.events(rid,owner,cursor);state=run['state']
     if hasattr(app,'public_events'):rows=app.public_events(run,rows)
    for e in rows:
     cursor=e['sequence'];yield 'id: '+str(cursor)+'\nevent: '+e['event']+'\ndata: '+json.dumps(e['data'],ensure_ascii=False)+'\n\n'
    if state in ('waiting','completed','failed','cancelled'):break
    yield ': heartbeat\n\n';time.sleep(.5)
  return StreamingResponse(stream(),media_type='text/event-stream',headers={'Cache-Control':'no-cache'})
 @api.get('/v1/evidence/{pid}')
 def evidence(pid:str,owner=Depends(principal)):
  with application() as app:return app.evidence(pid,owner)
 @api.get('/v1/evidence/{pid}/sources')
 def package_sources(pid:str,owner=Depends(principal)):
  with application() as app:return app.source_catalog(pid,owner)
 @api.get('/v1/evidence/{pid}/sources/{sid}/pdf')
 def package_source_pdf(pid:str,sid:str,owner=Depends(principal)):
  with application() as app:
   path=app.source_file(pid,sid,owner)
   return FileResponse(path,media_type='application/pdf',filename='insurance-source.pdf',headers={'X-Content-Type-Options':'nosniff'})
 @api.get('/v1/documents/{version}/source')
 def source(version:str,package_id:str,owner=Depends(principal)):
  with application() as app:
   packet=app.store.read_package(package_id,owner);run_id=app.store.execute('SELECT run_id FROM insurance_agent_evidence_package WHERE package_id=?',(package_id,)).fetchone()[0];run=app.store.get(run_id,owner);tools=app.tools_factory(run['release']);e=next((e for e in packet['evidence'] if e['document_version_id']==version),None)
   if not e:raise Forbidden('source_unavailable')
   tools.validate_source(e);path=workspace_path(e['source_file'],tools.root,must_exist=True)
   return FileResponse(path,media_type='application/pdf',filename=version+'.pdf')
 @api.get('/v1/products')
 def products(owner=Depends(principal)):
  with application() as app:
   release=app.release_factory();tools=app.tools_factory(release)
   catalog=tools.capability_catalog() if hasattr(tools,'capability_catalog') else {}
   from insurance_rag.ingestion.library import candidates as library_candidates
   return {'scope':'current_source_business','library_products':library_candidates(app),'products':[{**p,'business_capabilities':catalog.get(p['product_version_id'],{})} for p in tools.candidates()],'business_capabilities':catalog,'development_only':tools.dev}
 @api.get('/v1/products/{pv}/material-metadata')
 def material_metadata(pv:str,owner=Depends(principal)):
  from insurance_rag.services.material_metadata import metadata
  with application() as app:
   tools=app.tools_factory(app.release_factory())
   if pv not in tools.versions:raise Forbidden('product_unavailable')
   return metadata(app,owner,pv)
 @api.put('/v1/products/{pv}/material-metadata')
 def maintain_material_metadata(pv:str,body:MaterialDates,owner=Depends(administrator)):
  from insurance_rag.services.material_metadata import maintain
  with application() as app:
   tools=app.tools_factory(app.release_factory())
   if pv not in tools.versions:raise Forbidden('product_unavailable')
   return maintain(app,owner,pv,body.model_dump(mode='json'))

 @api.post('/v1/threads/{tid}/open')
 def open_thread(tid:str,owner=Depends(principal)):
  with application() as app:
   with app.store.transaction():
    context=app.store.thread(tid,owner);context['memory_flush_requested']=True
    app.store.execute('UPDATE insurance_agent_thread SET context=? WHERE thread_id=?',(json.dumps(context,ensure_ascii=False),tid))
   return {'thread_id':tid,'memory_extraction':'next_request'}
 @api.get('/v1/analyses')
 def analyses(thread_id:str|None=None,owner=Depends(principal)):
  with application() as app:return {'analyses':app.workflow_store.analyses(owner,thread_id)}
 @api.get('/v1/analyses/{analysis_id}')
 def analysis_record(analysis_id:str,owner=Depends(principal)):
  with application() as app:
   row=app.workflow_store.read_result(analysis_id,owner)
   run=app.store.get(row['run_id'],owner)
   app.validate_calculation_result(row['content'],run['release'])
   from insurance_rag.agent.presentation import display_result
   return {**row,'content':display_result(row['content']),'analysis_id':analysis_id,'release_id':run['release']['release_id'],'historical':True}
 @api.get('/health/live')
 def live():return {'status':'alive'}
 @api.get('/health/ready')
 def ready(owner=Depends(principal)):
  with application() as app:
   r=app.release_factory();tools=app.tools_factory(r);tools.availability();return {'status':'ready','release_id':r['release_id'],'development_only':tools.dev,'model_connection_tested':False}
 from insurance_rag.api.product_ingestion import install as install_product_ingestion
 install_product_ingestion(api,application,principal,start_workers=factory is open_application or getattr(factory,'__name__','')=='open_loop_application')
 @api.get('/product-upload',response_class=HTMLResponse)
 def product_upload_page(request:Request):return page(request,'product_ingestion.html')
 return api
