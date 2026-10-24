"""Bailian hosted text rerank with complete, index-bound candidate scores."""
import os,math,time
from urllib.parse import urlparse
from insurance_rag.parsing.common import stable_hash
from insurance_rag.indexing.bailian import BailianEmbedding

class BailianReranker:
    def __init__(self,base_url,api_key,model='qwen3.7-text-rerank',*,opener=None,sleep=time.sleep):
        # Reuse the validated HTTPS JSON transport, retries and secret-safe errors.
        # The internal transport never calls the embedding endpoint or encode().
        self._transport=BailianEmbedding(base_url,api_key,model,1,opener=opener,sleep=sleep)
        url=urlparse(base_url)
        self._transport.endpoint=url.scheme+'://'+url.netloc+'/api/v1/services/rerank/text-rerank/text-rerank'
        if model not in ('qwen3.7-text-rerank','qwen3-rerank'):raise ValueError('unsupported_reranker_model')
        self.model=model;self.max_document_bytes=30000 if model=='qwen3.7-text-rerank' else 4000
        self.binding={'provider':'aliyun_bailian_text_rerank','model':model,'endpoint_hash':stable_hash(self._transport.endpoint),
                      'input_policy':'untruncated_content_v1','instruction_policy':'provider_default_qa_v1'}
        self.stats=self._transport.stats
    @classmethod
    def from_environment(cls):
        provider=cls(os.environ.get('DASHSCOPE_BASE_URL',''),os.environ.get('DASHSCOPE_API_KEY',''),
                   os.environ.get('DASHSCOPE_RERANK_MODEL','qwen3.7-text-rerank'))
        from insurance_rag.reliability.shared_policy import SharedDependencyPolicy
        from pathlib import Path
        provider._transport.policy=SharedDependencyPolicy(Path(__file__).resolve().parents[3]/'data/ops/dependencies.sqlite3',stable_hash([provider._transport.endpoint,provider.model]))
        return provider
    def score(self,query,contents):
        if not isinstance(query,str) or not query.strip() or len(query.encode('utf8'))>4000:raise ValueError('invalid_rerank_query')
        if not isinstance(contents,(list,tuple)) or len(contents)>500 or any(not isinstance(c,str) or not c.strip() for c in contents):raise ValueError('invalid_rerank_contents')
        if not contents:return []
        sizes=[len(c.encode('utf8')) for c in contents]
        if any(n>self.max_document_bytes for n in sizes):raise ValueError('rerank_document_exceeds_conservative_limit')
        if sum(sizes)+len(query.encode('utf8'))*len(contents)>120000:raise ValueError('rerank_request_exceeds_conservative_limit')
        began=time.monotonic()
        try:
            response=self._transport._request_payload({'model':self.model,'input':{'query':query,'documents':list(contents)},
                'parameters':{'top_n':len(contents)}})
            output=response.get('output') if isinstance(response,dict) else None
            rows=output.get('results') if isinstance(output,dict) else None
            if not isinstance(rows,list) or len(rows)!=len(contents):raise ValueError('rerank_response_count_mismatch')
            scores=[None]*len(contents)
            for row in rows:
                if not isinstance(row,dict):raise ValueError('rerank_response_invalid_item')
                index=row.get('index');value=row.get('relevance_score')
                if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<len(contents) or scores[index] is not None:raise ValueError('rerank_response_index_mismatch')
                if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or not 0<=value<=1:raise ValueError('rerank_response_invalid_score')
                scores[index]=float(value)
            self.stats['input_texts']+=len(contents)
            usage=response.get('usage',{})
            tokens=usage.get('total_tokens',usage.get('prompt_tokens')) if isinstance(usage,dict) else None
            if isinstance(tokens,int) and not isinstance(tokens,bool) and tokens>=0:
                self.stats['prompt_tokens']+=tokens;self.stats['usage_responses']+=1
            return scores
        finally:self.stats['elapsed_seconds']+=round(time.monotonic()-began,6)
