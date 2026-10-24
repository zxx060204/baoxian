"""Business regressions for monitored failures, independent of any company or document ID."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_native_loop as native
from test_native_loop import tool,answer
from insurance_rag.tools.adapters.product_table_steps import _inputs
from insurance_rag.agent.context import ContextEngine,message_groups
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.ingestion.subscriptions import wake_preparation_subscribers
from insurance_rag.reliability.diagnostics import stage,OperationFailure

class GenericAcceptanceFixTests(unittest.TestCase):
 def setUp(self):
  self.fixture=native.LoopTests();self.fixture.setUp();self.addCleanup(self.fixture.tearDown)
 def test_prior_trial_conditions_continue_but_latest_premium_wins(self):
  history='假设30岁男性，交费3年，年交保费10000元，展示5年，正常年交无变更无贷款。'
  app=SimpleNamespace(journal=SimpleNamespace(messages=lambda r:[{'role':'user','content':'其他条件不变，年交保费改为20000元'}]))
  c={'app':app,'run':{'run_id':'next','thread_id':'thread'},'owner':'alice','state':{'conversation_history':[{'run_id':'old','user':history}]}}
  inputs={'entry_age':30,'sex':'male','payment_years':3,'annual_premium_input':'20000','end_year':5,'scenario':'normal_annual_payment_no_changes_no_loans'}
  values,error=_inputs({'inputs':inputs},c);self.assertIsNone(error);self.assertEqual(values['annual_premium_input'],'20000')
  _,error=_inputs({'inputs':{**inputs,'annual_premium_input':'10000'}},c);self.assertIn('annual_premium_input',error['missing_fields'])
 def test_forgotten_trial_source_is_not_restored(self):
  app=SimpleNamespace(journal=SimpleNamespace(messages=lambda r:[{'role':'user','content':'其他条件不变，年交保费20000元'}]),profile=SimpleNamespace(snapshot=lambda *a:{'facts':[],'conflicts':[]},redactions=lambda *a:[{'source_message_id':'old:1'}]))
  c={'app':app,'run':{'run_id':'next','thread_id':'thread'},'owner':'alice','state':{'conversation_history':[{'run_id':'old','user':'假设30岁男性，交费3年，展示5年，正常年交无变更无贷款'}]}}
  _,error=_inputs({'inputs':{'entry_age':30,'sex':'male','payment_years':3,'annual_premium_input':'20000','end_year':5,'scenario':'normal_annual_payment_no_changes_no_loans'}},c)
  self.assertIn('entry_age',error['missing_fields'])
 def test_read_patch_read_uses_new_version_and_finalizes(self):
  f=self.fixture;seen=[]
  def patch_from_read(messages):
   v=json.loads(messages[-1]['content'])['data'];seen.append(v['version'])
   return tool('patch_file',{'path':'notes/fix.md','old':'第一版','new':'第二版','expected_version':v['version']},'patch')
  def verify(messages):
   v=json.loads(messages[-1]['content'])['data'];self.assertEqual(v['content'],'第二版');self.assertNotEqual(seen[0],v['version']);return answer(messages)
  app=f.app([tool('write_file',{'path':'notes/fix.md','content':'第一版'}),answer,tool('read_file',{'path':'notes/fix.md'},'read1'),patch_from_read,tool('read_file',{'path':'notes/fix.md'},'read2'),verify])
  self.assertEqual(app.drive(f.submit(app,'请把第一版保存到 notes/fix.md')['run_id'],'alice')['state'],'completed')
  end=app.drive(f.submit(app,'将 notes/fix.md 的第一版更新为第二版并读回')['run_id'],'alice');self.assertEqual(end['state'],'completed',end)
 def test_invalid_memory_feedback_supports_correct_forget(self):
  f=self.fixture
  def invalid(messages):
   trusted=json.loads(messages[1]['content'].split('：',1)[1]);m=trusted['memory'][0]
   return tool('memory',{'action':'forget','memory_id':m['memory_id']},'forget-bad')
  def correct(messages):
   err=json.loads(messages[-1]['content']);self.assertIn('expected_revision',err['missing_fields'])
   trusted=json.loads(messages[1]['content'].split('：',1)[1]);m=trusted['memory'][0]
   return tool('memory',{'action':'forget','memory_id':m['memory_id'],'expected_revision':m['revision']},'forget-good')
  app=f.app([tool('memory',{'action':'remember','content':'先给结论'}),answer,invalid,correct,answer])
  self.assertEqual(app.drive(f.submit(app,'请记住：先给结论')['run_id'],'alice')['state'],'completed')
  end=app.drive(f.submit(app,'请忘记刚才的偏好')['run_id'],'alice');self.assertEqual(end['state'],'completed',end);self.assertEqual(app.memory.active('alice'),[])
 def test_three_large_groups_compress_with_paired_calls(self):
  messages=[{'role':'user','content':'原始任务'}]
  for i in range(2):messages.extend([tool('fixture',{},str(i)),{'role':'tool','tool_call_id':str(i),'content':'x'*18000}])
  built,c=ContextEngine(36000).build('rules',messages,{'conditions_revision':1,'release_id':'r','conditions':{'age':30}},[])
  self.assertGreater(c['boundary'],0);self.assertLess(c['after_bytes'],30600);message_groups(built[3:]);self.assertIn('30',built[1]['content'])
 def test_rule_handoff_keeps_formula_without_repeated_source_column(self):
  formula={'variables':[{'name':'p','meaning':'保费','unit':'CNY'}],'expression_tree':{'type':'parameter','name':'p'}}
  wire=ContextEngine().wire({'status':'ok','data':{'table_rules':{'rules':[{'id':'t','formula':formula,'source_column':{'raw_text':'x'*25000}}]}}},'ref')
  self.assertEqual(wire['data']['table_rules']['rules'][0]['formula'],formula);self.assertNotIn('source_column',str(wire));self.assertEqual(wire['data_ref'],'ref')
 def subscription_scene(self,state):
  f=self.fixture;app=f.app([tool('resolve_products',{}),answer]);submitted=f.submit(app,'整理附件后继续');run=f.store.claim_run(submitted['run_id'],'alice');saved=app._initialize(run,'alice');app.journal.begin(run,saved)
  c={'kind':'document_processing','job_id':'job','interrupt_id':'interrupt'};saved['clarification']=c;app.journal.save(run,saved)
  intake=IntakeStore(f.store);intake.create('preparation_job','job','alice','',{'stage':'completed'},state);intake.create('bundle_subscription','sub','alice','',{'job_id':'job','run_id':run['run_id']})
  return f,app,run,c,intake
 def test_completion_wakes_original_run_and_is_delivered_once(self):
  f,app,run,c,intake=self.subscription_scene('ready');f.store.settle(run,'waiting',clarification=c);calls=[]
  def dispatch(rid,owner):calls.append(rid);app.drive(rid,owner)
  wake_preparation_subscribers(SimpleNamespace(store=f.store),'alice','job',dispatch=dispatch)
  self.assertEqual(f.store.get(run['run_id'],'alice')['state'],'completed');self.assertEqual(intake.get('bundle_subscription','sub')['state'],'delivered')
  wake_preparation_subscribers(SimpleNamespace(store=f.store),'alice','job',dispatch=dispatch);self.assertEqual(calls,[run['run_id']])
 def test_failed_preparation_also_wakes_waiter(self):
  f,app,run,c,intake=self.subscription_scene('failed');f.store.settle(run,'waiting',clarification=c)
  wake_preparation_subscribers(SimpleNamespace(store=f.store),'alice','job',dispatch=lambda rid,owner:app.drive(rid,owner))
  self.assertEqual(f.store.get(run['run_id'],'alice')['state'],'completed')
 def test_completion_before_pause_is_consumed_after_settle(self):
  f,app,run,c,intake=self.subscription_scene('partial');dispatch=Mock(side_effect=lambda rid,owner:app.drive(rid,owner))
  wake_preparation_subscribers(SimpleNamespace(store=f.store),'alice','job',dispatch=dispatch);dispatch.assert_not_called();self.assertEqual(intake.get('bundle_subscription','sub')['state'],'pending')
  f.store.settle(run,'waiting',clarification=c);wake_preparation_subscribers(SimpleNamespace(store=f.store),'alice','job',dispatch=dispatch)
  self.assertEqual(f.store.get(run['run_id'],'alice')['state'],'completed')
 def test_diagnostics_preserve_stage_without_payload(self):
  with self.assertRaises(OperationFailure) as raised:
   with stage(self.fixture.root,'source_rerank'):raise RuntimeError('bailian_http_503')
  d=raised.exception.diagnostic;self.assertEqual(d['dependency_reason'],'bailian_http_503');self.assertEqual(d['stage'],'source_rerank');self.assertEqual(d['error_code'],'service_unavailable')
  with self.assertRaises(OperationFailure):
   with stage(self.fixture.root,'source_answer_selection'):raise RuntimeError('secret-payload-123')
  self.assertNotIn('secret-payload-123',(self.fixture.root/'data/ops/diagnostics.jsonl').read_text())
 def test_incomplete_output_is_not_a_semantic_unresolved_binding(self):
  from insurance_rag.ingestion.source_execution import binding_proposals
  formulas=[{'target':{'target_id':'t'},'rule':{'formula':{'variables':[{'name':'p'}]}}}]
  with self.assertRaisesRegex(ValueError,'incomplete'):binding_proposals({'targets':[{'id':'t','bindings':[]}]},formulas)
  p=binding_proposals({'targets':[{'id':'t','bindings':[{'name':'p','kind':'unresolved','reason':'缺少定义'}]}]},formulas)
  self.assertEqual(p['t'][0]['kind'],'unresolved')
 def test_provider_failure_never_becomes_cached_binding_gap(self):
  from insurance_rag.ingestion.source_execution import prepare_execution
  app=SimpleNamespace(root=self.fixture.root,model=Mock(binding={'provider':'fixture'}))
  formula={'variables':[{'name':'p','meaning':'保险费','unit':'CNY'}],'output_unit':'CNY','expression_tree':{'type':'parameter','name':'p'}}
  rules={'rules':[{'target':{'target_id':'t','title':'某指标'},'rule':{'type':'formula','formula':formula}}]}
  app.model.structured.side_effect=[RuntimeError('deepseek_connection_failed'),{'targets':[{'id':'t','bindings':[{'name':'p','kind':'input','key':'annual_premium_input'}]}]}]
  with patch('insurance_rag.ingestion.source_execution.load_package',return_value={'package_id':'p'}),patch('insurance_rag.ingestion.source_rules.validate_rules',return_value=rules):
   with self.assertRaises(OperationFailure):prepare_execution(app,{'path':'p'},rules_ref={'path':'r'})
   self.assertEqual(list((app.root/'data/source_execution').glob('*/result.json')),[])
   result=prepare_execution(app,{'path':'p'},rules_ref={'path':'r'});self.assertEqual(result['targets'][0]['state'],'ready');self.assertEqual(app.model.structured.call_count,2)

if __name__=='__main__':unittest.main()
