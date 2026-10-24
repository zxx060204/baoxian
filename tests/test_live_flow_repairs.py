"""Regressions for live-run deadlines and handoffs, without product-specific extraction rules."""
import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
from insurance_rag.parsing.common import write_json,stable_hash
from insurance_rag.ingestion.source_package import read_bound,reference
from insurance_rag.ingestion.source_execution import load_execution,POLICY,PROMPT
from insurance_rag.agent.context import ContextEngine
from insurance_rag.tools.adapters.analysis import recommend

class BoundExecutionCacheTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.root=Path(self.tmp.name);self.app=SimpleNamespace(root=self.root)
  self.dep=self.root/'source.bin';self.dep.write_bytes(b'original source');self.dep_ref=reference(self.root,self.dep)
  write_json(self.root/'package.json',{'id':'source'});self.pkg=reference(self.root,self.root/'package.json')
  write_json(self.root/'bindings.json',{'bound':True});self.binding=reference(self.root,self.root/'bindings.json')
  data={'scope':'current_source_execution','source_package_ref':self.pkg,'source_package_id':'source-package:fixture','rules_ref':None,'bindings_ref':self.binding,'versions':{'policy':POLICY,'prompt':stable_hash(PROMPT)},'targets':[]}
  write_json(self.root/'execution.json',data);self.content={'source_package_ref':self.pkg,'source_package_id':'source-package:fixture','result_refs':{'execution':reference(self.root,self.root/'execution.json')}}
 def validator(self,root,pkg,binding):
  for ref in (pkg,binding,self.dep_ref):read_bound(root,ref)
  return {},[{'value':'verified-source-value'}]
 def test_reuses_semantic_validation_and_rehashes_every_dependency(self):
  with patch('insurance_rag.ingestion.source_execution_bindings.validate_bindings',side_effect=self.validator) as validate:
   a=load_execution(self.app,self.content,values=True);b=load_execution(self.app,self.content,values=True)
   self.assertEqual(a,b);self.assertEqual(validate.call_count,1)
   stamp=self.dep.stat();self.dep.write_bytes(b'modified source');os.utime(self.dep,ns=(stamp.st_atime_ns,stamp.st_mtime_ns))
   with self.assertRaisesRegex(ValueError,'source_artifact_changed'):load_execution(self.app,self.content,values=True)
 def test_cache_is_not_shared_between_runs(self):
  with patch('insurance_rag.ingestion.source_execution_bindings.validate_bindings',side_effect=self.validator) as validate:
   load_execution(self.app,self.content,values=True);load_execution(SimpleNamespace(root=self.root),self.content,values=True)
   self.assertEqual(validate.call_count,2)
 def test_failure_is_never_cached_as_valid(self):
  with patch('insurance_rag.ingestion.source_execution_bindings.validate_bindings',side_effect=[ValueError('bad_source'),({},[])]) as validate:
   with self.assertRaises(ValueError):load_execution(self.app,self.content,values=True)
   load_execution(self.app,self.content,values=True);self.assertEqual(validate.call_count,2)

