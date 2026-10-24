import json,tempfile,unittest
from pathlib import Path
from insurance_rag.reliability.runtime import Deadline,DeadlineExceeded,DependencyPolicy,DependencyUnavailable,error_code,retry_delay,request_budget
from insurance_rag.parsing.common import stable_hash
class Clock:
 def __init__(self):self.value=100
 def __call__(self):return self.value


class SharedPolicyTests(unittest.TestCase):
 def test_two_instances_share_capacity(self):
  from insurance_rag.reliability.shared_policy import SharedDependencyPolicy
  with tempfile.TemporaryDirectory() as d:
   a=SharedDependencyPolicy(Path(d)/'p.db','model',concurrency=1);b=SharedDependencyPolicy(Path(d)/'p.db','model',concurrency=1)
   with a.call():
    with self.assertRaises(DependencyUnavailable):
     with b.call():pass
   with b.call():pass
 def test_shared_circuit_and_expired_orphan_lease(self):
  from insurance_rag.reliability.shared_policy import SharedDependencyPolicy
  with tempfile.TemporaryDirectory() as d:
   c=Clock();a=SharedDependencyPolicy(Path(d)/'p.db','model',failures=1,cooldown=5,lease_seconds=2,clock=c);b=SharedDependencyPolicy(Path(d)/'p.db','model',failures=1,cooldown=5,lease_seconds=2,clock=c)
   with self.assertRaises(RuntimeError):
    with a.call():raise RuntimeError('bailian_http_429')
   with self.assertRaises(DependencyUnavailable):
    with b.call():pass
   c.value+=6;owner=b.claim();c.value+=3
   with a.call():pass
   b.complete(owner,RuntimeError('late'));db=a.db()
   try:self.assertEqual(db.execute('SELECT failed FROM dependency_state').fetchone()[0],0)
   finally:db.close()
if __name__=='__main__':unittest.main()



