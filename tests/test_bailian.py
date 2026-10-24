"""Offline Bailian contracts; never read .env or send requests."""
import base64,io,json,os,struct,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError,URLError
from insurance_rag.indexing.bailian import BailianEmbedding,NoRedirect
from insurance_rag.indexing.embedding import EmbeddingCache
from insurance_rag.runtime_env import load_env

class Reply(io.BytesIO):
    def __init__(self,body):super().__init__(json.dumps(body).encode())
class Opener:
    def __init__(self,results):self.results=iter(results);self.requests=[]
    def open(self,request,**kwargs):
        self.requests.append(request);response=next(self.results)
        if isinstance(response,Exception):raise response
        return Reply(response)
def payload(vectors):return {'model':'fixture-model','data':[{'index':i,'embedding':v} for i,v in enumerate(vectors)],'usage':{'prompt_tokens':5}}
def provider(results,**kwargs):return BailianEmbedding('https://dashscope.aliyuncs.com/compatible-mode/v1','fixture-key','fixture-model',2,opener=Opener(results),sleep=lambda _:None,**kwargs)

class BailianTests(unittest.TestCase):
    def test_request_shape_and_binding_no_secret(self):
        p=provider([payload([[3.,4.]])]);result=p.encode(['测试'])
        request=p.opener.requests[0];body=json.loads(request.data)
        self.assertEqual(body,{'model':'fixture-model','input':['测试'],'dimensions':2,'encoding_format':'float'})
        self.assertAlmostEqual(result[0][0],.6);self.assertAlmostEqual(result[0][1],.8)
        self.assertNotIn('fixture-key',json.dumps(p.binding));self.assertEqual(p.stats['prompt_tokens'],5)
    def test_shuffled_response_restored(self):
        data=payload([[1.,0.],[0.,1.]]);data['data'].reverse()
        self.assertEqual(provider([data]).encode(['甲','乙']),[[1.,0.],[0.,1.]])
    def test_duplicate_response_index_rejected(self):
        data=payload([[1.,0.],[0.,1.]]);data['data'][1]['index']=0
        with self.assertRaisesRegex(ValueError,'index_mismatch'):provider([data]).encode(['甲','乙'])
    def test_response_count_rejected(self):
        with self.assertRaisesRegex(ValueError,'count_mismatch'):provider([payload([])]).encode(['甲'])
    def test_nonfinite_and_dimension_rejected(self):
        for vector in ([1.], [float('nan'),1.],[0.,0.]):
            with self.assertRaises(ValueError):provider([payload([vector])]).encode(['甲'])
    def test_wrong_model_rejected(self):
        data=payload([[1.,0.]]);data['model']='different'
        with self.assertRaisesRegex(ValueError,'model_mismatch'):provider([data]).encode(['甲'])
    def test_base64_float32_supported(self):
        data=payload([base64.b64encode(struct.pack('<2f',3.,4.)).decode()])
        self.assertAlmostEqual(provider([data]).encode(['甲'])[0][0],.6)
    def test_redirects_and_nonaliyun_endpoints_rejected(self):
        self.assertIsNone(NoRedirect().redirect_request(None,None,None,None,None,None))
        for address in ('http://dashscope.aliyuncs.com/compatible-mode/v1','https://example.com/compatible-mode/v1',
                        'https://dashscope.aliyuncs.com.evil.test/compatible-mode/v1','https://user:pass@dashscope.aliyuncs.com/compatible-mode/v1',
                        'https://dashscope.aliyuncs.com/compatible-mode/v1/embeddings'):
            with self.assertRaisesRegex(ValueError,'base_url'):BailianEmbedding(address,'fixture','fixture',2)
    def test_429_retries_and_usage(self):
        error=HTTPError('https://test',429,'not logged',{'Retry-After':'0'},io.BytesIO(b'secret response'))
        p=provider([error,payload([[1.,0.]])]);self.assertEqual(p.encode(['甲']),[[1.,0.]])
        self.assertEqual(p.stats['requests'],2);self.assertEqual(p.stats['retries'],1)
    def test_401_not_retried_or_echoed(self):
        error=HTTPError('https://secret',401,'fixture-key',{},io.BytesIO(b'fixture-key'))
        p=provider([error])
        with self.assertRaisesRegex(RuntimeError,'^bailian_http_401$'):p.encode(['甲'])
        self.assertEqual(p.stats['requests'],1)
    def test_network_error_sanitized(self):
        with self.assertRaisesRegex(RuntimeError,'^bailian_connection_failed$'):provider([URLError('secret')],retries=0).encode(['甲'])
    def test_batch_byte_budget_and_input_preflight(self):
        p=provider([payload([[1.,0.]]),payload([[0.,1.]])],max_input_bytes=4,max_batch_bytes=4)
        self.assertEqual(len(p.encode(['甲','乙'])),2)
        with self.assertRaisesRegex(ValueError,'conservative_limit'):p.encode(['太长文本'])
        self.assertEqual(p.stats['requests'],2)
    def test_cache_deduplicates_and_resumes(self):
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'cache.sqlite3';p=provider([payload([[1.,0.]])]);cache=EmbeddingCache(path)
            vectors,stats=cache.encode(['甲','甲'],p);cache.close()
            self.assertEqual(stats['computed_unique_texts'],1);self.assertEqual(vectors[0],vectors[1])
            cache=EmbeddingCache(path);other=provider([]);_,stats=cache.encode(['甲'],other);cache.close()
            self.assertEqual(stats['cache_hits'],1);self.assertEqual(other.stats['requests'],0)
    def test_env_literal_blank_preserves_and_invalid_atomic(self):
        with tempfile.TemporaryDirectory() as tmp,patch.dict(os.environ,{'KEEP':'existing'},clear=True):
            path=Path(tmp)/'settings';path.write_text("# comment\nKEEP=\nNEW='literal $HOME $(command)'\n",encoding='utf8')
            load_env(path);self.assertEqual(os.environ['KEEP'],'existing');self.assertEqual(os.environ['NEW'],'literal $HOME $(command)')
            path.write_text('OTHER=value\ninvalid secret\n',encoding='utf8')
            with self.assertRaisesRegex(ValueError,'line_2'):load_env(path)
            self.assertNotIn('OTHER',os.environ)
    def test_empty_inputs_never_call_api(self):
        p=provider([]);self.assertEqual(p.encode([]),[])
        with self.assertRaises(ValueError):p.encode([''])
        self.assertEqual(p.stats['requests'],0)
if __name__=='__main__':unittest.main()
