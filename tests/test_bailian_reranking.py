"""Offline hosted reranking response, budget and transport contracts."""
import io,json,unittest
from insurance_rag.retrieval.bailian_reranking import BailianReranker
class Opener:
    def __init__(self,data):self.data=data;self.calls=[]
    def open(self,request,**kwargs):self.calls.append(request);return io.BytesIO(json.dumps(self.data).encode())
def make(rows):
    opener=Opener({'output':{'results':rows},'usage':{'total_tokens':12}})
    return BailianReranker('https://dashscope.aliyuncs.com/compatible-mode/v1','fixture-key',opener=opener)
class HostedRerankTests(unittest.TestCase):
    def test_shuffled_result_indices_and_endpoint(self):
        p=make([{'index':1,'relevance_score':.8},{'index':0,'relevance_score':.2}]);self.assertEqual(p.score('责任',['保障','免责']),[.2,.8])
        req=p._transport.opener.calls[0];self.assertTrue(req.full_url.endswith('/api/v1/services/rerank/text-rerank/text-rerank'))
        body=json.loads(req.data);self.assertEqual(body['parameters']['top_n'],2);self.assertEqual(body['input']['documents'],['保障','免责'])
        self.assertNotIn('fixture-key',json.dumps(p.binding));self.assertEqual(p.stats['prompt_tokens'],12)
    def test_count_mismatch_rejected(self):
        with self.assertRaisesRegex(ValueError,'count_mismatch'):make([]).score('责任',['保障'])
    def test_duplicate_indices_rejected(self):
        with self.assertRaisesRegex(ValueError,'index_mismatch'):make([{'index':0,'relevance_score':.8},{'index':0,'relevance_score':.2}]).score('责任',['甲','乙'])
    def test_invalid_scores_rejected(self):
        for value in (float('nan'),True,-.1,1.1,'0.5'):
            with self.assertRaisesRegex(ValueError,'invalid_score'):make([{'index':0,'relevance_score':value}]).score('责任',['正文'])
    def test_oversized_document_not_truncated(self):
        p=make([])
        with self.assertRaisesRegex(ValueError,'conservative_limit'):p.score('责任',['甲'*10001])
        self.assertEqual(p._transport.opener.calls,[])
    def test_request_budget_no_cross_batch_score_mixing(self):
        p=make([])
        with self.assertRaisesRegex(ValueError,'request_exceeds'):p.score('责任',['甲'*5000]*9)
        self.assertEqual(p._transport.opener.calls,[])
    def test_empty_no_network(self):
        p=make([]);self.assertEqual(p.score('责任',[]),[]);self.assertEqual(p._transport.opener.calls,[])
    def test_unknown_model_rejected(self):
        with self.assertRaisesRegex(ValueError,'unsupported_reranker'):BailianReranker('https://dashscope.aliyuncs.com/compatible-mode/v1','fixture','invented-model')
if __name__=='__main__':unittest.main()
