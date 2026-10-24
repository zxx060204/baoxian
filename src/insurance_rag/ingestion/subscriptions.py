"""Deliver preparation completion to the subscribed Agent using its existing supervisor."""
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.agent.store import Conflict
from insurance_rag.reliability.runtime import error_code

def wake_preparation_subscribers(app,owner,job_id,*,dispatch=None):
 store=IntakeStore(app.store);job=store.get('preparation_job',job_id,owner)
 if job['state'] not in ('ready','partial','failed'):return
 if dispatch is None:
  from insurance_rag.agent.supervisor import supervise
  dispatch=lambda rid,owner:supervise(rid,owner)
 ids=store.execute("SELECT id FROM insurance_onboarding_bundle_subscription WHERE owner_id=? AND state='pending'",(owner,)).fetchall()
 for (sid,) in ids:
  row=store.get('bundle_subscription',sid,owner)
  if row['state']!='pending' or row['content'].get('job_id')!=job_id:continue
  run=app.store.get(row['content']['run_id'],owner);clarification=run.get('clarification') or {}
  # The run will see the terminal job before pausing, or its supervisor will notify us after settling.
  if run['state'] in ('queued','running'):continue
  if run['state']!='waiting' or clarification.get('kind')!='document_processing' or clarification.get('job_id')!=job_id:
   store.update('bundle_subscription',sid,row['revision'],row['content'],'obsolete','subscription','run_no_longer_waiting');continue
  try:row=store.update('bundle_subscription',sid,row['revision'],row['content'],'dispatching','subscription','preparation_terminal')
  except Conflict:continue
  try:
   dispatch(run['run_id'],owner)
   final=app.store.get(run['run_id'],owner)
   waiting=final['state']=='waiting' and (final.get('clarification') or {}).get('kind')=='document_processing' and (final.get('clarification') or {}).get('job_id')==job_id
   body={**row['content'],'run_state':final['state'],'error_code':final.get('error_code')}
   store.update('bundle_subscription',sid,row['revision'],body,'failed' if waiting else 'delivered','subscription','resume_not_advanced' if waiting else 'completion_delivered')
  except Exception as exc:
   store.update('bundle_subscription',sid,row['revision'],{**row['content'],'error_code':error_code(exc)},'failed','subscription','resume_failed')
