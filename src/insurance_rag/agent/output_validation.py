"""Final assertions must reference current scoped evidence and exact tool results."""
from insurance_rag.agent.model_adapter import strict_json
from insurance_rag.tools.contracts import AnswerDraft
from insurance_rag.agent.presentation import context_notice

def validate_final(content,context):
 app,run,state,tools=context['app'],context['run'],context['state'],context['tools']
 raw=strict_json(content)
 if not isinstance(raw,dict) or set(raw)-{'action','claims','result_refs','message'} or raw.get('action') not in ('answer','unavailable'):raise ValueError('final_schema_invalid')
 if raw['action']=='unavailable':
  receipts=[]
  for ref,entry in state.get('refs',{}).items():
   if entry['revision']!=state['conditions_revision']:continue
   packet=app.packet(ref,context);data=packet.get('data',{})
   if packet['status']=='ok' and (data.get('path') and data.get('version') or data.get('action') in ('remember','update','forget')):receipts.append(ref)
  if receipts:
   receipt=validate_final(__import__('json').dumps({'action':'answer','claims':[],'result_refs':receipts[-1:]}),context);receipt['completion_scope']='verified_side_effect_receipt';return receipt
  return {'status':'unavailable','reason_code':'insufficient_verified_evidence','text':'当前资料或条件不足，未形成可核验的业务结论。','claims':[]}
 message=raw.get('message','')
 if not isinstance(message,str) or len(message)>8000:raise ValueError('invalid_answer_message')
 for (live_run,_),live in app.journal.live_messages.items():
  if live_run==run['run_id'] and live.get('role')=='tool':
   try:body=__import__('json').loads(live.get('content','')).get('data',{}).get('content','')
   except (ValueError,AttributeError):body=''
   if isinstance(body,str) and len(body)>64 and body in message:raise ValueError('local_file_fulltext_not_publishable')
 for (live_run,_),arguments in app.journal.live_calls.items():
  if live_run!=run['run_id']:continue
  try:body=__import__('json').loads(arguments).get('content','')
  except (ValueError,AttributeError):body=''
  if isinstance(body,str) and len(body)>64 and body in message:raise ValueError('local_file_fulltext_not_publishable')
 draft=AnswerDraft.model_validate({'claims':raw.get('claims',[])})
 results=[app.packet(ref,context) for ref in raw.get('result_refs',[])]
 if len(results)>8:raise ValueError('final_result_limit')
 evidence={};required=[];document_support=[]
 for ref,entry in state.get('refs',{}).items():
  if entry['revision']!=state['conditions_revision']:continue
  packet=app.packet(ref,context)
  evidence.update({e['evidence_id']:e for e in packet.get('evidence',[])})
  required+=packet.get('data',{}).get('required_source_quotes',[])
  if packet['status'] in ('ok','partial') and packet.get('data',{}).get('document_analysis'):document_support.append(packet)
 # Preserve source cards from the current revision when presenting document answers.
 if any(p.get('data',{}).get('document_analysis') for p in results):
  seen={p['data']['document_analysis']['analysis_id'] for p in results if p.get('data',{}).get('document_analysis')}
  for packet in document_support:
   aid=packet['data']['document_analysis']['analysis_id']
   if aid not in seen:results.append(packet);seen.add(aid)
  if len(results)>8:raise ValueError('final_result_limit')
 claims=[];rejected_claims=0
 verified_business=any(any(k in p.get('data',{}) for k in ('product_comparison','table_rules','table_values','benefit_table','product_example','source_tables','source_table','source_value','product_preparation','document_analysis')) for p in results)
 for claim in draft.claims:
  e=evidence.get(claim.evidence_id)
  quote=claim.quote
  if e and quote not in e['text']:
   candidate=quote.replace('\\r\\n','\r\n').replace('\\n','\n').replace('\\r','\r').replace('\\t','\t')
   if candidate in e['text']:quote=candidate
  if not e or quote not in e['text']:
   if verified_business:rejected_claims+=1;continue
   raise ValueError('claim_source_not_supported')
  tools.validate_source(e);start=e['text'].find(quote)
  claims.append({'evidence_id':e['evidence_id'],'quote':quote,'product_version_id':e['product_version_id'],'document_version_id':e['document_version_id'],'page_start':e['page_start'],'page_end':e['page_end'],'quote_range':[start,start+len(quote)]})
 if claims:
  for item in required:
   e=evidence.get(item['evidence_id'])
   if not e or item['quote'] not in e['text']:raise ValueError('required_quote_invalid')
   if not any(q['evidence_id']==item['evidence_id'] and item['quote'] in q['quote'] for q in claims):
    tools.validate_source(e);claims.append({'evidence_id':e['evidence_id'],'quote':item['quote'],'product_version_id':e['product_version_id'],'document_version_id':e['document_version_id'],'page_start':e['page_start'],'page_end':e['page_end']})
 for packet in results:
  if packet['status'] not in ('ok','partial'):raise ValueError('unsuccessful_result_not_answer')
 if not claims and not any(any(k in p.get('data',{}) for k in ('product_comparison','table_rules','table_values','benefit_table','product_example','source_tables','source_table','source_value','product_preparation','document_analysis','files','path','memory_id','memories','history','products','capability_view','profile','recommendation','analysis_record')) for p in results):
  if not message:raise ValueError('answer_requires_verified_business_result')
 if not claims and not results and not message:raise ValueError('answer_requires_verified_reference')
 user_request=state.get('user_request') or run['input'].get('message','')
 import re
 attempted={entry['tool'] for entry in state.get('refs',{}).values()}
 file_pattern=r'(?:请|帮我|替我)[^。；！？\n，,]*?(?:保存|写入|生成)[^。；！？\n，,]*?(?:文件|草稿|笔记|报告|\.md|\.csv)|(?:把|将)[^。；！？\n，,]*?(?:写入|保存到)'
 negated=r'(?:不(?:要|必|用|需)?|无需|勿|别|禁止)(?:再|帮我|替我|自动|直接|额外|另外|\s){0,3}(?:保存|写入|生成)'
 wants_file=any(not re.search(negated,m[0]) for m in re.finditer(file_pattern,user_request,re.I))
 if wants_file and not attempted.intersection({'write_file','patch_file'}):raise ValueError('requested_file_operation_not_executed')
 if state.get('last_analysis_id') and re.search(r'(?:整理|列出|给出)候选',user_request) and not any(p.get('data',{}).get('recommendation') for p in results):raise ValueError('requested_recommendation_not_assembled')
 if re.search(r'请记住|请忘记|忘记该|删除记忆',user_request) and not attempted.intersection({'memory','update_profile','forget_profile'}):raise ValueError('requested_memory_operation_not_executed')
 if message and not claims and not results and any(p.get('product_name') and p['product_name'] in user_request for p in tools.versions.values()):raise ValueError('product_answer_requires_source_reference')
 package_evidence=list(evidence.values())
 answer={'quality_status':'source_and_scope_checks_passed_semantic_review_required','status':'answered','text':'以下为当前来源和工具验证结果。未知项、开发资料和不可比条件保留原状态。','claims':claims}
 for packet in results:
  data=packet.get('data',{})
  for key in ('product_comparison','table_rules','table_values','benefit_table','product_example','source_tables','source_table','source_value','product_preparation','document_analysis','products','workflow_result_id','capability_view','profile','recommendation','analysis_record'):
   if key in data:answer[key]={k:v for k,v in data[key].items() if k!='_audit' or key in ('table_values','benefit_table')} if isinstance(data[key],dict) else data[key]
  for singular,plural in (('benefit_table','benefit_tables'),('document_analysis','document_analyses'),('capability_view','capability_views'),('source_table','source_table_windows'),('source_tables','source_table_catalogs')):
   if singular in data:answer.setdefault(plural,[]).append(answer[singular])
  from insurance_rag.tools.adapters.product_table_steps import validate_packet
  validate_packet(packet,context)
  if data.get('path') and data.get('version'):answer.setdefault('files',[]).append(data)
  if 'memories' in data or 'history' in data or 'memory_id' in data:answer['memory_result']=data
 if 'product_preparation' in answer:answer['text']='资料任务状态与可用能力见处理结果。'
 if 'document_analysis' in answer:answer['text']=answer['document_analysis']['text']
 if answer.get('files'):answer['text']='会话文件已读取或按预期版本保存；内容属于用户草稿，不是正式产品事实。'
 if answer.get('memory_result'):answer['text']='记忆操作已完成；历史讨论不作为产品事实或本次报价条件。'
 if rejected_claims:answer['warnings']=['unsupported_model_quotes_omitted_verified_business_results_only']
 if claims:answer['text']='以下内容摘自原件，请结合完整适用条件阅读：\n'+'\n\n'.join(q['quote']+' [第'+str(q['page_start'])+'页]' for q in claims)
 if message:
  answer['text']=message;answer['explanation']=message;answer['quality_status']='references_checked_model_explanation'
 pid=app.store.package(run,{'status':'partial','evidence':package_evidence,'warnings':[]})
 answer['evidence_package_id']=pid
 from insurance_rag.agent.presentation import display_result
 return display_result(answer)
