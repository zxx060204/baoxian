import tempfile,unittest
from pathlib import Path
from insurance_rag.indexing.bailian_tokenization import BailianUsageCounter
from insurance_rag.indexing.bailian import BailianEmbedding
from test_bailian import Opener

def provider(tokens=5):
 body={'model':'qwen3.7-text-embedding-flash','data':[{'index':0,'embedding':[1,0]}],'usage':{'prompt_tokens':tokens}}
 return BailianEmbedding('https://dashscope.aliyuncs.com/compatible-mode/v1','fixture-key','qwen3.7-text-embedding-flash',2,opener=Opener([body]),sleep=lambda _:None)
class UsageCounterTests(unittest.TestCase):
 def test_single_input_and_cache_survive_restart(self):
  with tempfile.TemporaryDirectory() as t:
   p=provider();path=Path(t)/'counts.sqlite3';c=BailianUsageCounter(p,path,epoch='test');self.assertEqual(c('测试'),5);self.assertEqual(c('测试'),5);self.assertEqual(len(p.opener.requests),1);c.close()
   c=BailianUsageCounter(provider(),path,epoch='test');self.assertEqual(c('测试'),5);self.assertEqual(c.stats['measurements'],0);c.close()
 def test_bad_or_missing_usage_not_cached(self):
  for value in (None,True,-1,0):
   with tempfile.TemporaryDirectory() as t:
    c=BailianUsageCounter(provider(value),Path(t)/'cache',epoch='test')
    try:
     with self.assertRaisesRegex(ValueError,'prompt_tokens'):c('正文')
     self.assertEqual(c.db.execute('SELECT COUNT(*) FROM token_count_cache').fetchone()[0],0)
    finally:c.close()
 def test_budget_exhaustion_does_not_send_extra_payload(self):
  with tempfile.TemporaryDirectory() as t:
   p=provider();c=BailianUsageCounter(p,Path(t)/'cache',epoch='test',max_measurements=1)
   try:
    c('正文')
    with self.assertRaisesRegex(RuntimeError,'budget_exhausted'):c('新正文')
    self.assertEqual(len(p.opener.requests),1)
   finally:c.close()
 def test_epoch_prevents_stale_alias_counts(self):
  with tempfile.TemporaryDirectory() as t:
   path=Path(t)/'cache';c=BailianUsageCounter(provider(5),path,epoch='a');c('正文');c.close()
   c=BailianUsageCounter(provider(6),path,epoch='b');self.assertEqual(c('正文'),6);c.close()
 def test_blank_and_oversized_inputs_do_not_call_api(self):
  with tempfile.TemporaryDirectory() as t:
   p=provider();c=BailianUsageCounter(p,Path(t)/'cache',epoch='test')
   try:
    self.assertEqual(c(''),0)
    with self.assertRaisesRegex(ValueError,'input_limit'):c('x'*128001)
    self.assertEqual(p.stats['requests'],0)
   finally:c.close()
