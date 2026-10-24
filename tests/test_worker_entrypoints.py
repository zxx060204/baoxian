"""Actual process entrypoints must load and reject malformed input before touching services."""
import json,subprocess,sys,unittest
from pathlib import Path
from insurance_rag.agent.worker_protocol import read_report
ROOT=Path(__file__).resolve().parents[1]
class WorkerEntrypointTests(unittest.TestCase):
 def test_agent_worker_emits_only_allowlisted_failure_for_invalid_spec(self):
  p=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/agent_worker.py')],cwd=ROOT,input='{}',capture_output=True,text=True,timeout=15)
  report=read_report(p.stdout.encode(), '')
  self.assertEqual(p.returncode,1);self.assertIsNotNone(report);self.assertEqual(report['phase'],'initialize');self.assertEqual(report['state'],'failed')
 def test_preparation_worker_rejects_invalid_spec_before_database(self):
  p=subprocess.run([sys.executable,'-X','utf8',str(ROOT/'scripts/product_ingestion_worker.py')],cwd=ROOT,input='{}',capture_output=True,text=True,timeout=15)
  self.assertNotEqual(p.returncode,0);self.assertIn('invalid_preparation_spec',p.stderr);self.assertEqual(p.stdout,'')
