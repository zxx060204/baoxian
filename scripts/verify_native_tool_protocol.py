"""Real synthetic native-tool protocol probe; no customer data or raw reasoning."""
import sys,json
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.runtime_env import load_env
from insurance_rag.agent.models import DeepSeekChat
from insurance_rag.agent.model_adapter import NativeToolModel,strict_json
from insurance_rag.services.config import ChatSettings
from insurance_rag.parsing.common import write_json
root=Path(__file__).resolve().parents[1];load_env(root/'.env');out=root/sys.argv[1];cases=[]
schema={'type':'function','function':{'name':'lookup_test_code','description':'读取合成测试编码的数值，返回值必须通过工具获取。','parameters':{'type':'object','properties':{'code':{'type':'string','enum':['甲','乙']}},'required':['code'],'additionalProperties':False}}}
for stream in (False,True):
 for repeat in range(3):
  model=NativeToolModel(DeepSeekChat(ChatSettings.from_environment()));messages=[{'role':'system','content':'使用工具获取两个编码的值，工具结果返回后给出JSON {"sum":总和}，不能猜测。'},{'role':'user','content':'请分别查询合成编码甲和乙的值，然后相加。'}];calls=[];passed=False;error=None;detail=None
  try:
   for turn in range(4):
    reply=model.chat(messages,[schema],stream=stream);message=reply['message'];messages.append(message)
    if not message.get('tool_calls'):
     final=strict_json(message['content']);passed=final.get('sum')==42 and set(calls)=={'甲','乙'};break
    for call in message['tool_calls']:
     if call['function']['name']!='lookup_test_code':raise ValueError('unexpected_tool')
     arguments=strict_json(call['function']['arguments'])
     if set(arguments)!={'code'} or arguments['code'] not in ('甲','乙'):raise ValueError('unexpected_arguments')
     code=arguments['code'];calls.append(code);messages.append({'role':'tool','tool_call_id':call['id'],'content':json.dumps({'value':17 if code=='甲' else 25},ensure_ascii=False)})
  except Exception as exc:
   from insurance_rag.reliability.runtime import error_code
   error=error_code(exc);detail=exc.msg if isinstance(exc,json.JSONDecodeError) else str(exc) if isinstance(exc,ValueError) else type(exc).__name__
  cases.append({'stream':stream,'repeat':repeat+1,'tool_calls':len(calls),'codes':calls,'requests':model.usage['requests'],'usage':model.usage,'passed':passed,'error_code':error,'diagnostic':detail})
  write_json(out/'native_protocol.json',{'cases':cases,'passed':sum(c['passed'] for c in cases),'total':6,'status':'passed' if len(cases)==6 and all(c['passed'] for c in cases) else 'in_progress' if len(cases)<6 else 'failed','source':'synthetic_codes','reasoning_recorded':False})
  print(json.dumps(cases[-1],ensure_ascii=False),flush=True)
raise SystemExit(0 if all(c['passed'] for c in cases) else 1)
