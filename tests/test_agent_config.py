import unittest
from insurance_rag.services.config import ChatSettings,AgentSettings
class AgentConfigTests(unittest.TestCase):
 def test_secrets_not_in_repr_or_binding(self):
  c=ChatSettings.from_environment({'DEEPSEEK_API_KEY':'test-secret'})
  self.assertNotIn('test-secret',repr(c));self.assertNotIn('test-secret',str(c.binding));self.assertEqual(c.model,'deepseek-flash')
 def test_missing_deepseek_key_does_not_use_bailian_key(self):
  with self.assertRaises(ValueError):ChatSettings.from_environment({'DASHSCOPE_API_KEY':'test-only'})
 def test_endpoint_credentials_and_other_hosts_rejected(self):
  for url in ['http://api.deepseek.com','https://user:password@api.deepseek.com','https://api.deepseek.com.evil.test','https://api.deepseek.com/chat/completions','https://api.deepseek.com:bad']:
   with self.subTest(url=url),self.assertRaises(ValueError):ChatSettings.from_environment({'DEEPSEEK_API_KEY':'test-only','DEEPSEEK_BASE_URL':url})
 def test_invalid_budget_and_thinking_rejected(self):
  for field,value in [('DEEPSEEK_THINKING_ENABLED','yes'),('DEEPSEEK_CHAT_RETRIES','9'),('DEEPSEEK_CHAT_TIMEOUT_SECONDS','nan'),('DEEPSEEK_CHAT_MAX_OUTPUT_TOKENS','0')]:
   with self.subTest(field=field),self.assertRaises(ValueError):ChatSettings.from_environment({'DEEPSEEK_API_KEY':'test-only',field:value})
 def test_defaults(self):
  a=AgentSettings.from_environment({});self.assertEqual(a.environment,'dev-reliability')
if __name__=='__main__':unittest.main()
