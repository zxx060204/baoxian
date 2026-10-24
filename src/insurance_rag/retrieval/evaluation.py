"""Graded retrieval metrics; pending query drafts never count as acceptance."""
import math
from insurance_rag.parsing.audit import reviewed
from .gold import evidence_coverage

def metrics(ranked,relevant,k=10):
    ranked=list(dict.fromkeys(ranked))[:k]
    positives={r for r,g in relevant.items() if g>0}
    if not positives:return {'recall':None,'mrr':None,'ndcg':None,'empty_correct':not ranked}
    recall=len(set(ranked)&positives)/len(positives)
    mrr=next((1/(i+1) for i,r in enumerate(ranked) if r in positives),0)
    dcg=sum((2**relevant.get(r,0)-1)/math.log2(i+2) for i,r in enumerate(ranked))
    ideal=sum((2**g-1)/math.log2(i+2) for i,g in enumerate(sorted(relevant.values(),reverse=True)[:k]))
    return {'recall':recall,'mrr':mrr,'ndcg':dcg/ideal if ideal else 0,'empty_correct':None}

def evaluate(cases,search,*,k=10,context_builder=None):
    results=[];pending=0
    for case in cases:
        if not reviewed(case.get('audit')):pending+=1;continue
        relevant=case.get('relevant',{})
        if case.get('expected_empty'):
            if relevant:raise ValueError('empty_query_has_relevance_labels')
        elif not any(g>0 for g in relevant.values()):raise ValueError('positive_query_missing_relevance')
        if any(isinstance(g,bool) or g not in (0,1,2) for g in relevant.values()):raise ValueError('invalid_relevance_grade')
        hits=search(case['query'],case.get('scope','chunk'),case.get('filters',{}),k)
        coverage=evidence_coverage([h['record'] for h in hits],case.get('required_evidence',[]))
        expanded=None;omitted=None
        if context_builder and case.get('scope','chunk')=='chunk':
            context=context_builder(hits)
            evidence_records=[{'document_version_id':item['document_version_id'],'source_spans':item['source_spans']} for items in context['groups'].values() for item in items]
            expanded=evidence_coverage(evidence_records,case.get('required_evidence',[]));omitted=len(context['omitted'])
        expected=case.get('expected_product_versions')
        results.append({'expanded_evidence_coverage':expanded,'context_omitted_clauses':omitted,'evidence_coverage':coverage,'evidence_complete':coverage==1 if coverage is not None else None,
                        'wrong_version_count':sum(h['record'].get('product_version_id') not in expected for h in hits) if expected else None,
                        'query_id':case['query_id'],'scope':case.get('scope','chunk'),
                        **metrics([h['record']['record_id'] for h in hits],relevant,k)})
    groups={}
    for scope in ('product','chunk'):
        selected=[r for r in results if r['scope']==scope and r['recall'] is not None]
        groups[scope]={n:sum(r[n] for r in selected)/len(selected) if selected else None for n in ('recall','mrr','ndcg')}
        groups[scope]['positive_queries']=len(selected)
        evidence=[r['evidence_coverage'] for r in selected if r['evidence_coverage'] is not None]
        groups[scope]['evidence_coverage']=sum(evidence)/len(evidence) if evidence else None
        groups[scope]['complete_evidence_rate']=sum(v==1 for v in evidence)/len(evidence) if evidence else None
        expanded=[r['expanded_evidence_coverage'] for r in selected if r['expanded_evidence_coverage'] is not None]
        groups[scope]['expanded_evidence_coverage']=sum(expanded)/len(expanded) if expanded else None
        groups[scope]['expanded_complete_evidence_rate']=sum(v==1 for v in expanded)/len(expanded) if expanded else None
    return {'status':'evaluated_reviewed_queries' if results else 'awaiting_query_review','k':k,
        'negative_queries':sum(r['empty_correct'] is not None for r in results),
        'empty_correct_rate':sum(r['empty_correct'] for r in results if r['empty_correct'] is not None)/sum(r['empty_correct'] is not None for r in results) if any(r['empty_correct'] is not None for r in results) else None,
        'reviewed_queries':len(results),'pending_queries':pending,'groups':groups,'results':results}
