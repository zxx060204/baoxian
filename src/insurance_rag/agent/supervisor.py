"""Fenced process supervision with bounded, allowlisted child diagnostics."""
import json,os,subprocess,sys,time,uuid,tempfile
from pathlib import Path
from insurance_rag.agent.store import AgentStore,Conflict
from insurance_rag.agent.loop_config import LoopSettings
from insurance_rag.runtime_env import load_env
from insurance_rag.parsing.common import stable_hash
from insurance_rag.agent.worker_protocol import read_report

def supervise(rid,owner,**resume):
 root=Path(__file__).resolve().parents[3];load_env(root/'.env');limits=LoopSettings.from_environment();token=uuid.uuid4().hex
 spec={'run_id':rid,'owner':owner,'execution_token':token,'resume':resume}
 store=AgentStore.connect(os.environ['INSURANCE_POSTGRES_DSN']);process=None
 def fail_owned(code):
  final=store.get(rid,owner)
  if final['state']=='queued':
   try:final=store.claim_run(rid,owner,seconds=limits.timeout_seconds,expected_revision=final['revision'],execution_token=token)
   except Conflict:return
  if final and final['state']=='running' and final['lease_token']==token:
   try:store.settle(final,'failed',error=code)
   except Conflict:pass
 try:
  initial=store.get(rid,owner);spec['expected_bundle_hash']=stable_hash(initial['bundle'])
  initial_events=store.events(rid,owner);event_cursor=initial_events[-1]['sequence'] if initial_events else 0
  spec['runtime']='loop'
  if initial['state'] in ('completed','failed','cancelled','waiting') and not resume and not (initial['state']=='waiting' and (initial.get('clarification') or {}).get('kind')=='document_processing'):
   return {k:initial[k] for k in ('run_id','state','revision','error_code')}
  active_timeout=max(.001,limits.timeout_seconds-initial['budget'].get('active_milliseconds',0)/1000)
  # A file, rather than an unread PIPE, avoids stdout backpressure deadlocks.
  with tempfile.TemporaryFile() as output:
   try:
    process=subprocess.Popen([sys.executable,'-X','utf8',str(root/'scripts/agent_worker.py')],cwd=root,stdin=subprocess.PIPE,stdout=output,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    process.stdin.write(json.dumps(spec).encode('utf8'));process.stdin.close();began=time.monotonic()
   except OSError:
    fail_owned('worker_startup_failed');store.event(rid,'worker_exited',{'phase':'initialize','error_code':'worker_startup_failed'})
    return {k:v for k,v in store.get(rid,owner).items() if k in ('run_id','state','revision','error_code')}
   deadline=False
   while process.poll() is None:
    current=store.get(rid,owner)
    if current['state']=='cancelled':process.kill();process.wait(timeout=5);break
    if time.monotonic()-began>=active_timeout:
     process.kill();process.wait(timeout=5);deadline=True
     break
    time.sleep(.1)
   output.seek(0);raw=output.read(4097);report=read_report(raw,rid)
   diagnostic=report or {'phase':'execute' if not raw else 'initialize','error_code':'worker_exited_without_completion' if not raw else 'worker_protocol_invalid'}
   # Preserve already charged work and account for the unreported tail only.
   if deadline or report is None:
    current=store.get(rid,owner)
    if current['state']=='running' and current['lease_token']==token:
     elapsed=int((time.monotonic()-began)*1000)
     charged=current['budget'].get('active_milliseconds',0)-initial['budget'].get('active_milliseconds',0)
     usage={'active_milliseconds':max(0,elapsed-charged),'unknown_inflight_operations':1}
     events=store.events(rid,owner,event_cursor)
     model_events=[e['event'] for e in events if e['event'] in ('model_started','model_completed')]
     if model_events and model_events[-1]=='model_started':
      usage.update(unknown_usage_requests=1,conservative_unknown_tokens=limits.context_bytes+8192)
     try:store.add_usage(current,usage)
     except Conflict:pass
   store.event(rid,'worker_exited',{'phase':diagnostic['phase'],'error_code':'deadline_exceeded' if deadline else diagnostic.get('error_code'),'exit_code':process.returncode})
   fail_owned('deadline_exceeded' if deadline else diagnostic.get('error_code') or 'worker_exited_without_completion')
  final=store.get(rid,owner)
  if final['state']=='waiting' and (final.get('clarification') or {}).get('kind')=='document_processing':
   from types import SimpleNamespace
   from insurance_rag.ingestion.subscriptions import wake_preparation_subscribers
   wake_preparation_subscribers(SimpleNamespace(root=root,store=store),owner,final['clarification']['job_id'])
  return {k:v for k,v in store.get(rid,owner).items() if k in ('run_id','state','revision','error_code')}
 finally:
  if process and process.poll() is None:process.kill();process.wait(timeout=5)
  if process and process.stdin and not process.stdin.closed:process.stdin.close()
  store.close()
