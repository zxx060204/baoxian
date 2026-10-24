"""Exact per-text token counts from single-input Bailian usage, with durable cache."""
import sqlite3,time
from insurance_rag.parsing.common import stable_hash

class BailianUsageCounter:
 measured_model_tokens=True
 def __init__(self,provider,cache_path,*,epoch,max_measurements=2500):
  if provider.model not in ('qwen3.7-text-embedding','qwen3.7-text-embedding-flash'):raise ValueError('usage_counter_model_not_verified')
  if not isinstance(epoch,str) or not epoch.strip():raise ValueError('counter_epoch_required')
  if type(max_measurements)is not int or max_measurements<1:raise ValueError('invalid_counter_measurement_limit')
  self.provider=provider;self.max_measurements=max_measurements;self.name='bailian-single-input-usage-v1:'+stable_hash([provider.binding,epoch])
  self.binding={'method':'single_input_prompt_tokens','provider':provider.binding,'epoch':epoch,'max_input_bytes':128000}
  self.db=sqlite3.connect(cache_path);self.db.execute('CREATE TABLE IF NOT EXISTS token_count_cache(binding TEXT NOT NULL,text_hash TEXT NOT NULL,token_count INTEGER NOT NULL CHECK(token_count>=0),PRIMARY KEY(binding,text_hash))');self.db.commit()
  self.stats={'cache_hits':0,'measurements':0,'prompt_tokens':0,'elapsed_seconds':0.0}
 def __call__(self,text):
  if not isinstance(text,str):raise ValueError('counter_text_required')
  if not text.strip():return 0
  digest=stable_hash(text);row=self.db.execute('SELECT token_count FROM token_count_cache WHERE binding=? AND text_hash=?',(self.name,digest)).fetchone()
  if row:self.stats['cache_hits']+=1;return row[0]
  if len(text.encode('utf8'))>128000:raise ValueError('usage_counter_conservative_input_limit')
  if self.stats['measurements']>=self.max_measurements:raise RuntimeError('usage_counter_measurement_budget_exhausted_resume_with_cache')
  started=time.monotonic()
  try:
   response=self.provider._request([text]);self.provider._vectors(response,1)
   tokens=response.get('usage',{}).get('prompt_tokens')
   if type(tokens)is not int or tokens<1:raise ValueError('single_input_prompt_tokens_required')
   self.db.execute('INSERT INTO token_count_cache VALUES (?,?,?)',(self.name,digest,tokens));self.db.commit()
   self.stats['measurements']+=1;self.stats['prompt_tokens']+=tokens
   if self.stats['measurements']%100==0:print('model_count_measurements',self.stats['measurements'],'cache_hits',self.stats['cache_hits'],flush=True)
   return tokens
  finally:self.stats['elapsed_seconds']+=time.monotonic()-started
 def close(self):self.db.close()
