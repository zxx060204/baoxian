"""Machine draft assessment; never modifies human audit or publication flags."""
import re
from collections import Counter

def assess_drafts(bundle):
 results=[];families=Counter()
 for q in bundle['queries']:
  family=(q['scope'],tuple(sorted(q.get('filters',{}).items())),tuple(sorted(q.get('relevant',{}))))
  families[family]+=1;reasons=[]
  if q.get('expected_empty'):
   decision='replace_for_quality_set';reasons=['nonexistent_version_is_filter_contract_test_not_semantic_hard_negative']
  elif q['scope']=='chunk':
   evidence=q.get('source_evidence',[])
   if any(not e.get('block_id') or not e.get('source_character_range') for e in evidence):reasons.append('exact_source_range_missing')
   if any(e.get('candidate_labels','').startswith('page_projection') for e in evidence):reasons.append('same_page_chunks_are_not_confirmed_positive_labels')
   if any('如何规定' in q['query'] for _ in [0]):reasons.append('broad_question_requires_full_clause_and_dependencies')
   decision='revise' if reasons else 'candidate_for_machine_source_check'
  else:
   decision='keep_as_product_discovery_draft';reasons=['product_identity_requires_separate_review']
  if families[family]>1:reasons.append('near_duplicate_label_family')
  results.append({'query_id':q['query_id'],'query':q['query'],'decision':decision,'reasons':reasons,'human_audit_changed':False})
 return {'status':'machine_assessed','human_acceptance':False,'counts':dict(Counter(r['decision'] for r in results)), 'results':results,'scope':'source suitability and duplication assessment, not confirmed human gold'}
