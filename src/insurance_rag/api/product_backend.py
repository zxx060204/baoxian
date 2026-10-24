"""Standalone local backend for reusable multi-product preparation."""
import secrets,hashlib
from pathlib import Path
from fastapi import FastAPI,Request,Depends,HTTPException
from fastapi.responses import HTMLResponse,JSONResponse
from insurance_rag.services.bootstrap import open_services
from insurance_rag.agent.store import Forbidden,Conflict
from insurance_rag.api.product_ingestion import install

def create_app(factory=open_services,*,start_workers=True):
 api=FastAPI(title='多产品资料录入');token=secrets.token_urlsafe(32)
 def cookie_name(request):
  # Cookies have no port isolation; separate concurrently running local backends.
  return 'product_ingestion_session_'+hashlib.sha256(request.url.netloc.encode()).hexdigest()[:16]
 def principal(request:Request):
  if not request.client or request.client.host not in ('127.0.0.1','::1','testclient') or not secrets.compare_digest(request.cookies.get(cookie_name(request),''),token):raise HTTPException(401,'open_local_page_first')
  if request.method not in ('GET','HEAD') and request.headers.get('origin')!=str(request.base_url).rstrip('/'):raise HTTPException(403,'same_origin_required')
  return 'local-product-administrator'
 @api.middleware('http')
 async def headers(request,call_next):
  r=await call_next(request);r.headers['Cache-Control']='no-store';r.headers['X-Content-Type-Options']='nosniff'
  if 'Content-Security-Policy' not in r.headers:r.headers['Content-Security-Policy']="default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'"
  return r
 @api.exception_handler(Forbidden)
 async def forbidden(request,exc):return JSONResponse({'detail':'object_unavailable'},status_code=404)
 @api.exception_handler(Conflict)
 async def conflict(request,exc):return JSONResponse({'detail':str(exc)},status_code=409)
 @api.exception_handler(ValueError)
 async def invalid(request,exc):return JSONResponse({'detail':str(exc) if str(exc).replace('_','').isalnum() and len(str(exc))<100 else 'invalid_request'},status_code=422)
 @api.get('/')
 def page(request:Request):
  if not request.client or request.client.host not in ('127.0.0.1','::1','testclient'):raise HTTPException(403,'loopback_only')
  r=HTMLResponse(Path(__file__).with_name('product_ingestion.html').read_text('utf8').replace('data-library-admin="false"','data-library-admin="true"'));r.set_cookie(cookie_name(request),token,httponly=True,samesite='strict');return r
 @api.get('/health/live')
 def live():return {'status':'alive','system':'multi_product_ingestion'}
 install(api,factory,principal,start_workers=start_workers,library_admin=True)
 return api
