import unittest
from contextlib import contextmanager
from unittest.mock import patch
from fastapi.testclient import TestClient
from insurance_rag.api.app import create_app
class LocalSessionTests(unittest.TestCase):
 def factory(self):
  @contextmanager
  def factory():
   class App:
    def new_thread(self,owner):return 'thread-for-'+owner
   yield App()
  return factory
 def client(self,**kwargs):return TestClient(create_app(self.factory(),local_test_session=True),base_url='http://127.0.0.1:8765',client=('127.0.0.1',12345),**kwargs)
 def test_loopback_page_bootstraps_http_only_session_without_key(self):
  with self.client() as c:
   page=c.get('/agent');self.assertEqual(page.status_code,200);self.assertIn('data-local-session="true"',page.text)
   self.assertIn('HttpOnly',page.headers['set-cookie']);self.assertIn('SameSite=strict',page.headers['set-cookie'])
   self.assertEqual(c.post('/v1/threads').json()['thread_id'],'thread-for-local-user')
 def test_cross_origin_cookie_is_not_authorization(self):
  with self.client() as c:
   c.get('/agent')
   self.assertNotEqual(c.post('/v1/threads',headers={'Origin':'https://foreign.example','Sec-Fetch-Site':'cross-site'}).status_code,200)
   self.assertEqual(c.get('/agent',headers={'Sec-Fetch-Site':'cross-site'}).status_code,403)
 def test_foreign_host_cannot_bootstrap(self):
  with self.client() as c:self.assertEqual(c.get('/agent',headers={'Host':'foreign.example'}).status_code,403)
 def test_remote_client_cannot_bootstrap(self):
  with TestClient(create_app(self.factory(),local_test_session=True),base_url='http://127.0.0.1:8765',client=('192.0.2.1',1000)) as c:self.assertEqual(c.get('/agent').status_code,403)
 def test_default_server_does_not_enable_local_cookie(self):
  with patch.dict('os.environ',{'INSURANCE_AGENT_API_TOKEN':'test-only'}),TestClient(create_app(self.factory()),base_url='http://127.0.0.1:8765',client=('127.0.0.1',12345)) as c:
   self.assertNotIn('set-cookie',c.get('/agent').headers)
   self.assertEqual(c.post('/v1/threads').status_code,401)
   self.assertEqual(c.post('/v1/threads',headers={'Authorization':'Bearer test-only'}).status_code,200)

 def test_extension_top_level_navigation_opens_local_page_but_not_api(self):
  headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
  with self.client() as c:
   self.assertEqual(c.get('/agent',headers=headers).status_code,200)
   self.assertNotEqual(c.post('/v1/threads',headers=headers).status_code,200)
   self.assertEqual(c.post('/v1/threads',headers={'Origin':'http://127.0.0.1:8765','Sec-Fetch-Site':'same-origin'}).status_code,200)
 def test_navigation_exception_does_not_allow_foreign_origin_or_iframe(self):
  headers={'Sec-Fetch-Site':'cross-site','Sec-Fetch-Mode':'navigate','Sec-Fetch-Dest':'document'}
  with self.client() as c:
   self.assertEqual(c.get('/agent',headers={**headers,'Origin':'https://foreign.example'}).status_code,403)
   self.assertEqual(c.get('/agent',headers={**headers,'Sec-Fetch-Dest':'iframe'}).status_code,403)
   self.assertEqual(c.get('/agent',headers={**headers,'Host':'foreign.example'}).status_code,403)
