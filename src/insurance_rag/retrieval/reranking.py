"""Cross-encoder reranking; no lexical scores disguised as model judgments."""
import math
from pathlib import Path

class LocalCrossEncoder:
    def __init__(self,folder,max_tokens=512):
        from sentence_transformers import CrossEncoder
        folder=Path(folder).resolve(strict=True)
        self.model=CrossEncoder(str(folder),local_files_only=True,trust_remote_code=False,device='cpu',max_length=max_tokens)
        self.max_tokens=max_tokens
    def score(self,query,contents):
        pairs=[(query,text) for text in contents]
        for a,b in pairs:
            if len(self.model.tokenizer.encode(a,b,add_special_tokens=True,truncation=False))>self.max_tokens:
                raise ValueError('reranker_pair_would_truncate')
        values=self.model.predict(pairs,show_progress_bar=False)
        return values.tolist()

def rerank(query,hits,provider,top_k=10):
    if isinstance(top_k,bool) or not isinstance(top_k,int) or top_k<1:raise ValueError('invalid_rerank_top_k')
    unique={}
    for hit in hits:unique.setdefault(hit['record']['record_id'],hit)
    candidates=list(unique.values())
    if not candidates:return []
    scores=provider.score(query,[h['record']['content'] for h in candidates])
    if len(scores)!=len(candidates):raise ValueError('reranker_response_count_mismatch')
    if any(isinstance(v,bool) or not isinstance(v,(int,float)) or not math.isfinite(v) for v in scores):
        raise ValueError('invalid_reranker_score')
    result=[{**hit,'retrieval_score':hit['score'],'score':score,'reranker_score':score} for hit,score in zip(candidates,scores)]
    return sorted(result,key=lambda h:(-h['score'],h['record']['record_id']))[:top_k]
