import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from types import SimpleNamespace
from contextlib import contextmanager
from fastapi.testclient import TestClient
from insurance_rag.agent.runtime_binding import RuntimeSnapshot,source_manifest
from insurance_rag.agent.runtime_errors import RuntimeSourceChanged,RuntimeBindingMismatch
from insurance_rag.agent.worker_protocol import failure,read_report
from insurance_rag.agent.store import AgentStore
from insurance_rag.agent.supervisor import supervise
from insurance_rag.api.app import create_app


class RuntimeIsolationTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  (self.root/'src/insurance_rag/agent').mkdir(parents=True)
  self.engine=self.root/'src/insurance_rag/agent/engine.py';self.engine.write_text('VERSION=1')
  self.old=self.root/'src/insurance_rag/agent/graph.py';self.old.write_text('OLD=1')
 def tearDown(self):self.temp.cleanup()
 def test_legacy_change_does_not_invalidate_native_binding(self):
  frozen=RuntimeSnapshot(self.root);before=frozen.binding('rrf');self.old.write_text('OLD=2');self.assertEqual(before,frozen.binding('rrf'))
 def test_native_change_refuses_mixed_version_binding(self):
  frozen=RuntimeSnapshot(self.root);self.engine.write_text('VERSION=2')
  with self.assertRaises(RuntimeSourceChanged):frozen.binding('rrf')
 def test_native_deletion_and_new_dependency_are_detected(self):
  frozen=RuntimeSnapshot(self.root);self.engine.unlink()
  with self.assertRaises(RuntimeSourceChanged):frozen.assert_current()
  self.engine.write_text('VERSION=1');(self.root/'src/insurance_rag/services').mkdir();frozen=RuntimeSnapshot(self.root)
  (self.root/'src/insurance_rag/services/new.py').write_text('X=1')
  with self.assertRaises(RuntimeSourceChanged):frozen.assert_current()
 def test_native_manifest_excludes_graph_and_includes_retrieval_and_store(self):
  root=Path(__file__).resolve().parents[1];files=source_manifest(root)
  for name in ('graph.py','application.py','bootstrap.py','persistence.py','legacy_prompts.py'):
   self.assertNotIn('src/insurance_rag/agent/'+name,files)
  self.assertIn('src/insurance_rag/workflow/store.py',files);self.assertTrue(any('/retrieval/' in f for f in files))
 def test_worker_error_does_not_expose_exception_details(self):
  receipt=failure(RuntimeBindingMismatch('private DSN and key'),'run','initialize')
  self.assertEqual(receipt['error_code'],'runtime_binding_mismatch');self.assertNotIn('private',str(receipt))
 def test_worker_protocol_rejects_foreign_run_unknown_errors_and_raw_output(self):
  good={'run_id':'r','state':'failed','phase':'initialize','error_code':'runtime_binding_mismatch'}
  self.assertIsNotNone(read_report(json.dumps(good).encode(),'r'))
  for value in ({**good,'run_id':'foreign'},{**good,'error_code':'secret value'},{**good,'debug':'private'}):
   self.assertIsNone(read_report(json.dumps(value).encode(),'r'))
  self.assertIsNone(read_report(b'raw traceback','r'));self.assertIsNone(read_report(b'x'*4097,'r'))
 def test_api_source_change_returns_explicit_503_before_task_creation(self):
  @contextmanager
  def factory():raise RuntimeSourceChanged('private');yield
  with TestClient(create_app(factory,authenticate=lambda _:'alice')) as client:
   response=client.post('/v1/threads/t/runs',json={'message':'hello','request_id':'r'})
   self.assertEqual(response.status_code,503);self.assertEqual(response.json()['detail'],'runtime_source_changed_restart_required')
 def test_recovery_version_mismatch_preserves_failed_state(self):
  retry=Mock();store=SimpleNamespace(get=lambda rid,owner:{'state':'failed'},retry_failed=retry)
  def reject(run):raise RuntimeBindingMismatch('private')
  @contextmanager
  def factory():yield SimpleNamespace(store=store,validate_runtime=reject)
  with TestClient(create_app(factory,authenticate=lambda _:'alice')) as client:
   response=client.post('/v1/runs/r/recover')
  self.assertEqual(response.status_code,409);self.assertEqual(response.json()['detail'],'runtime_binding_mismatch');retry.assert_not_called()


