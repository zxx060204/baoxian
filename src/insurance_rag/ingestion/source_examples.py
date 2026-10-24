"""Current-package illustration columns and literal spatial case context. No semantic inference."""
from collections import defaultdict
from pathlib import Path
import re
from .source_package import load_package,read_bound,reference
from .source_tables import source_documents
from insurance_rag.parsing.common import stable_hash,read_json,write_json

POLICY='current-source-examples-v1'
AXIS=re.compile(r'^(?:保单年度|保单年数|保单年度数|保险年度)$')
DIMENSION=re.compile(r'^(?:保单年度|保单年数|保单年度数|保险年度|年龄|投保年龄|被保险人年龄|被保险人年度末年龄|年度末年龄|性别)$')
def compact(text):return re.sub(r'\s+','',text)

def build_examples(root,package):
 cases=[];targets=[];unresolved=[]
 for document,tables,cells in source_documents(root,package,document_types={'brochure'}):
  if document['document_type']!='brochure':continue
  by_table=defaultdict(list)
  for c in cells:by_table[c['table_id']].append(c)
  groups=defaultdict(list)
  for t in tables:groups[t.get('logical_table_id') or t['table_id']].append(t)
  for logical,parts in groups.items():
   parts.sort(key=lambda t:(t['page_number'],(t.get('bbox') or [0,0])[1]));first=parts[0];headers=first.get('header_cells',[]);header_by={c['cell_id']:c for c in headers}
   paths={str(i):[header_by[c]['raw_text'] for c in first.get('column_paths',{}).get(str(i),[]) if c in header_by] for i in range(first['column_count'])}
   # General source evidence: a policy-year axis plus actual mapped body values.
   annual=any(any(AXIS.fullmatch(compact(p)) for p in path) for path in paths.values())
   if not annual:
    unresolved.append({'document_version_id':document['document_version_id'],'table_id':first['table_id'],'reason':'not_confirmed_annual_illustration','retained_in_source_tables':True});continue
   if not any(c.get('mapping',{}).get('value',{}).get('kind')=='number' for t in parts for c in by_table[t['table_id']]):continue
   cid='source-case:'+stable_hash([package['package_id'],document['document_version_id'],logical]);columns=[]
   for i,path in paths.items():
    title=' / '.join(path);leaf=path[-1] if path else '';role='dimension' if DIMENSION.fullmatch(compact(leaf)) else 'formula_target' if path else 'unresolved_header'
    target='column_'+stable_hash([document['document_version_id'],logical,int(i),path])[:24]
    column={'column_id':target,'index':int(i),'title':title,'header_path':path,'header_ids':first.get('column_paths',{}).get(i,[]),'role':role}
    columns.append(column)
    if role=='formula_target':targets.append({'target_id':target,'case_id':cid,'document_version_id':document['document_version_id'],'logical_table_id':logical,'column_index':int(i),'title':title,'header_path':path,'header_ids':column['header_ids']})
   part_ids={t['table_id'] for t in parts};context_ids={r['context_id'] for t in parts for r in t.get('context_relations',[])}
   contexts=[c for c in document['contexts'] if c['context_id'] in context_ids or c['document_position'] in ('opening','unlocated')]
   neighbors=[]
   for t in parts:
    a=t.get('bbox')
    if not a:continue
    same=[s for s in tables if s['table_id'] not in part_ids and s.get('bbox') and s['page_number']==t['page_number'] and min(a[2],s['bbox'][2])>max(a[0],s['bbox'][0])]
    before=[s for s in same if s['bbox'][3]<=a[1]];after=[s for s in same if s['bbox'][1]>=a[3]]
    for position,neighbor in [('before',max(before,key=lambda s:s['bbox'][3],default=None)),('after',min(after,key=lambda s:s['bbox'][1],default=None))]:
     if neighbor is None:continue
     related=by_table[neighbor['table_id']];limit=80
     neighbors.append({'relative_to_table_id':t['table_id'],'position':position,'association':'spatial_only_not_business_applicability','table':neighbor,'cells':related if len(related)<=limit else [],'cells_included':len(related)<=limit,'read_ref':{'document_id':document['document_version_id'],'table_id':neighbor['table_id']}})
   cases.append({'case_id':cid,'document_version_id':document['document_version_id'],'logical_table_id':logical,'source':document['source'],'columns':columns,'tables':parts,'contexts':contexts,'nearby_tables':neighbors,'association':'spatial_only_not_business_applicability','personalization':'source_example_only','condition_interpretation':'literal_context_only','status':'partial' if any(t['status']!='relations_mapped' for t in parts) or any(c['role']=='unresolved_header' for c in columns) else 'source_structure_preserved'})
 return {'scope':'current_source_examples','policy':POLICY,'source_package_id':package['package_id'],'cases':cases,'targets':targets,'unconfirmed_tables':unresolved,'model_calls':0,'business_interpretation':False}

def prepare_examples(root,package_ref):
 root=Path(root);package=load_package(root,package_ref);folder=root/'data/source_examples'/stable_hash([POLICY,package_ref]);folder.mkdir(parents=True,exist_ok=True);path=folder/'result.json'
 if path.exists():
  ref=reference(root,path);validate_examples(root,package_ref,ref);return {**read_json(path),'result_ref':ref}
 result={**build_examples(root,package),'source_package_ref':package_ref};write_json(path,result);return {**result,'result_ref':reference(root,path)}

def validate_examples(root,package_ref,result_ref):
 root=Path(root);package=load_package(root,package_ref);result=read_json(read_bound(root,result_ref))
 if result.get('scope')!='current_source_examples' or result.get('policy')!=POLICY or result.get('source_package_ref')!=package_ref or result.get('source_package_id')!=package['package_id']:raise ValueError('current_example_source_mismatch')
 if result!={**build_examples(root,package),'source_package_ref':package_ref}:raise ValueError('current_examples_changed')
 return result

def read_example(root,package_ref,result_ref,case_id,*,table_id=None,row_start=0,row_count=20,column_start=0,column_count=20):
 if any(type(v)is not int or v<0 for v in (row_start,column_start)) or any(type(v)is not int or not 1<=v<=200 for v in (row_count,column_count)) or row_count*column_count>4000:raise ValueError('invalid_example_window')
 result=validate_examples(root,package_ref,result_ref);case=next((c for c in result['cases'] if c['case_id']==case_id),None)
 if case is None:raise ValueError('case_not_in_current_source')
 selected=next((t for t in case['tables'] if t['table_id']==(table_id or case['tables'][0]['table_id'])),None)
 if selected is None:raise ValueError('table_not_in_case')
 package=load_package(root,package_ref);window=[]
 for doc,tables,cells in source_documents(root,package,document_types={'brochure'}):
  if doc['document_version_id']!=case['document_version_id']:continue
  window=[c for c in cells if c['table_id']==selected['table_id'] and c['row_index']<row_start+row_count and c['row_index']+c['row_span']>row_start and c['column_index']<column_start+column_count and c['column_index']+c['column_span']>column_start]
 return {**case,'selected_table_id':selected['table_id'],'cells':window,'window':{'row_start':row_start,'row_count':row_count,'column_start':column_start,'column_count':column_count},'has_more_rows':row_start+row_count<selected['row_count'],'has_more_columns':column_start+column_count<selected['column_count'],'other_table_ids':[t['table_id'] for t in case['tables'] if t['table_id']!=selected['table_id']]}
