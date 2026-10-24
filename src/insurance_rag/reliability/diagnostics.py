"""Small, payload-free diagnostics for source preparation and online answering."""
import json,re,time,uuid
from contextlib import contextmanager
from pathlib import Path
from .runtime import StageFailure,error_code

class OperationFailure(StageFailure):
 def __init__(self,diagnostic):
  super().__init__(diagnostic['error_code']);self.diagnostic=diagnostic

@contextmanager
def stage(root,name):
 try:yield
 except OperationFailure:raise
 except Exception as exc:
  detail={'diagnostic_id':uuid.uuid4().hex,'stage':name,'error_code':error_code(exc),'exception_type':type(exc).__name__,'time':time.time()}
  reason=str(exc)
  if re.fullmatch(r'(?:deepseek|bailian|opensearch)_http_[0-9]{3}|(?:deepseek|bailian)_connection_failed|all_search_routes_failed|shared_circuit_open|shared_concurrency_limit|shared_rate_limit',reason):detail['dependency_reason']=reason
  try:
   path=Path(root)/'data/ops/diagnostics.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
   with path.open('a',encoding='utf8') as stream:stream.write(json.dumps(detail,ensure_ascii=False)+'\n')
  except OSError:pass
  raise OperationFailure(detail) from None
