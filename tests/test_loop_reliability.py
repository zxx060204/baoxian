import json,tempfile,unittest,os
from pathlib import Path
from unittest.mock import patch
from insurance_rag.agent.store import AgentStore,Conflict
from insurance_rag.agent.loop_store import LoopJournal
from insurance_rag.agent.context import ContextEngine
from insurance_rag.agent.memory import MemoryStore
from insurance_rag.workspace.files import Workspace
from insurance_rag.tools.adapters.insurance import Memory
from insurance_rag.parsing.common import stable_hash
class ReliabilityTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=AgentStore.sqlite(self.root/'state.db');self.journal=LoopJournal(self.store);self.thread=self.store.new_thread('alice');rid=self.store.new_run(self.thread,'alice','r',{'message':'请记住：报告先给结论'}, {'release_id':'r'},{});self.run=self.store.claim_run(rid,'alice');self.journal.begin(self.run,{'conditions_revision':1});self.workspace=Workspace(self.root,'alice',self.thread,self.store,self.run)
 def tearDown(self):self.store.close();self.temp.cleanup()
 def test_file_effect_before_db_commit_reconciles_without_second_write(self):
  original=os.replace
  def crash(source,target):original(source,target);raise RuntimeError('simulated_process_loss')
  with patch('insurance_rag.workspace.files.os.replace',side_effect=crash),self.assertRaises(RuntimeError):self.workspace.write('notes/a.md','saved',None,'op')
  with patch('insurance_rag.workspace.files.os.replace',side_effect=AssertionError('repeated_side_effect')):
   result=self.workspace.write('notes/a.md','saved',None,'op')
  self.assertEqual(result['version'],self.workspace.read('notes/a.md')['version'])
 def test_lost_fence_cannot_write(self):
  self.store.cancel_run(self.run['run_id'],'alice')
  with self.assertRaises(Conflict):self.workspace.write('notes/a.md','no',None,'op')
  self.assertFalse((self.workspace.root/'notes/a.md').exists())
 def test_memory_remember_same_operation_not_duplicated(self):
  m=MemoryStore(self.store);a=m.change(self.run,'alice','remember','报告先给结论',operation_id='c1');b=m.change(self.run,'alice','remember','报告先给结论',operation_id='c1');self.assertEqual(a,b);self.assertEqual(len(m.active('alice')),1)
 def test_memory_update_uncertain_replay_is_revision_safe(self):
  m=MemoryStore(self.store);mid=m.change(self.run,'alice','remember','先结论',operation_id='c1');m.change(self.run,'alice','update','先来源',memory_id=mid,expected_revision=1,operation_id='c2');m.change(self.run,'alice','update','先来源',memory_id=mid,expected_revision=1,operation_id='c2');self.assertEqual(m.active('alice')[0]['revision'],2)
 def test_memory_action_shapes_reject_empty_and_foreign_fields(self):
  for value in ({'action':'remember'},{'action':'search','query':'结论','content':'unexpected'},{'action':'forget','memory_id':'x'},{'action':'update','memory_id':'x','expected_revision':1}):
   with self.subTest(value=value),self.assertRaises(ValueError):Memory.model_validate(value)
 def test_deleted_memory_redacted_from_old_prompt_snapshot(self):
  state={'conditions_revision':1,'release_id':'r','memory_snapshot':[],'redacted_memory_contents':['喜欢旧方案']}
  messages=[{'role':'user','content':'请记住：喜欢旧方案'}];built,_=ContextEngine().build('rules',messages,state,[]);self.assertNotIn('喜欢旧方案',json.dumps(built,ensure_ascii=False))
 def test_chinese_history_search_respects_owner_and_literal_wildcards(self):
  memory=MemoryStore(self.store);self.assertEqual(len(memory.history('alice','结论')),1);self.assertEqual(memory.history('bob','结论'),[]);self.assertEqual(memory.history('alice','%_'),[])
 def test_compression_revision_cas_rejects_stale_snapshot(self):
  rev,_=self.journal.state(self.run);self.journal.save(self.run,{'conditions_revision':2})
  with self.assertRaises(Conflict):self.journal.checkpoint(self.run,rev,{'summary':'stale'})
 def test_semantic_summary_failure_uses_reference_fallback(self):
  state={'conditions_revision':1,'release_id':'r'};messages=[{'role':'user','content':'x'*3000} for _ in range(8)]
  def broken(payload):raise ValueError('summary_failure')
  built,checkpoint=ContextEngine(22000).build('rules',messages,state,[],summarize=broken);self.assertTrue(state['summary_cooldown']);self.assertEqual(checkpoint['summary']['kind'],'reference_summary');self.assertEqual(len(messages),8)
 def test_semantic_summary_cannot_add_unknown_references(self):
  state={'conditions_revision':1,'release_id':'r'};messages=[{'role':'user','content':'x'*3000} for _ in range(8)]
  _,checkpoint=ContextEngine(22000).build('rules',messages,state,[],summarize=lambda p:{'objective':'a','completed':'b','unresolved':'c','references':['forged']})
  self.assertEqual(checkpoint['summary']['kind'],'reference_summary')
 def test_large_result_has_explicit_read_reference_and_no_local_paths(self):
  output={'status':'ok','data':{'text':'x'*16000,'file_path':'C:/secret'},'evidence':[]};wire=ContextEngine().wire(output,'ref')
  self.assertTrue(wire['truncation']['omitted']);self.assertEqual(wire['data_ref'],'ref');self.assertNotIn('C:/secret',str(wire))
 def test_context_budget_refuses_unsafe_request_instead_of_dropping_state(self):
  with self.assertRaises(ValueError):ContextEngine(8000).build('x'*9000,[],{'conditions_revision':1,'conditions':{'age':36},'release_id':'r'},[])

 def test_user_supplements_survive_reference_compaction(self):
  messages=[{'role':'user','content':'年龄未提供，请先询问'}]
  messages += [{'role':'assistant','content':'请提供年龄'}, {'role':'user','content':'投保年龄0岁，其他条件不变'}]
  messages += [{'role':'assistant','content':'x'*3000} for _ in range(8)]
  state={'run_id':'r','user_supplement_message_ids':['r:3'],'conditions_revision':2,'release_id':'r','user_request':messages[0]['content'],'execution_budget':{'conserve':True}}
  built,checkpoint=ContextEngine(22000).build('rules',messages,state,[])
  protected=json.loads(built[1]['content'].split('：',1)[1])
  self.assertGreater(checkpoint['boundary'],3)
  self.assertEqual(protected['user_supplements'],['投保年龄0岁，其他条件不变'])
  self.assertNotIn('投保年龄0岁',json.dumps(checkpoint['summary'],ensure_ascii=False))
 def test_protected_supplements_honor_memory_redaction(self):
  messages=[{'role':'user','content':'初始问题'},{'role':'user','content':'请记住：喜欢旧方案'}]
  state={'run_id':'r','user_supplement_message_ids':['r:2'],'conditions_revision':2,'release_id':'r','redacted_memory_contents':['喜欢旧方案']}
  built,_=ContextEngine().build('rules',messages,state,[])
  self.assertNotIn('喜欢旧方案',json.dumps(built,ensure_ascii=False))
  self.assertIn('旧内容不再用于推断',built[1]['content'])
