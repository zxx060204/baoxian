"""Authorization, strict arguments, source checks and bounded tool execution."""
import time
from insurance_rag.agent.model_adapter import strict_json
from insurance_rag.tools.contracts import result
from insurance_rag.reliability.runtime import request_budget,error_code
from insurance_rag.agent.store import Conflict,BudgetExceeded,Forbidden

def execute(registry,call,context):
 app,run,state=context['app'],context['run'],context['state'];store=app.store
 spec=registry.specs.get(call['name']);started=time.monotonic()
 if not spec:return result('unavailable',reason_code='unknown_tool')
 if not set(spec.permissions)<=set(context['permissions']):return result('unavailable',reason_code='tool_not_authorized')
 if spec.availability_check and not spec.availability_check(context):return result('unavailable',reason_code='tool_not_available')
 try:
  arguments=strict_json(call['arguments'])
  if not isinstance(arguments,dict):raise ValueError('object_required')
  if arguments.get('__local_content_omitted__') and call['name'] in ('write_file','patch_file'):
   from insurance_rag.tools.adapters.insurance import workspace
   try:
    current=workspace(context).read(arguments['path'],0,1)
    if arguments.get('file_hash')==current['version']:return result('ok',data={k:v for k,v in current.items() if k!='content'})
   except (OSError,ValueError):pass
   return result('unavailable',reason_code='local_content_requires_resubmission',data={'instruction':'本地文件正文未持久化；请读取已存在文件或重新生成内容，不猜测已完成写入。'})
  parsed=spec.input_model.model_validate(arguments)
 except (ValueError,TypeError) as exc:
  missing=[];hints={}
  if call['name']=='memory' and isinstance(locals().get('arguments'),dict):
   if arguments.get('action') in ('update','forget'):
    missing=[k for k in ('memory_id','expected_revision') if arguments.get(k) is None]
    hints={'instruction':'update/forget必须携带memory_id和expected_revision，使用快照或search的revision；forget不传content/query。'}
  elif hasattr(exc,'errors'):
   missing=['.'.join(map(str,e['loc'])) for e in exc.errors() if e.get('type')=='missing']
  return result('needs_clarification',reason_code='invalid_tool_arguments',missing_fields=missing,data=hints)
 store.guard(run)
 store.reserve(run,'tool_calls',app.limits.max_tool_calls)
 with store.transaction():store.guard(run);store.event(run['run_id'],'tool_started',{'tool':spec.name,'invocation_id':call['invocation_id']})
 try:
  with request_budget(spec.timeout):
   output=spec.handler(parsed.model_dump(),context)
  store.guard(run)
  if not isinstance(output,dict) or output.get('status') not in ('ok','partial','needs_clarification','no_evidence','conflict','unavailable'):raise ValueError('invalid_tool_result')
  for evidence in output.get('evidence',[]):context['tools'].validate_source(evidence)
 except Conflict as exc:
  if str(exc)=='comparison_context_invalid_or_stale':output=result('conflict',reason_code='comparison_context_invalid_or_stale')
  else:raise
 except Forbidden as exc:
  output=result('conflict' if str(exc)=='result_reference_unavailable_or_stale' else 'unavailable',reason_code='result_reference_unavailable_or_stale' if str(exc)=='result_reference_unavailable_or_stale' else 'source_unavailable')
 except BudgetExceeded:raise
 except ValueError as exc:
  code=str(exc)
  if code.startswith('invalid_agent_') or code in ('condition_requires_user_evidence','condition_value_not_supported'):output=result('conflict',reason_code=code,data={'instruction':'按工具Schema使用规范英文条件键和枚举，并引用用户原话；不要自行增加条件。'})
  else:output=result('unavailable',reason_code='contract_or_quality_violation')
 except Exception as exc:output=result('unavailable',reason_code=error_code(exc),retryable=False)
 return {**output,'invocation_id':call['invocation_id'],'conditions_revision':state['conditions_revision'],'schema_version':2,'timings':{'seconds':round(time.monotonic()-started,4)}}