class HandoffRegressionTests(unittest.TestCase):
 def test_large_preparation_status_is_complete_navigation_not_empty_preview(self):
  job={'job_id':'job','state':'partial','bundle_id':'selected','qa_ready':True,'capabilities':{'illustration':{'status':'partial','ready_count':7,'targets':[{'source':'x'*50000}]}}}
  wire=ContextEngine().wire({'status':'partial','data':{'product_preparation':job}},'packet')
  self.assertTrue(wire['reference_ready']);self.assertFalse(wire['read_before_answer_required']);self.assertEqual(wire['data']['product_preparation']['bundle_id'],'selected');self.assertLess(len(json.dumps(wire)),2500)
 def test_historical_comparison_exposes_the_already_selected_sources(self):
  record={'result_id':'history','content':{'product_comparison':{'products':[{'bundle_id':'chosen','product':'Example','inputs':{'entry_age':30}}]}}}
  wire=ContextEngine().wire({'status':'ok','data':{'analysis_record':record}},'packet')
  self.assertEqual(wire['data']['analysis_record']['products'][0]['bundle_id'],'chosen')
 def recommendation_context(self):
  profile={'values':{},'facts':[],'conflicts':[]};app=SimpleNamespace(profile=SimpleNamespace(snapshot=Mock(return_value=profile)),journal=SimpleNamespace(messages=lambda r:[{'role':'user','content':'继续整理候选，本次重视流动性，不保存长期画像'}]),packet=lambda ref,c:{'status':'ok','data':{'bundle_id':'chosen'}})
  c={'app':app,'owner':'alice','run':{'thread_id':'thread'},'state':{},'tools':SimpleNamespace(versions={})}
  args={'priority_quote':'重视流动性','candidates':[{'bundle_id':'chosen','reason':'现金价值未知，保留不足','result_refs':['history'],'disposition':'insufficient_information'}]}
  return profile,c,args
 def test_temporary_priority_needs_no_profile_write_and_keeps_unknown_unranked(self):
  profile,c,args=self.recommendation_context()
  with patch('insurance_rag.ingestion.store.bundle',return_value={'content':{'identity':{'product_version_id':'version','product_name':'Example'}}}):r=recommend(args,c)
  value=r['data']['recommendation'];self.assertEqual(value['profile']['goal'],'重视流动性');self.assertEqual(profile['values'],{});self.assertIsNone(value['candidates'][0]['rank']);self.assertEqual(value['candidates'][0]['eligibility'],'not_fully_verified')
 def test_temporary_priority_still_requires_user_evidence(self):
  _,c,args=self.recommendation_context();args['priority_quote']='模型自拟目标'
  self.assertEqual(recommend(args,c)['reason_code'],'recommendation_priority_requires_user_quote')


