"""Native Chat Completions tool protocol; no reasoning is logged or exposed."""
import json
from urllib.request import Request
from urllib.error import HTTPError,URLError
from insurance_rag.reliability.runtime import bounded_timeout,check_budget,retry_sleep,retry_delay,DependencyUnavailable

def strict_json(text):
 def pairs(items):
  result={}
  for key,value in items:
   if key in result:raise ValueError('duplicate_json_key')
   result[key]=value
  return result
 def constant(value):raise ValueError('nonfinite_json')
 return json.loads(text,object_pairs_hook=pairs,parse_constant=constant)

def normalize_reply(message,finish,usage=None,request_id=None):
 if finish not in ('stop','tool_calls'):raise ValueError('native_response_incomplete')
 if not isinstance(message,dict) or message.get('role','assistant')!='assistant':raise ValueError('native_assistant_required')
 calls=message.get('tool_calls') or []
 if not isinstance(calls,list) or len(calls)>8:raise ValueError('native_tool_batch_limit')
 found=set();normalized=[]
 for call in calls:
  if call.get('type')!='function' or not isinstance(call.get('id'),str) or not 1<=len(call['id'])<=200 or call['id'] in found:raise ValueError('native_call_identity_invalid')
  found.add(call['id']);function=call.get('function',{});name=function.get('name');arguments=function.get('arguments')
  if not isinstance(name,str) or not name or len(name)>100 or not isinstance(arguments,str) or len(arguments)>32000:raise ValueError('native_call_invalid')
  # Invalid JSON is preserved as a call so it gets a paired, bounded tool error.
  normalized.append({'id':call['id'],'type':'function','function':{'name':name,'arguments':arguments}})
 content=message.get('content') or ''
 if not isinstance(content,str) or len(content)>32000:raise ValueError('native_content_invalid')
 if finish=='tool_calls' and not normalized:raise ValueError('native_missing_tool_calls')
 if finish=='stop' and normalized:raise ValueError('native_finish_mismatch')
 return {'message':{'role':'assistant','content':content,'tool_calls':normalized} if normalized else {'role':'assistant','content':content},'finish_reason':finish,'usage':usage,'provider_request_id':request_id}

class NativeToolModel:
 def __init__(self,client):
  self.client=client;self.usage=client.usage
  self.binding={**client.binding,'protocol':'native_chat_tools_v1','loop_thinking':False}
 def structured(self,*args,**kwargs):return self.client.structured(*args,**kwargs)
 def chat(self,messages,tools,*,reserve=None,stream=False):
  settings=self.client.settings
  body={'model':settings.model,'messages':messages,'tools':tools,'tool_choice':'auto','response_format':{'type':'json_object'},'thinking':{'type':'disabled'},'temperature':0,'max_tokens':settings.max_output_tokens,'stream':stream}
  if not tools:body.pop('tools');body.pop('tool_choice')
  if stream:body['stream_options']={'include_usage':True}
  encoded=json.dumps(body,ensure_ascii=False,allow_nan=False).encode()
  if len(encoded)>300000:raise ValueError('native_request_context_limit')
  with self.client.policy.call():
   for attempt in range(min(settings.retries,2)+1):
    check_budget()
    if reserve:reserve()
    self.usage['requests']+=1
    request=Request(settings.base_url+'/chat/completions',data=encoded,headers={'Content-Type':'application/json','Authorization':'Bearer '+settings.api_key},method='POST')
    try:
     with self.client.opener.open(request,timeout=bounded_timeout(settings.timeout_seconds)) as response:
      if stream:raw=self._stream(response)
      else:
       data=response.read(2*1024*1024+1)
       if len(data)>2*1024*1024:raise ValueError('native_response_too_large')
       raw=strict_json(data.decode('utf8'))
     usage=raw.get('usage')
     if isinstance(usage,dict) and all(type(usage.get(k)) is int and usage[k]>=0 for k in ('prompt_tokens','completion_tokens')):
      for key in ('prompt_tokens','completion_tokens'):self.usage[key]+=usage[key]
     else:self.usage['unknown_usage_requests']+=1
     choice=raw['choices'][0]
     return normalize_reply(choice['message'],choice.get('finish_reason'),usage,raw.get('id'))
    except HTTPError as exc:
     code=exc.code;after=exc.headers.get('Retry-After') if exc.headers else None;exc.close();self.usage['unknown_usage_requests']+=1
     if code not in (408,429,500,502,503,504) or attempt==min(settings.retries,2):raise RuntimeError('deepseek_http_'+str(code)) from None
     delay=retry_delay(attempt,after)
     if delay>30:raise DependencyUnavailable('deepseek_retry_deferred',delay)
    except (URLError,TimeoutError,OSError):
     self.usage['unknown_usage_requests']+=1
     if attempt==min(settings.retries,2):raise RuntimeError('deepseek_connection_failed') from None
     delay=retry_delay(attempt)
    except (KeyError,IndexError,TypeError,json.JSONDecodeError):raise ValueError('native_response_invalid') from None
    retry_sleep(delay,self.client.sleep)
 def _stream(self,response):
  content=[];calls={};finish=None;usage=None;request_id=None;size=0;done=False
  for line in response:
   size+=len(line)
   if size>2*1024*1024:raise ValueError('native_stream_too_large')
   if not line.startswith(b'data:'):continue
   data=line[5:].strip()
   if data==b'[DONE]':done=True;break
   packet=strict_json(data.decode('utf8'));request_id=packet.get('id',request_id)
   if packet.get('usage') is not None:usage=packet['usage']
   for choice in packet.get('choices',[]):
    if choice.get('index',0)!=0:raise ValueError('native_multiple_choices')
    delta=choice.get('delta',{})
    if delta.get('content'):content.append(delta['content'])
    for part in delta.get('tool_calls',[]):
     index=part.get('index')
     if type(index) is not int or not 0<=index<8:raise ValueError('native_stream_call_index')
     item=calls.setdefault(index,{'id':'','type':'function','function':{'name':'','arguments':''}})
     if part.get('id'):item['id']+=part['id']
     if part.get('type'):item['type']=part['type']
     function=part.get('function',{})
     for key in ('name','arguments'):
      if function.get(key):item['function'][key]+=function[key]
    if choice.get('finish_reason') is not None:finish=choice['finish_reason']
  if not done or finish is None:raise ValueError('native_stream_incomplete')
  return {'id':request_id,'usage':usage,'choices':[{'finish_reason':finish,'message':{'role':'assistant','content':''.join(content),'tool_calls':[calls[i] for i in sorted(calls)]}}]}
