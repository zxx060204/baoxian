"""Offline regressions for the ten-company scope; no retained insurance documents."""
import json,pathlib,ssl,tempfile,unittest,urllib.error
from unittest.mock import patch
from insurance_rag.acquisition.transport import Transport,FetchError,digest
from insurance_rag.acquisition import adapters,crawler
class TransportTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.delays=[]; self.t=Transport(['example.com'],self.tmp.name,interval=0,sleeper=self.delays.append)
    def run_sequence(self,sequence):
        calls=[]
        def once(url,data,headers,backend,timeout,limit):
            calls.append((backend,timeout,limit)); step=sequence.pop(0)
            if isinstance(step,Exception):raise step
            return step,dict(http_status=200,sha256=digest(step),bytes=len(step))
        self.t._once=once
        return calls
    def test_ssl_switch_and_evidence(self):
        self.t.curl='curl.exe'; calls=self.run_sequence([ssl.SSLError('EOF'),b'good'])
        body,meta=self.t.fetch('https://example.com/document')
        self.assertEqual([c[0] for c in calls],['urllib','curl']); self.assertEqual(meta['retry_count'],1)
        saved=json.loads(next(pathlib.Path(self.tmp.name).glob('*.json')).read_text())
        self.assertEqual(saved['attempts'][0]['failure_kind'],'tls_connection')
        self.assertEqual(body,b'good')
    def test_timeout_increases_and_bounds(self):
        calls=self.run_sequence([TimeoutError('timeout')]*3)
        with self.assertRaises(FetchError) as cm:self.t.fetch('https://example.com/doc')
        self.assertEqual(cm.exception.kind,'timeout'); self.assertEqual([x[1] for x in calls],[45,90,90])
        self.assertEqual(self.delays,[3,6]); self.assertEqual(len(cm.exception.attempts),3)
    def test_retry_after_server(self):
        self.run_sequence([FetchError('server_transient','503',503,{'Retry-After':'7'}),b'ok'])
        self.t.fetch('https://example.com/doc'); self.assertEqual(self.delays,[7])
    def test_access_certificate_and_404_no_blind_retry(self):
        for exc,kind in [(urllib.error.HTTPError('https://example.com',403,'forbidden',{},None),'access_restriction'),(urllib.error.HTTPError('https://example.com',429,'limit',{},None),'access_restriction'),(ssl.SSLCertVerificationError('certificate'),'tls_certificate'),(FetchError('not_found','404',404),'not_found')]:
            calls=self.run_sequence([exc])
            with self.assertRaises(FetchError) as cm:self.t.fetch('https://example.com/doc')
            self.assertEqual(cm.exception.kind,kind); self.assertEqual(len(calls),1)
    def test_retry_after_long_is_deferred(self):
        calls=self.run_sequence([FetchError('server_transient','503',503,{'Retry-After':'3600'})])
        with self.assertRaises(FetchError):self.t.fetch('https://example.com/doc')
        self.assertEqual(len(calls),1); self.assertEqual(self.delays,[])
    def test_size_increases_once(self):
        calls=self.run_sequence([FetchError('size_limit','limit'),FetchError('size_limit','limit')])
        with patch('insurance_rag.acquisition.transport.shutil.disk_usage',return_value=type('Space',(),{'free':10*1024**3})()):
            with self.assertRaises(FetchError):self.t.fetch('https://example.com/doc')
        self.assertEqual([x[2] for x in calls],[128*1024**2,256*1024**2])
    def test_redirect_does_not_leave_official_domains(self):
        self.t._urllib=lambda *args:(b'',302,{'Location':'https://evil.example/doc'},args[0])
        with self.assertRaises(FetchError) as cm:self.t.fetch('https://example.com/doc')
        self.assertEqual(cm.exception.kind,'unapproved_redirect')
    def test_length_mismatch_retry(self):
        self.t._urllib=lambda *args:(b'short',200,{'Content-Length':'10'},args[0])
        with self.assertRaises(FetchError) as cm:self.t.fetch('https://example.com/doc')
        self.assertEqual(cm.exception.kind,'incomplete_body'); self.assertEqual(len(cm.exception.attempts),3)


class ScopeTests(unittest.TestCase):
    def test_exact_ten_and_defaults(self):
        expected={'AIA友邦','PingAn平安','Allianz安联','HSBC汇丰','CPIC太平洋人寿','NewChina新华保险','Taikang泰康人寿','中国人寿','人保寿险','工银安盛人寿'}
        self.assertEqual(set(adapters.COMPANIES),expected)
        self.assertEqual(set(adapters.DEFAULT_COMPANIES),expected)
    def test_icbc_same_row_four_files(self):
        from insurance_rag.acquisition.expanded_adapters import parse_html
        markup='<table><tr><td>测试两全保险</td><td><a href="/a.pdf">产品条款</a><a href="/b.pdf">产品说明书</a><a href="/c.xlsx">产品费率表</a><a href="/d.pdf">产品现金价值表</a></td></tr></table>'
        items,_=parse_html('工银安盛人寿',markup,'https://www.icbc-axa.com/')
        self.assertEqual({r['category'] for r in items},set(adapters.CORE))
        self.assertEqual(len({r['product_identity'] for r in items}),1)
    def test_chinalife_row_and_pagination(self):
        from insurance_rag.acquisition.public_api_adapters import discover
        class FakeClient:
            def __init__(self):self.calls=[]
            def get(self,url,**kwargs):
                self.calls.append(url)
                return json.dumps({'count':1,'list':[{'productname':'测试年金保险','issell':'1','clause':'/a.pdf','productdescription':'/b.pdf','ratestable':'/c.pdf','cashvalue':'/d.pdf'}]}).encode(),{}
        client=FakeClient();cat=discover('中国人寿',client)
        self.assertEqual(len(client.calls),1)
        self.assertEqual({r['category'] for r in cat['candidates']},set(adapters.CORE))
    def test_cpic_references_remain_same_product(self):
        markup='<tr><td>1</td><td><a title="测试终身寿险" href="/a.pdf">测试终身寿险</a></td><td><a href="/b.pdf">费率</a></td><td></td><td></td></tr>'
        products,items=adapters.parse_cpic(markup,'https://www.cpic.com.cn/')
        self.assertEqual(len(products),1)
        self.assertEqual({r['category'] for r in items},{'条款','费率表'})
    def test_no_previous_result_cache(self):
        self.assertEqual(crawler.existing_cache(),({},[]))

if __name__=='__main__':unittest.main()
