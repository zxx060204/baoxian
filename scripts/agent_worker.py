"""Execute the existing native loop using the supervisor's allowlisted protocol."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.agent.worker_protocol import failure

def main():
 run_id='';phase='initialize'
 try:
  spec=json.loads(sys.stdin.buffer.read(16385))
  if not isinstance(spec,dict) or spec.get('runtime')!='loop' or not all(isinstance(spec.get(k),str) and spec[k] for k in ('run_id','owner','execution_token','expected_bundle_hash')) or not isinstance(spec.get('resume',{}),dict):raise ValueError('invalid_worker_spec')
  run_id=spec['run_id']
  from insurance_rag.agent.loop_bootstrap import open_loop_application
  from insurance_rag.parsing.common import stable_hash
  from insurance_rag.agent.runtime_errors import RuntimeBindingMismatch
  with open_loop_application() as app:
   if stable_hash(app.bundle)!=spec['expected_bundle_hash']:raise RuntimeBindingMismatch('runtime_binding_mismatch')
   phase='execute';result=app.drive(run_id,spec['owner'],execution_token=spec['execution_token'],**spec.get('resume',{}))
  report={'run_id':run_id,'state':result['state'],'error_code':result.get('error_code'),'phase':'complete'}
 except Exception as exc:report=failure(exc,run_id,phase)
 sys.stdout.write(json.dumps(report,ensure_ascii=False));sys.stdout.flush()
 return 1 if report['state']=='failed' else 0
if __name__=='__main__':raise SystemExit(main())