class SupervisorFailureTests(unittest.TestCase):
 def setUp(self):
  env_loader=patch('insurance_rag.agent.supervisor.load_env');env_loader.start();self.addCleanup(env_loader.stop)
  self.temp=tempfile.TemporaryDirectory();self.store=AgentStore.sqlite(Path(self.temp.name)/'db');self.owner='alice'
  self.rid=self.store.new_run(self.store.new_thread(self.owner),self.owner,'r',{'message':'test'},{'release_id':'r'},{'runtime_version':'native-loop-1'})
 def tearDown(self):self.store.close();self.temp.cleanup()
 def fake_child(self,args,**kwargs):
  receipt={'run_id':self.rid,'state':'failed','error_code':'runtime_binding_mismatch','phase':'initialize'}
  kwargs['stdout'].write(json.dumps(receipt).encode());kwargs['stdout'].flush()
  class Child:
   stdin=io.BytesIO();returncode=1
   def poll(self):return self.returncode
  return Child()
 def test_supervisor_keeps_specific_startup_failure_and_safe_event(self):
  with patch('insurance_rag.agent.supervisor.AgentStore.connect',return_value=self.store),patch.object(self.store,'close'),patch('insurance_rag.agent.supervisor.subprocess.Popen',side_effect=self.fake_child),patch.dict('os.environ',{'INSURANCE_POSTGRES_DSN':'unused-test'}):
   result=supervise(self.rid,self.owner)
  self.assertEqual(result['error_code'],'runtime_binding_mismatch');self.assertEqual(result['state'],'failed')
  events=self.store.events(self.rid,self.owner);exited=next(e for e in events if e['event']=='worker_exited')
  self.assertEqual(exited['data']['phase'],'initialize');self.assertNotIn('stdout',exited['data'])
 def test_spawn_failure_does_not_leave_queued_run(self):
  with patch('insurance_rag.agent.supervisor.AgentStore.connect',return_value=self.store),patch.object(self.store,'close'),patch('insurance_rag.agent.supervisor.subprocess.Popen',side_effect=OSError('private')),patch.dict('os.environ',{'INSURANCE_POSTGRES_DSN':'unused-test'}):
   result=supervise(self.rid,self.owner)
  self.assertEqual(result['error_code'],'worker_startup_failed');self.assertEqual(result['state'],'failed')

 def interrupted_child(self,args,**kwargs):
  store=self.store;rid=self.rid;owner=self.owner
  class Input(io.BytesIO):
   def close(self):pass
  class Child:
   stdin=Input();returncode=1;claimed=False
   def poll(child):
    if not child.claimed:
     spec=json.loads(child.stdin.getvalue());run=store.claim_run(rid,owner,execution_token=spec['execution_token']);store.add_usage(run,{'active_milliseconds':1000});store.event(rid,'model_started',{'protocol':'native_tools'});child.claimed=True
    return child.returncode
   def kill(child):child.returncode=1
   def wait(child,timeout=None):return child.returncode
  return Child()
 def test_empty_worker_exit_recoverable_and_unreported_budget_counted_once(self):
  with patch('insurance_rag.agent.supervisor.AgentStore.connect',return_value=self.store),patch.object(self.store,'close'),patch('insurance_rag.agent.supervisor.subprocess.Popen',side_effect=self.interrupted_child),patch('insurance_rag.agent.supervisor.time.monotonic',side_effect=[100,103]),patch.dict('os.environ',{'INSURANCE_POSTGRES_DSN':'unused-test'}):
   result=supervise(self.rid,self.owner)
  self.assertEqual(result['state'],'failed');self.assertEqual(result['error_code'],'worker_exited_without_completion')
  failed=self.store.get(self.rid,self.owner);self.assertEqual(failed['budget']['active_milliseconds'],3000);self.assertEqual(failed['budget']['unknown_inflight_operations'],1);self.assertEqual(failed['budget']['conservative_unknown_tokens'],96000+8192)
  self.store.retry_failed(self.rid,self.owner);retried=self.store.get(self.rid,self.owner);self.assertEqual(retried['state'],'queued');self.assertEqual(retried['budget'],failed['budget'])
 def test_nonempty_invalid_protocol_stays_nonretryable(self):
  def invalid_child(args,**kwargs):
   child=self.fake_child(args,**kwargs);kwargs['stdout'].seek(0);kwargs['stdout'].truncate();kwargs['stdout'].write(b'not-json');kwargs['stdout'].flush();return child
  with patch('insurance_rag.agent.supervisor.AgentStore.connect',return_value=self.store),patch.object(self.store,'close'),patch('insurance_rag.agent.supervisor.subprocess.Popen',side_effect=invalid_child),patch.dict('os.environ',{'INSURANCE_POSTGRES_DSN':'unused-test'}):result=supervise(self.rid,self.owner)
  self.assertEqual(result['error_code'],'worker_protocol_invalid')
  from insurance_rag.agent.store import Conflict
  with self.assertRaises(Conflict):self.store.retry_failed(self.rid,self.owner)
 def test_hard_deadline_accounts_unreported_time_before_settling(self):
  def stuck_child(args,**kwargs):
   child=self.interrupted_child(args,**kwargs);child.returncode=None;return child
  with patch('insurance_rag.agent.supervisor.AgentStore.connect',return_value=self.store),patch.object(self.store,'close'),patch('insurance_rag.agent.supervisor.subprocess.Popen',side_effect=stuck_child),patch('insurance_rag.agent.supervisor.time.monotonic',side_effect=[100,102,103]),patch('insurance_rag.agent.supervisor.LoopSettings.from_environment',return_value=SimpleNamespace(timeout_seconds=1,context_bytes=96000)),patch.dict('os.environ',{'INSURANCE_POSTGRES_DSN':'unused-test'}):result=supervise(self.rid,self.owner)
  self.assertEqual(result['error_code'],'deadline_exceeded');run=self.store.get(self.rid,self.owner);self.assertEqual(run['budget']['active_milliseconds'],3000);self.assertEqual(run['budget']['unknown_inflight_operations'],1)
