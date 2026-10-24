"""DeepSeek JSON client. No model reasoning, key, or raw errors enter reports."""
import json,time
from http.client import HTTPException
from urllib.request import Request,build_opener
from urllib.error import HTTPError,URLError
from insurance_rag.indexing.bailian import NoRedirect
from insurance_rag.reliability.runtime import bounded_timeout,check_budget,retry_delay,retry_sleep,DependencyUnavailable
from insurance_rag.reliability.shared_policy import SharedDependencyPolicy
from insurance_rag.parsing.common import stable_hash
from pathlib import Path

class DeepSeekChat:
 def __init__(self,settings,*,opener=None,sleep=time.sleep):
  self.settings=settings;self.binding=settings.binding;self.opener=opener or build_opener(NoRedirect());self.sleep=sleep
  self.policy=SharedDependencyPolicy(Path(__file__).resolve().parents[3]/'data/ops/dependencies.sqlite3',stable_hash(['deepseek',settings.base_url,settings.model]))
  self.usage={'requests':0,'prompt_tokens':0,'completion_tokens':0,'unknown_usage_requests':0}
 def structured(self,system,payload,*,reserve=None):
  thinking=self.settings.thinking
  output_tokens=self.settings.max_output_tokens
  body={'model':self.settings.model,'messages':[{'role':'system','content':system+'\nReturn valid JSON only.'},{'role':'user','content':json.dumps(payload,ensure_ascii=False,allow_nan=False)}],
   'response_format':{'type':'json_object'},'thinking':{'type':'enabled' if thinking else 'disabled'},'max_tokens':output_tokens,'temperature':0,'stream':False}
  if len(json.dumps(body,ensure_ascii=False).encode('utf8'))>100000:raise ValueError('chat_input_too_large')
  with self.policy.call():
   for attempt in range(self.settings.retries+1):
    check_budget()
    if reserve:reserve()
    self.usage['requests']+=1
    request=Request(self.settings.base_url+'/chat/completions',data=json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf8'),headers={'Content-Type':'application/json','Authorization':'Bearer '+self.settings.api_key},method='POST')
    try:
     with self.opener.open(request,timeout=bounded_timeout(self.settings.timeout_seconds)) as response:raw=response.read(2*1024*1024+1)
     if len(raw)>2*1024*1024:raise ValueError('chat_response_too_large')
     output=json.loads(raw);usage=output.get('usage')
     if isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens')):
      for k in ('prompt_tokens','completion_tokens'):self.usage[k]+=usage[k]
     else:self.usage['unknown_usage_requests']+=1
     choice=output['choices'][0]
     if choice.get('finish_reason')!='stop':raise ValueError('chat_response_incomplete')
     result=json.loads(choice['message']['content'])
     if not isinstance(result,dict):raise ValueError('chat_json_object_required')
     return result
    except HTTPError as exc:
     code=exc.code;after=exc.headers.get('Retry-After') if exc.headers else None;exc.close();self.usage['unknown_usage_requests']+=1
     if code not in (408,429,500,502,503,504) or attempt==self.settings.retries:raise RuntimeError('deepseek_http_'+str(code)) from None
     delay=retry_delay(attempt,after)
     if delay>30:raise DependencyUnavailable('deepseek_retry_deferred',delay) from None
    except (URLError,TimeoutError,OSError,HTTPException):
     self.usage['unknown_usage_requests']+=1
     if attempt==self.settings.retries:raise RuntimeError('deepseek_connection_failed') from None
     delay=retry_delay(attempt)
    except (KeyError,IndexError,TypeError,json.JSONDecodeError):raise ValueError('invalid_chat_response') from None
    retry_sleep(delay,self.sleep)

