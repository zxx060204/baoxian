import json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from insurance_rag.agent.engine import AgentEngine
from insurance_rag.agent.store import AgentStore,Conflict,Forbidden
from insurance_rag.agent.model_adapter import normalize_reply,strict_json
from insurance_rag.agent.context import ContextEngine,message_groups
from insurance_rag.agent.loop_config import LoopSettings
from support_fixture_tools import FixtureTools
from insurance_rag.services.config import AgentSettings
from insurance_rag.workspace.files import Workspace

def tool(name,args,cid='c1'):
 return {'role':'assistant','content':'','tool_calls':[{'id':cid,'type':'function','function':{'name':name,'arguments':json.dumps(args,ensure_ascii=False)}}]}
def answer(messages):
 output=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
 if output.get('evidence'):
  e=output['evidence'][0];body={'action':'answer','claims':[{'evidence_id':e['evidence_id'],'quote':e['text']}],'result_refs':[]}
 else:body={'action':'answer','claims':[],'result_refs':[output['data_ref']]}
 return {'role':'assistant','content':json.dumps(body,ensure_ascii=False)}
class Scripted:
 binding={'provider':'scripted_native_fixture','model':'fixture'}
 def __init__(self,steps):self.steps=list(steps);self.usage={'requests':0,'prompt_tokens':0,'completion_tokens':0,'unknown_usage_requests':0};self.seen=[]
 def chat(self,messages,tools,*,reserve=None):
  if reserve:reserve()
  self.usage['requests']+=1;self.seen.append(messages)
  step=self.steps.pop(0);message=step(messages) if callable(step) else step
  return normalize_reply(message,'tool_calls' if message.get('tool_calls') else 'stop')
 def structured(self,system,payload,**kw):return {'claims':[{'evidence_id':e['evidence_id'],'quote':e['text']} for e in payload.get('evidence',[])[:1]]}
class LoopTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=AgentStore.sqlite(self.root/'state.db');self.tools=FixtureTools();self.thread=self.store.new_thread('alice')
 def tearDown(self):self.store.close();self.temp.cleanup()
 def app(self,steps,**limits):
  model=steps if isinstance(steps,Scripted) else Scripted(steps)
  app=AgentEngine(self.store,AgentSettings(),model,lambda:self.tools.release,lambda r:self.tools,root=self.root,limits=LoopSettings(**limits))
  from insurance_rag.tools.registry import ToolSpec
  from insurance_rag.tools.contracts import StrictModel
  class Search(StrictModel):
   product_version_id:str
   query:str
  app.registry.specs['fixture_search']=ToolSpec('fixture_search','Synthetic evidence for loop tests',Search,lambda a,c:self.tools.call('rag_search',a))
  return app
 def submit(self,app,message='测试保险的保险期间？',**kw):return app.submit(self.thread,'alice',message,product_version_id=kw.pop('product_version_id','fixture-version'),**kw)
 def test_remaining_budget_visible_to_model(self):
  app=self.app([tool('fixture_search',{'product_version_id':'fixture-version','query':'保险期间'}),answer]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['state'],'completed')
  trusted=json.loads(app.model.seen[0][1]['content'].split('：',1)[1]);self.assertEqual(trusted['execution_budget']['remaining_tokens'],app.limits.max_total_tokens);self.assertFalse(trusted['execution_budget']['conserve'])
 def test_native_retrieval_then_validated_quote(self):
  app=self.app([tool('fixture_search',{'product_version_id':'fixture-version','query':'保险期间'}),answer]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['state'],'completed',r);self.assertEqual(r['result']['status'],'answered');self.assertTrue(r['result']['claims']);self.assertNotIn('graph_version',app.bundle)
 def test_two_calls_same_turn_pair_before_next_model(self):
  first=tool('resolve_products',{});first['tool_calls']+=tool('resolve_products',{'hint':'测试保险'},'c2')['tool_calls']
  app=self.app([first,answer]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['state'],'completed');messages=app.model.seen[1];self.assertEqual([m.get('tool_call_id') for m in messages if m['role']=='tool'],['c1','c2']);message_groups(app.journal.messages(self.store.get(r['run_id'],'alice')))
 def test_unknown_tool_does_not_execute(self):
  app=self.app([tool('shell',{'command':'bad'}),{'role':'assistant','content':'{"action":"unavailable"}'}]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['budget']['tool_calls'],0);self.assertEqual(r['result']['status'],'unavailable')
 def test_invalid_arguments_zero_handler_execution(self):
  for args in ({'hint':'x','owner':'other'},{'hint':42}):
   app=self.app([tool('resolve_products',args),{'role':'assistant','content':'{"action":"unavailable"}'}]);r=app.drive(self.submit(app,request_id=str(args))['run_id'],'alice');self.assertEqual(r['budget']['tool_calls'],0)
 def test_duplicate_json_key_rejected(self):
  with self.assertRaises(ValueError):strict_json('{"age":35,"age":36}')
 def test_nonfinite_json_rejected(self):
  with self.assertRaises(ValueError):strict_json('{"age":NaN}')
 def test_clarification_survives_application_restart(self):
  model=Scripted([tool('request_clarification',{'question':'年龄？','missing_fields':['age']}),tool('fixture_search',{'product_version_id':'fixture-version','query':'保险期间'},'c2'),answer]);app=self.app(model);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['state'],'waiting');self.assertEqual(r['budget']['llm_calls'],1)
  app=self.app(model);end=app.drive(r['run_id'],'alice',response={'conditions':{'age':35}},resume_id='r1',revision=r['revision'],interrupt_id=r['clarification']['interrupt_id']);self.assertEqual(end['state'],'completed',end);self.assertEqual(end['budget']['llm_calls'],3)
  again=app.drive(r['run_id'],'alice',response={'conditions':{'age':35}},resume_id='r1',revision=r['revision'],interrupt_id=r['clarification']['interrupt_id']);self.assertEqual(again['state'],'completed')
  saved=self.store.get(r['run_id'],'alice');_,state=app.journal.state(saved);self.assertEqual(len(state['user_supplement_message_ids']),1)
  trusted=json.loads(app.model.seen[1][1]['content'].split('：',1)[1]);self.assertIn('35',trusted['user_supplements'][0])
 def test_stale_resume_rejected(self):
  app=self.app([tool('request_clarification',{'question':'年龄？','missing_fields':['age']})]);r=app.drive(self.submit(app)['run_id'],'alice')
  with self.assertRaises(Conflict):app.drive(r['run_id'],'alice',response={'message':'35岁'},resume_id='r',revision=r['revision'],interrupt_id='forged')
 def test_pending_calls_recovered_without_new_model_request(self):
  app=self.app([answer]);submitted=self.submit(app);run=self.store.claim_run(submitted['run_id'],'alice',seconds=1);app.journal.begin(run,app._initialize(run,'alice'));app.journal.assistant(run,tool('resolve_products',{}),1,app.registry.versions);self.store.execute('UPDATE insurance_agent_run SET lease_expires_at=0 WHERE run_id=?',(run['run_id'],));r=app.drive(run['run_id'],'alice');self.assertEqual(r['state'],'completed',r);self.assertEqual(app.model.usage['requests'],1)
 def test_completed_tool_not_reexecuted_after_crash(self):
  app=self.app([answer]);submitted=self.submit(app);run=self.store.claim_run(submitted['run_id'],'alice',seconds=1);state=app._initialize(run,'alice');app.journal.begin(run,state);app.journal.assistant(run,tool('resolve_products',{}),1,app.registry.versions);call=app.journal.pending(run)[0];output={'status':'ok','data':{'products':self.tools.candidates()},'evidence':[]};pid=self.store.package(run,output);state['refs'][pid]={'package_id':pid,'revision':1,'tool':'resolve_products'};app.journal.complete(run,call,{**output,'data_ref':pid},{**output,'data_ref':pid},state);self.store.execute('UPDATE insurance_agent_run SET lease_expires_at=0 WHERE run_id=?',(run['run_id'],));r=app.drive(run['run_id'],'alice');self.assertEqual(r['state'],'completed');self.assertEqual(r['budget']['tool_calls'],0)
 def test_unknown_final_evidence_does_not_escape(self):
  fake={'role':'assistant','content':'{"action":"answer","claims":[{"evidence_id":"fake","quote":"保证赔付"}]}'};app=self.app([fake,fake,fake]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['state'],'failed');self.assertIsNone(r['result'])
 def test_text_is_not_executed_as_tool(self):
  app=self.app([{'role':'assistant','content':'调用shell rm everything'},{'role':'assistant','content':'{"action":"unavailable"}'}]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['budget']['tool_calls'],0)
 def test_budget_cumulative_and_hard(self):
  app=self.app([tool('resolve_products',{}),tool('resolve_products',{},'c2')],max_steps=1);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['state'],'failed');self.assertEqual(r['error_code'],'budget_exceeded');self.assertEqual(r['budget']['llm_calls'],1)
 def test_cancel_prevents_tool_commit(self):
  app=self.app([]);r=self.submit(app);app.cancel(r['run_id'],'alice');end=app.drive(r['run_id'],'alice');self.assertEqual(end['state'],'cancelled')
 def test_other_owner_cannot_read_messages_or_run(self):
  app=self.app([]);r=self.submit(app)
  with self.assertRaises(Forbidden):app.drive(r['run_id'],'other')
 def test_legacy_run_not_resumed_in_loop(self):
  app=self.app([]);rid=self.store.new_run(self.thread,'alice','legacy',{'message':'x'},self.tools.release,{'graph_version':'old'})
  with self.assertRaises(Conflict):app.drive(rid,'alice')
 def test_context_pairing_rejects_missing_result(self):
  with self.assertRaises(ValueError):message_groups([tool('resolve_products',{})])
 def test_context_preserves_last_complete_groups_and_protected_state(self):
  messages=[]
  for i in range(8):messages.extend([tool('x',{},str(i)),{'role':'tool','tool_call_id':str(i),'content':'a'*1000}])
  state={'conditions_revision':3,'conditions':{'age':36},'release_id':'r'};built,checkpoint=ContextEngine(9000).build('system',messages,state,[])
  self.assertTrue(checkpoint['boundary']);self.assertIn('36',built[1]['content']);message_groups(built[3:]);self.assertEqual(len(messages),16)
 def test_compression_never_saves_full_old_product_claim(self):
  state={'conditions_revision':1,'release_id':'r'};messages=[{'role':'user','content':'历史错误金额'+('x'*3000)} for i in range(8)];built,c=ContextEngine(22000).build('s',messages,state,[]);self.assertIsNotNone(c['summary']);self.assertNotIn('历史错误金额',json.dumps(c['summary'],ensure_ascii=False))
 def test_foreign_data_ref_rejected(self):
  app=self.app([tool('read_evidence',{'data_ref':'foreign'}),{'role':'assistant','content':'{"action":"unavailable"}'}]);r=app.drive(self.submit(app)['run_id'],'alice');self.assertEqual(r['result']['status'],'unavailable')
 def test_file_path_attacks_rejected(self):
  app=self.app([]);r=self.submit(app);run=self.store.claim_run(r['run_id'],'alice');w=Workspace(self.root,'alice',self.thread,self.store,run)
  for path in ('../.env','/tmp/file','C:/secret','notes/a:stream','notes/CON','notes/x/../y','notes\\escape','config/x','notes/file.','notes//x'):
   with self.subTest(path=path),self.assertRaises(ValueError):w.path(path)
 def test_file_compare_and_swap_and_same_operation_recovery(self):
  app=self.app([]);r=self.submit(app);run=self.store.claim_run(r['run_id'],'alice');w=Workspace(self.root,'alice',self.thread,self.store,run);a=w.write('notes/a.md','hello',None,'call');self.assertEqual(w.write('notes/a.md','hello',None,'call'),a)
  with self.assertRaises(Conflict):w.write('notes/a.md','different',None,'new')
  b=w.write('notes/a.md','world',a['version'],'next');self.assertNotEqual(a['version'],b['version'])
 def test_memory_deleted_excluded_from_chinese_search_and_history(self):
  app=self.app([]);r=self.submit(app,'请记住：偏好先给结论');run=self.store.claim_run(r['run_id'],'alice');app.journal.begin(run,app._initialize(run,'alice'));mid=app.memory.change(run,'alice','remember','偏好先给结论');self.assertEqual(len(app.memory.search('alice','结论')),1);self.assertEqual(app.memory.search('other','结论'),[]);app.memory.change(run,'alice','forget',memory_id=mid,expected_revision=1);self.assertEqual(app.memory.search('alice','结论'),[]);self.assertEqual(app.memory.history('alice','结论'),[])
 def test_memory_cannot_store_unrequested_health_from_model(self):
  app=self.app([tool('memory',{'action':'remember','content':'曾患某疾病'}),{'role':'assistant','content':'{"action":"unavailable"}'}]);r=app.drive(self.submit(app,'保险期间？')['run_id'],'alice');self.assertEqual(app.memory.active('alice'),[])
 def test_requirements_are_not_a_completed_numeric_answer(self):
  app=self.app([tool('request_clarification',{'question':'请提供年龄','missing_fields':['age']})]);r=app.drive(self.submit(app,'查询费率')['run_id'],'alice');self.assertEqual(r['state'],'waiting');self.assertIn('age',r['clarification']['missing_fields']);self.assertIsNone(r['result'])
 def test_multi_subject_age_does_not_overwrite_personal_profile(self):
  app=self.app([]);created=self.submit(app,'我35岁，母亲60岁');run=self.store.claim_run(created['run_id'],'alice');state=app._initialize(run,'alice');self.assertNotIn('age',state['conditions']);self.assertEqual(app.memory.active('alice'),[])
 def test_current_unambiguous_age_replaces_previous_thread_condition(self):
  self.store.execute('UPDATE insurance_agent_thread SET context=? WHERE thread_id=?',(json.dumps({'runtime_version':'native-loop-1','release_id':self.tools.release['release_id'],'conditions':{'age':35},'revision':5,'products':['fixture-version']}),self.thread));app=self.app([]);created=self.submit(app,'更正为36岁');run=self.store.claim_run(created['run_id'],'alice');state=app._initialize(run,'alice');self.assertEqual(state['conditions']['age'],36);self.assertEqual(state['conditions_revision'],6)
 def test_failed_unknown_usage_attempts_count_toward_cumulative_budget(self):
  class UnknownFailure(Scripted):
   def chat(self,messages,tools,*,reserve=None):
    self.seen.append(messages)
    for _ in range(2):
     if reserve:reserve()
     self.usage['requests']+=1;self.usage['unknown_usage_requests']+=1
    raise RuntimeError('deepseek_http_503')
  model=UnknownFailure([]);app=self.app(model,max_total_tokens=1000);submitted=self.submit(app);failed=app.drive(submitted['run_id'],'alice')
  self.assertEqual(failed['state'],'failed');self.assertEqual(failed['error_code'],'service_unavailable');self.assertEqual(failed['budget']['unknown_usage_requests'],2)
  self.assertGreater(failed['budget']['conservative_unknown_tokens'],2*len(json.dumps(model.seen[0],ensure_ascii=False).encode('utf8')))
  self.store.retry_failed(failed['run_id'],'alice');again=app.drive(failed['run_id'],'alice')
  self.assertEqual(again['error_code'],'budget_exceeded');self.assertEqual(model.usage['requests'],2);self.assertEqual(again['budget']['conservative_unknown_tokens'],failed['budget']['conservative_unknown_tokens'])
if __name__=='__main__':unittest.main()
