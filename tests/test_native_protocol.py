import io,json,unittest
from types import SimpleNamespace
from contextlib import nullcontext
from insurance_rag.agent.model_adapter import NativeToolModel,normalize_reply
class ProtocolTests(unittest.TestCase):
 def test_truncated_reply_rejected(self):
  for reason in ('length','content_filter',None):
   with self.subTest(reason=reason),self.assertRaises(ValueError):normalize_reply({'content':'x'},reason)
 def test_duplicate_call_ids_rejected(self):
  call={'id':'a','type':'function','function':{'name':'resolve_products','arguments':'{}'}}
  with self.assertRaises(ValueError):normalize_reply({'tool_calls':[call,call]},'tool_calls')
 def test_invalid_json_preserved_as_paired_call(self):
  call={'id':'a','type':'function','function':{'name':'resolve_products','arguments':'{'}}
  self.assertEqual(normalize_reply({'tool_calls':[call]},'tool_calls')['message']['tool_calls'][0]['function']['arguments'],'{')
 def test_finish_reason_consistency(self):
  with self.assertRaises(ValueError):normalize_reply({},'tool_calls')
 def model(self):return NativeToolModel(SimpleNamespace(usage={},binding={}))
 def stream(self,packets,done=True):
  data=b''.join(b'data: '+json.dumps(p).encode()+b'\n\n' for p in packets)+(b'data: [DONE]\n\n' if done else b'')
  return self.model()._stream(io.BytesIO(data))
 def test_stream_arguments_assembled_before_execution(self):
  raw=self.stream([{'id':'r','choices':[{'index':0,'delta':{'tool_calls':[{'index':0,'id':'c','type':'function','function':{'name':'resolve_','arguments':'{"hint":'}}]}}]},{'choices':[{'delta':{'tool_calls':[{'index':0,'function':{'name':'products','arguments':'null}'}}]},'finish_reason':'tool_calls'}]},{'choices':[],'usage':{'prompt_tokens':10,'completion_tokens':2}}])
  reply=normalize_reply(raw['choices'][0]['message'],raw['choices'][0]['finish_reason'],raw['usage']);call=reply['message']['tool_calls'][0];self.assertEqual(call['function']['name'],'resolve_products');self.assertEqual(call['function']['arguments'],'{"hint":null}');self.assertEqual(reply['usage']['prompt_tokens'],10)
 def test_stream_missing_done_not_accepted(self):
  with self.assertRaises(ValueError):self.stream([{'choices':[{'delta':{'content':'x'},'finish_reason':'stop'}]}],False)
 def test_stream_missing_finish_not_accepted(self):
  with self.assertRaises(ValueError):self.stream([{'choices':[{'delta':{'content':'x'}}]}])
 def test_stream_unknown_usage_remains_unknown(self):
  self.assertIsNone(self.stream([{'choices':[{'delta':{'content':'{}'},'finish_reason':'stop'}]}])['usage'])
 def test_reasoning_never_normalized_or_returned(self):
  reply=normalize_reply({'content':'{}','reasoning_content':'private'},'stop');self.assertNotIn('reasoning_content',str(reply))