class LiveCompletionRegressionTests(unittest.TestCase):
 def test_budget_completion_preserves_verified_recommendation(self):
  import test_native_loop as native
  from insurance_rag.tools.registry import ToolSpec
  from insurance_rag.tools.contracts import StrictModel,result
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown)
  app=f.app([native.tool('recommend_products',{})],max_total_tokens=20)
  class Empty(StrictModel):pass
  def recommend_ready(args,c):
   c['app'].store.add_usage(c['run'],{'prompt_tokens':20})
   return result('partial',data={'recommendation':{'candidates':[{'product_name':'Example','disposition':'insufficient_information','rank':None}]}})
  app.registry.specs['recommend_products']=ToolSpec('recommend_products','fixture verified recommendation',Empty,recommend_ready)
  v=app.drive(f.submit(app,'继续整理候选')['run_id'],'alice')
  self.assertEqual(v['state'],'completed',v);self.assertEqual(v['result']['status'],'partial');self.assertIn('recommendation',v['result']);self.assertEqual(v['budget']['llm_calls'],1)
 def test_known_hash_accepts_short_company_label_without_switching_identity(self):
  from insurance_rag.ingestion.documents import identify
  with tempfile.TemporaryDirectory() as folder:
   root=Path(folder);write_json(root/'layout.json',{'metadata':{'company_id':'Alpha甲方保险','product_name':'标准合同','product_id':'product:canonical'},'pages':[]})
   doc={'document_type':'terms','source_sha256':'hash','layout_file':'layout.json','layout_sha256':reference(root,root/'layout.json')['sha256']};app=SimpleNamespace(root=root)
   with patch('insurance_rag.ingestion.documents.known_documents',return_value=[doc]),patch('insurance_rag.ingestion.documents.get_asset',return_value={'content':{'sha256':'hash'}}):
    v=identify(app,'alice',[{'asset_id':'asset','document_type':'terms','sha256':'hash'}],{'company':'甲方','product_name':'标准合同','version_label':'uploaded-version'})
    self.assertEqual(v['company_id'],'Alpha甲方保险');self.assertEqual(v['product_id'],'product:canonical')
    with self.assertRaisesRegex(ValueError,'declared_company_conflict'):identify(app,'alice',[{'asset_id':'asset','document_type':'terms','sha256':'hash'}],{'company':'乙方保险','product_name':'标准合同','version_label':''})
 def test_resumed_run_starting_another_finished_job_is_not_stranded(self):
  import io
  import test_native_loop as native
  from insurance_rag.ingestion.subscriptions import wake_preparation_subscribers
  from insurance_rag.onboarding.store import IntakeStore
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown);app=f.app([]);row=f.submit(app,'处理附件');rid=row['run_id'];run=f.store.claim_run(rid,'alice')
  def clarification(job):return {'kind':'document_processing','job_id':job,'interrupt_id':job}
  f.store.settle(run,'waiting',clarification=clarification('first'));intake=IntakeStore(f.store)
  for job in ('first','second'):
   intake.create('preparation_job',job,'alice','',{},'failed');intake.create('bundle_subscription','sub-'+job,'alice','',{'job_id':job,'run_id':rid})
  calls=[]
  def child(*a,**k):
   current=f.store.get(rid,'alice');claimed=f.store.claim_run(rid,'alice',expected_revision=current['revision']);calls.append(1)
   if len(calls)==1:f.store.settle(claimed,'waiting',clarification=clarification('second'))
   else:f.store.settle(claimed,'completed',result={'status':'answered','text':'terminal job explained'})
   return SimpleNamespace(stdin=io.BytesIO(),poll=lambda:0,returncode=0)
  with patch('insurance_rag.agent.supervisor.load_env'),patch.dict('os.environ',{'INSURANCE_POSTGRES_DSN':'unused-test'}),patch('insurance_rag.agent.supervisor.AgentStore.connect',return_value=f.store),patch.object(f.store,'close'),patch('insurance_rag.agent.supervisor.subprocess.Popen',side_effect=child):
   wake_preparation_subscribers(SimpleNamespace(store=f.store),'alice','first')
  self.assertEqual(len(calls),2);self.assertEqual(f.store.get(rid,'alice')['state'],'completed');self.assertEqual(intake.get('bundle_subscription','sub-first')['state'],'delivered');self.assertEqual(intake.get('bundle_subscription','sub-second')['state'],'delivered')


 def test_last_requested_deterministic_tool_can_finish_at_token_limit(self):
  import test_native_loop as native
  from insurance_rag.tools.registry import ToolSpec
  from insurance_rag.tools.contracts import StrictModel,result
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown)
  class ExhaustedReply(native.Scripted):
   def chat(self,*a,**k):
    reply=super().chat(*a,**k);self.usage['prompt_tokens']+=20;return reply
  model=ExhaustedReply([native.tool('recommend_products',{})]);app=f.app(model,max_total_tokens=20)
  class Empty(StrictModel):pass
  handler=Mock(return_value=result('partial',data={'recommendation':{'candidates':[{'product_name':'Example','rank':None}]}}));app.registry.specs['recommend_products']=ToolSpec('recommend_products','verified fixture',Empty,handler)
  v=app.drive(f.submit(app,'整理候选')['run_id'],'alice');self.assertEqual(v['state'],'completed',v);self.assertIn('recommendation',v['result']);handler.assert_called_once();self.assertEqual(v['budget']['llm_calls'],1)
 def test_valid_final_answer_needs_no_more_model_budget(self):
  import test_native_loop as native
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown)
  class ExhaustedReply(native.Scripted):
   def chat(self,*a,**k):
    reply=super().chat(*a,**k);self.usage['prompt_tokens']+=20;return reply
  model=ExhaustedReply([{'role':'assistant','content':json.dumps({'action':'answer','message':'你好','claims':[],'result_refs':[]})}]);app=f.app(model,max_total_tokens=20)
  v=app.drive(f.submit(app,'说一句你好')['run_id'],'alice');self.assertEqual(v['state'],'completed',v);self.assertEqual(v['result']['text'],'你好');self.assertEqual(v['budget']['llm_calls'],1)
 def test_model_using_tools_cannot_bypass_exhausted_token_budget(self):
  import test_native_loop as native
  from insurance_rag.agent.store import BudgetExceeded
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown);app=f.app([],max_total_tokens=20);run=f.store.claim_run(f.submit(app,'测试')['run_id'],'alice');f.store.add_usage(run,{'prompt_tokens':20});app.model.structured=Mock(return_value={'ok':True})
  with self.assertRaises(BudgetExceeded):app.structured(run,'test',{})
  app.model.structured.assert_not_called()

