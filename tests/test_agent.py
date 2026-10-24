import tempfile,unittest,json
from pathlib import Path

class AgentProviderTests(unittest.TestCase):
 def test_known_usage_and_no_reasoning_exposure(self):
  import io
  from insurance_rag.agent.models import DeepSeekChat
  from insurance_rag.services.config import ChatSettings
  from insurance_rag.reliability.runtime import DependencyPolicy
  class Opener:
   def open(self,request,timeout):return io.BytesIO(json.dumps({'choices':[{'finish_reason':'stop','message':{'content':'{"status":"ok"}','reasoning_content':'private'}}],'usage':{'prompt_tokens':10,'completion_tokens':3}}).encode())
  p=DeepSeekChat(ChatSettings.from_environment({'DEEPSEEK_API_KEY':'test-key'}),opener=Opener());p.policy=DependencyPolicy();calls=[];r=p.structured('JSON',{},reserve=lambda:calls.append(1));self.assertEqual(r,{'status':'ok'});self.assertEqual(len(calls),1);self.assertEqual(p.usage['prompt_tokens'],10)
 def test_model_truncation_rejected(self):
  import io
  from insurance_rag.agent.models import DeepSeekChat
  from insurance_rag.services.config import ChatSettings
  from insurance_rag.reliability.runtime import DependencyPolicy
  class Opener:
   def open(self,*a,**kw):return io.BytesIO(json.dumps({'choices':[{'finish_reason':'length','message':{'content':'{}'}}]}).encode())
  p=DeepSeekChat(ChatSettings.from_environment({'DEEPSEEK_API_KEY':'test-key'}),opener=Opener());p.policy=DependencyPolicy()
  with self.assertRaises(ValueError):p.structured('JSON',{})

if __name__=='__main__':unittest.main()

