"""Deterministic, auditable two-route fusion with per-query normalization."""
import math
from insurance_rag.parsing.common import stable_hash

def validate_fusion(method='rrf',rrf_k=60,dense_weight=0.5):
    if method not in ('rrf','weighted'):raise ValueError('invalid_fusion_method')
    if isinstance(rrf_k,bool) or not isinstance(rrf_k,int) or rrf_k<=0:raise ValueError('invalid_rrf_k')
    if isinstance(dense_weight,bool) or not isinstance(dense_weight,(int,float)) or not math.isfinite(dense_weight) or not 0<=dense_weight<=1:
        raise ValueError('invalid_dense_weight')

def fuse(routes,*,method='rrf',rrf_k=60,dense_weight=0.5,top_k=10):
    validate_fusion(method,rrf_k,dense_weight)
    if isinstance(top_k,bool) or not isinstance(top_k,int) or not 1<=top_k<=100:raise ValueError('invalid_fusion_top_k')
    if not isinstance(routes,dict) or set(routes)-{'bm25','dense'}:raise ValueError('invalid_fusion_routes')
    unique={};identities={};normalized={};bounds={}
    for route,hits in routes.items():
        rows=[];seen=set()
        for hit in hits:
            record=hit['record'];rid=record['record_id'];score=hit['score']
            if isinstance(score,bool) or not isinstance(score,(int,float)) or not math.isfinite(score):raise ValueError('invalid_fusion_route_score')
            signature=stable_hash([record.get(k) for k in ('scope','product_version_id','document_version_id','layout_sha256','source_sha256','content')])
            if rid in identities and identities[rid]!=signature:raise ValueError('fusion_record_identity_conflict')
            identities[rid]=signature
            if rid in seen:continue
            seen.add(rid);rows.append(hit)
        unique[route]=rows
        if rows:
            low=min(h['score'] for h in rows);high=max(h['score'] for h in rows)
            bounds[route]={'min':low,'max':high,'constant':low==high}
            # All equally scored candidates are evidence; assign 1 instead of dropping them.
            scale=max(abs(low),abs(high),1.0)
            normalized[route]={h['record']['record_id']:(1.0 if low==high else (h['score']/scale-low/scale)/(high/scale-low/scale)) for h in rows}
        else:bounds[route]=None;normalized[route]={}
    # A missing key is a failed route. Empty successful routes retain their weight.
    weights={'bm25':1-dense_weight,'dense':dense_weight}
    if method=='weighted' and len(routes)==1:weights={r:1.0 for r in routes}
    fused={}
    for route,rows in unique.items():
        if method=='weighted' and weights[route]==0:continue
        for rank,hit in enumerate(rows,1):
            rid=hit['record']['record_id']
            item=fused.setdefault(rid,{'record':hit['record'],'score':0.0,'ranks':{},'route_scores':{},'normalized_scores':{},'fusion_contributions':{}})
            contribution=1/(rrf_k+rank) if method=='rrf' else weights[route]*normalized[route][rid]
            item['score']+=contribution;item['ranks'][route]=rank;item['route_scores'][route]=hit['score']
            item['normalized_scores'][route]=normalized[route][rid];item['fusion_contributions'][route]=contribution
    hits=sorted(fused.values(),key=lambda h:(-h['score'],h['record']['record_id']))[:top_k]
    return {'hits':hits,'fusion':{'method':method,'rrf_k':rrf_k if method=='rrf' else None,
        'dense_weight':dense_weight if method=='weighted' else None,'effective_weights':weights if method=='weighted' else None,
        'normalization':'per_query_route_min_max_constant_one','score_bounds':bounds,
        'missing_route_policy':'renormalize_available_route','tie_break':'record_id'}}