class CoordinatedBudgetTests(unittest.TestCase):
 def test_defaults_and_environment_override(self):
  from insurance_rag.agent.loop_config import LoopSettings
  self.assertEqual(LoopSettings().max_total_tokens,500000)
  self.assertEqual(LoopSettings.from_environment({}).max_total_tokens,500000)
  self.assertEqual(LoopSettings.from_environment({'INSURANCE_LOOP_MAX_TOTAL_TOKENS':'250000'}).max_total_tokens,250000)
 def test_cumulative_pressure_compacts_small_context_without_summary_call(self):
  state={'conditions_revision':1,'release_id':'r','execution_budget':{'conserve':True,'remaining_tokens':100}}
  messages=[{'role':'user','content':'old task '+str(i)} for i in range(6)]
  summary=Mock(side_effect=AssertionError('no extra model call under pressure'))
  rows,checkpoint=ContextEngine(96000).build('system',messages,state,[],summarize=summary)
  self.assertGreater(checkpoint['boundary'],0);self.assertEqual(checkpoint['summary']['kind'],'reference_summary');summary.assert_not_called()
  self.assertEqual([m['content'] for m in rows[-2:]],['old task 4','old task 5'])

class SustainedAcceptanceRegressionTests(unittest.TestCase):
 def test_document_wire_is_ready_and_keeps_product_identity(self):
  packet={'status':'ok','data':{'document_analysis':{'analysis_id':'a','bundle_id':'b','product_name':'Example','text':'source answer','claims':[{'quote':'source'}]}}}
  wire=ContextEngine().wire(packet,'ref');self.assertTrue(wire['reference_ready']);self.assertFalse(wire['read_before_answer_required']);self.assertEqual(wire['data']['document_analysis']['bundle_id'],'b')
 def test_wrong_candidate_reference_identifies_valid_current_alternative(self):
  f=HandoffRegressionTests();_,c,args=f.recommendation_context();args['candidates'][0]['result_refs']=['wrong']
  c['state']={'conditions_revision':1,'refs':{'right':{'revision':1,'products':[{'bundle_id':'chosen'}]},'stale':{'revision':0,'products':[{'bundle_id':'chosen'}]}}}
  c['app'].packet=lambda ref,ctx:{'status':'ok','data':{'table_values':{'bundle_id':'other' if ref=='wrong' else 'chosen'}}}
  with patch('insurance_rag.ingestion.store.bundle',return_value={'content':{'identity':{'product_version_id':'version','product_name':'Example'}}}):
   rejected=recommend(args,c);self.assertEqual(rejected['reason_code'],'recommendation_reference_product_mismatch');self.assertEqual(rejected['data']['compatible_result_refs'],['right']);self.assertEqual(rejected['data']['referenced_products'],[{'bundle_id':'other'}])
   args['candidates'][0]['result_refs']=['right'];self.assertEqual(recommend(args,c)['status'],'partial')
 def test_document_answer_retains_all_current_verified_source_cards(self):
  from insurance_rag.agent.output_validation import validate_final
  packets={str(i):{'status':'ok','data':{'document_analysis':{'analysis_id':'a'+str(i),'bundle_id':'b'+str(i),'text':'answer '+str(i),'claims':[{'quote':'source'}]}}} for i in range(5)}
  app=SimpleNamespace(journal=SimpleNamespace(live_messages={},live_calls={}),packet=lambda ref,c:packets[ref],store=SimpleNamespace(package=Mock(return_value='evidence')))
  state={'conditions_revision':1,'refs':{str(i):{'revision':1 if i<4 else 0,'tool':'product_documents'} for i in range(5)}}
  c={'app':app,'run':{'run_id':'run','input':{'message':'four products'}},'state':state,'tools':SimpleNamespace(versions={})}
  answer=validate_final(json.dumps({'action':'answer','claims':[],'result_refs':['2'],'message':'sources'}),c)
  self.assertEqual({x['bundle_id'] for x in answer['document_analyses']},{'b0','b1','b2','b3'})
 def test_low_model_call_allowance_triggers_conservation_and_wrapup(self):
  import test_native_loop as native
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown);app=f.app([{'role':'assistant','content':json.dumps({'action':'answer','message':'你好','claims':[],'result_refs':[]})}],max_llm_calls=2)
  v=app.drive(f.submit(app,'说你好')['run_id'],'alice');self.assertEqual(v['state'],'completed')
  state=json.loads(app.model.seen[0][1]['content'].split('：',1)[1]);self.assertTrue(state['execution_budget']['conserve']);self.assertTrue(state['execution_budget']['wrap_up'])

