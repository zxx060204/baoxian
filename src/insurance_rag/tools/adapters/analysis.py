"""Analysis continuation and evidence-bound qualitative candidate recommendations."""
from typing import Literal
from pydantic import BaseModel, ConfigDict, Field, model_validator
from insurance_rag.tools.contracts import result
from insurance_rag.tools.registry import ToolSpec
from insurance_rag.agent.store import Forbidden
from insurance_rag.agent.context import product_sources


class Input(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Analysis(Input):
    analysis_id: str = Field(min_length=1, max_length=200)


class Candidate(Input):
    product_version_id: str | None = Field(default=None, min_length=1, max_length=200)
    bundle_id: str | None = Field(default=None, min_length=1, max_length=200)
    reason: str = Field(min_length=1, max_length=1200)
    result_refs: list[str] = Field(min_length=1, max_length=8)
    disposition: Literal['preferred','alternative','excluded','insufficient_information'] = 'alternative'

    @model_validator(mode='after')
    def one_source(self):
        if bool(self.product_version_id)==bool(self.bundle_id):raise ValueError('one_candidate_source_required')
        return self


class Recommendation(Input):
    candidates: list[Candidate] = Field(min_length=1, max_length=5)
    priority_quote: str = Field(min_length=1, max_length=1000)


def read_analysis(args, c):
    store = c['app'].workflow_store
    row = store.read_result(args['analysis_id'], c['owner'])
    run = c['app'].store.get(row['run_id'], c['owner'])
    content = row['content']
    c['app'].validate_calculation_result(content, run['release'])
    from insurance_rag.ingestion.service import validate_analysis
    for analysis in content.get('document_analyses',[]) or ([content['document_analysis']] if content.get('document_analysis') else []):
        validate_analysis(c['app'],c['owner'],analysis)
    # Historical analysis remains a historical result, never a new product publication.
    return result('ok', data={'analysis_record': {**row, 'release_id': run['release']['release_id'],
        'historical': True, 'reuse_policy': 'recheck_versions_and_conditions_before_new_calculation'}})


def recommend(args, c):
    profile = c['app'].profile.snapshot(c['owner'], c['run']['thread_id'])
    relevant = {k:v for k,v in profile['values'].items() if k != 'response_preference'}
    texts = [m['content'] for m in c['app'].journal.messages(c['run']) if m['role'] == 'user']
    if not any(args['priority_quote'] in t for t in texts) and not any(
            args['priority_quote'] in f['quote'] for f in profile['facts']):
        return result('conflict', reason_code='recommendation_priority_requires_user_quote')
    # The current, attributed priority is sufficient for a temporary recommendation; no profile write is implied.
    relevant={**relevant,'goal':args['priority_quote']}
    if profile['conflicts']:
        return result('needs_clarification', reason_code='recommendation_profile_conflict',
                      data={'profile': profile}, missing_fields=[x['field'] for x in profile['conflicts']])
    candidates, evidence, numeric = [], {}, []
    allowed = set(c['state'].get('products', [])) | set(c['state'].get('resolved_products', []))
    seen = set()
    for item in args['candidates']:
        bid=item.get('bundle_id')
        if bid:
            from insurance_rag.ingestion.store import bundle
            private=bundle(c['app'],c['owner'],bid)['content']['identity']
            pv=private['product_version_id']
            product={**private,'product_name':private['product_name']}
        else:
            pv=item['product_version_id']
            if pv not in c['tools'].versions or pv not in allowed:raise Forbidden('recommendation_candidate_not_selected')
            product=c['tools'].versions[pv]
        if pv in seen:
            return result('conflict', reason_code='duplicate_recommendation_candidate')
        seen.add(pv)
        for ref in item['result_refs']:
            packet = c['app'].packet(ref, c)
            if packet['status'] not in ('ok', 'partial'):
                return result('conflict', reason_code='recommendation_reference_unavailable')
            # Each rationale must cite a result that actually contains this product.
            def contains(value):
                if isinstance(value, dict):
                    return value.get('product_version_id') == pv or bool(bid and value.get('bundle_id')==bid) or any(contains(v) for v in value.values())
                if isinstance(value, list):
                    return any(contains(v) or v == pv for v in value)
                return False
            if not contains(packet.get('data', {})) and not any(e.get('product_version_id') == pv for e in packet.get('evidence', [])):
                compatible=[]
                for candidate_ref,entry in c['state'].get('refs',{}).items():
                    if entry.get('revision')!=c['state'].get('conditions_revision'):continue
                    sources=entry.get('products',[])
                    if any((source.get('bundle_id')==bid if bid else source.get('product_version_id')==pv) for source in sources):compatible.append(candidate_ref)
                return result('conflict', reason_code='recommendation_reference_product_mismatch',data={
                    'expected_product':{'bundle_id':bid,'product_version_id':pv},'rejected_ref':ref,
                    'referenced_products':product_sources(packet.get('data',{})),
                    'compatible_result_refs':compatible,
                    'instruction':'该引用不属于此候选，不能沿用或重复提交。选用compatible_result_refs中对应此产品的已验证结果；历史比较可用read_analysis返回的当前data_ref。证据不足保留insufficient_information，不移用另一产品金额。'})
            for e in packet.get('evidence', []):
                evidence[e['evidence_id']] = e
            numeric += packet.get('numeric_sources', [])
        age_range = product.get('age_range') or {}
        age = relevant.get('age')
        excluded = age is not None and age_range.get('min') is not None and age_range.get('max') is not None and not age_range['min'] <= age <= age_range['max']
        candidates.append({**item, 'product_version_id':pv, 'product_name': product['product_name'],
                           'disposition': 'excluded' if excluded else item['disposition'],
                           'eligibility': 'outside_declared_age_range' if excluded else 'not_fully_verified',
                           'rank': None})
    # Order is the Agent's explained qualitative judgment, not an invented mathematical score.
    rank = 0
    for item in candidates:
        if item['disposition'] in ('preferred', 'alternative'):
            rank += 1
            item['rank'] = rank
    return {**result('partial', data={'recommendation': {
        'candidates': candidates, 'profile': relevant, 'priority_quote': args['priority_quote'],
        'basis': 'agent_qualitative_judgment_over_referenced_results', 'priority_scope':'current_request',
        'unknowns': ['投保资格以完整产品条件为准；排序不代表核保结论。',
                     '缺失数据只影响相关判断，不按零或通过处理。']}},
        evidence=list(evidence.values())), 'numeric_sources': numeric}


def specs():
    return [ToolSpec('read_analysis', '按analysis_id读取本人历史结构化分析；历史结果不能当当前条件下的新报价。', Analysis, read_analysis),
            ToolSpec('recommend_products', '根据已有证据及本次用户明确目标整理候选，不要求先写画像。继续历史比较先read_analysis，沿用其已选bundle；每项产品传bundle_id或product_version_id二选一，引用对应当前data_ref。证据不足可选insufficient_information且不排名；未知资格保留未知，不保存画像或文件。', Recommendation, recommend, version='2')]
