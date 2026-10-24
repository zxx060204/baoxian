"""Validate reviewed source ranges independently from retrieval rankings."""
from insurance_rag.parsing.common import stable_hash,read_json,workspace_path
from insurance_rag.parsing.audit import reviewed

def validate_query_gold(bundle,manifest,products,chunks,*,root,require_complete=False,mode='human'):
 if mode not in ('human','machine'):raise ValueError('invalid_gold_validation_mode')
 if bundle.get('snapshot_hash')!=stable_hash(manifest):raise ValueError('query_snapshot_binding_changed')
 known={r['record_id']:r for r in products+chunks};docs={d['document_version_id']:d for d in manifest['documents']};layouts={};seen=set();checked=0
 for case in bundle['queries']:
  from .query import validate,record_matches
  validate(case.get('query'),case.get('scope'),case.get('filters'),10)
  if case.get('split') not in ('development','holdout') or type(case.get('expected_empty')) is not bool:raise ValueError('invalid_query_split_or_empty_flag')
  qid=case['query_id']
  if qid in seen:raise ValueError('duplicate_query_id')
  seen.add(qid)
  for rid,grade in case.get('relevant',{}).items():
   if rid not in known or known[rid]['scope']!=case['scope']:raise ValueError('unknown_or_wrong_scope_relevance_id')
   if isinstance(grade,bool) or grade not in (0,1,2):raise ValueError('invalid_relevance_grade')
  accepted=reviewed(case.get('audit')) if mode=='human' else (case.get('machine_assessment',{}).get('status')=='accepted_for_automatic_diagnostic' and case.get('machine_assessment',{}).get('source_text_disagreements')==0 and case.get('machine_assessment',{}).get('method')=='clause-scoped candidates plus independent PDFium per-span agreement')
  if not accepted:
   if require_complete:raise ValueError('unreviewed_query_prevents_freeze')
   continue
  checked+=1;positive=[known[rid] for rid,g in case.get('relevant',{}).items() if g>0]
  if case.get('expected_empty'):
   if case.get('relevant'):raise ValueError('empty_query_has_relevance_labels')
   continue
  if not positive:raise ValueError('positive_query_missing_relevance')
  if any(not record_matches(r,case.get('filters',{}),case['scope']) for r in positive):raise ValueError('gold_positive_conflicts_with_query_filters')
  evidence=case.get('source_evidence',[])
  if not evidence:raise ValueError('reviewed_query_requires_source_evidence')
  if case['scope']=='chunk' and (case.get('answer_requirements',{}).get('complete_body_required') or ('完整文字' in case.get('query','') and case.get('question_type')=='clause_location')):
   import re
   direct=''.join(e.get('excerpt','') for e in evidence if e.get('evidence_role','direct')=='direct')
   if len(re.sub(r'\s+','',direct))<case.get('answer_requirements',{}).get('minimum_direct_characters',30):raise ValueError('clause_oracle_contains_no_body')
  case['expected_product_versions']=sorted({r['product_version_id'] for r in positive})
  required=[]
  for e in evidence:
   doc=e.get('document_version_id');d=docs.get(doc)
   if not d or any(e.get(k)!=d[k] for k in ('source_file','source_sha256','layout_sha256')):raise ValueError('gold_source_binding_mismatch')
   if case['scope']=='product':
    if not any(doc in r['document_version_ids'] for r in positive):raise ValueError('product_gold_source_not_in_relevance')
    continue
   if doc not in layouts:layouts[doc]=read_json(workspace_path(d['layout_file'],root,must_exist=True))
   blocks=[b for b in layouts[doc]['blocks'] if b['block_id']==e.get('block_id')]
   if len(blocks)!=1:raise ValueError('gold_exact_source_block_required')
   b=blocks[0];span=e.get('source_character_range')
   if not isinstance(span,list) or len(span)!=2 or any(type(v)is not int for v in span) or not 0<=span[0]<span[1]<=len(b['raw_text']):raise ValueError('invalid_gold_character_range')
   if e.get('excerpt')!=b['raw_text'][span[0]:span[1]] or e.get('page_number')!=b['page_number'] or e.get('source_bbox')!=b.get('bounding_box',b.get('bbox')):raise ValueError('gold_range_text_or_coordinate_mismatch')
   required.append({'document_version_id':doc,'block_id':b['block_id'],'source_character_range':span})
  if case['scope']=='chunk':
   for r in positive:
    if not any(r['document_version_id']==e['document_version_id'] and any(s['block_id']==e['block_id'] and max(s['source_character_range'][0],e['source_character_range'][0])<min(s['source_character_range'][1],e['source_character_range'][1]) for s in r['source_spans']) for e in required):raise ValueError('positive_chunk_does_not_overlap_reviewed_evidence')
   if evidence_coverage(positive,required)!=1:raise ValueError('reviewed_evidence_not_fully_mapped_to_positive_chunks')
   case['required_evidence']=required
 return {'reviewed_queries':checked,'pending_queries':len(seen)-checked} if mode=='human' else {'machine_eligible_queries':checked,'machine_unresolved_queries':len(seen)-checked}

def evidence_coverage(records,required):
 if not required:return None
 covered=0
 for e in required:
  intervals=[]
  for r in records:
   if r.get('document_version_id')!=e['document_version_id']:continue
   for s in r.get('source_spans',[]):
    if s['block_id']==e['block_id']:intervals.append(s['source_character_range'])
  cursor,end=e['source_character_range']
  for lo,hi in sorted(intervals):
   if lo>cursor:break
   if hi>cursor:cursor=hi
   if cursor>=end:break
  covered+=cursor>=end
 return covered/len(required)