class UnsuccessfulReferenceHandoffTests(unittest.TestCase):
 def test_no_evidence_receipt_cannot_be_presented_as_success(self):
  packet={'status':'no_evidence','data':{'table_rules':{'bundle_id':'b','rules':[]}}}
  wire=ContextEngine().wire(packet,'gap');self.assertFalse(wire['final_result_eligible']);self.assertTrue(wire['reference_ready']);self.assertEqual(wire['status'],'no_evidence')
 def test_final_repair_can_choose_verified_capability_instead_of_gap(self):
  import test_native_loop as native
  from insurance_rag.tools.registry import ToolSpec
  from insurance_rag.tools.contracts import StrictModel,result
  f=native.LoopTests();f.setUp();self.addCleanup(f.tearDown)
  calls=native.tool('fixture_cap',{},'c1');calls['tool_calls']+=native.tool('fixture_gap',{},'c2')['tool_calls']
  def repair_answer(messages):
   feedback=[m['content'] for m in messages if m['role']=='system' and '可用于result_refs' in m['content']][-1]
   packets=[json.loads(m['content']) for m in messages if m['role']=='tool'];cap=next(p for p in packets if p['status']=='ok');gap=next(p for p in packets if p['status']=='no_evidence')
   self.assertIn(cap['data_ref'],feedback);allowed=feedback.split('引用仅为',1)[1].split('；',1)[0];self.assertNotIn(gap['data_ref'],allowed)
   return {'role':'assistant','content':json.dumps({'action':'answer','claims':[],'result_refs':[cap['data_ref']],'message':'已确认当前没有个人测算目标，保留缺口。'})}
  app=f.app([calls,native.answer,repair_answer])
  class Empty(StrictModel):pass
  app.registry.specs['fixture_cap']=ToolSpec('fixture_cap','fixture',Empty,lambda a,c:result('ok',data={'capability_view':{'bundle_id':'b','capabilities':[]}}))
  app.registry.specs['fixture_gap']=ToolSpec('fixture_gap','fixture',Empty,lambda a,c:result('no_evidence',data={'table_rules':{'bundle_id':'b','rules':[]}}))
  v=app.drive(f.submit(app,'说明当前能力，不估算金额')['run_id'],'alice');self.assertEqual(v['state'],'completed',v);self.assertIn('capability_view',v['result']);self.assertNotIn('table_rules',v['result'])

if __name__=='__main__':unittest.main()
