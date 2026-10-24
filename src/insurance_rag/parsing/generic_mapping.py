"""JSON-only, product-independent table relations. No database/model/index side effects."""
import re
from collections import defaultdict
from statistics import median
from .common import stable_hash

POLICY='generic-table-relations-v1'
NUMBER=re.compile(r'[-+]?\d+(?:[,.]\d+)*(?:[%％])?\Z')
def clean(v):return re.sub(r'\s+','',v or '')
def box(c):return c.get('native_bounding_box',c.get('bounding_box',c.get('bbox')))
def scalar(text):
 s=clean(text).replace(',','').replace('，','')
 if not s:return {'kind':'blank','raw':text,'value':None}
 if NUMBER.fullmatch(s):return {'kind':'percent' if s.endswith(('%','％')) else 'number','raw':text,'value':s.rstrip('%％')}
 return {'kind':'text','raw':text,'value':None}
def grid(table):
 xs=sorted(set(round(v,2) for c in table.get('cells',[]) for v in (box(c)[0],box(c)[2]) if box(c)))
 return xs if len(xs)==table['column_count']+1 else None

def fragment_cells(fragment,parent,page_width,parent_width):
 xs=grid(parent)
 if not xs:return None
 xs=[v*page_width/parent_width for v in xs];rows=defaultdict(lambda:defaultdict(list));matched=0
 for token in fragment.get('tokens',[]):
  b=token['native_bounding_box'];x=(b[0]+b[2])/2
  col=next((i for i in range(len(xs)-1) if xs[i]-1<=x<xs[i+1]+1),None)
  if col is None:return None
  rows[token['row_index']][col].append(token);matched+=1
 if not rows or not matched:return None
 cells=[]
 for ri,columns in sorted(rows.items()):
  for col,tokens in sorted(columns.items()):
   tokens=sorted(tokens,key=lambda t:t['native_bounding_box'][0]);raw=' '.join(t['text'] for t in tokens);boxes=[t['native_bounding_box'] for t in tokens];b=[min(b[0] for b in boxes),min(b[1] for b in boxes),max(b[2] for b in boxes),max(b[3] for b in boxes)]
   cells.append({'cell_id':stable_hash([POLICY,fragment['table_id'],ri,col,tokens]),'row_index':ri,'column_index':col,'row_span':1,'column_span':1,'raw_text':raw,'native_bounding_box':b,'source_token_indices':[fragment['tokens'].index(t) for t in tokens]})
 # Row-axis progression is supporting source evidence, never an assumed product range.
 previous=[c for c in parent['cells'] if c['column_index']==0 and re.fullmatch(r'\d+',clean(c['raw_text']))]
 current=[c for c in cells if c['column_index']==0 and re.fullmatch(r'\d+',clean(c['raw_text']))]
 if not previous or not current or int(current[0]['raw_text'])<=int(previous[-1]['raw_text']):return None
 return {**fragment,'cells':cells,'column_count':parent['column_count'],'row_count':max(rows)+1,'header_rows_candidate':[],'structure_status':'recovered_fragment','continuation_of':parent['table_id']}

def structure(table,inherited=None):
 cells=table.get('cells',[]);rows=defaultdict(list)
 for c in cells:rows[c['row_index']].append(c)
 for row in rows.values():row.sort(key=lambda c:c['column_index'])
 if inherited:
  return {'data_start':0,'stub_columns':inherited['stub_columns'],'column_paths':inherited['column_paths'],'header_cells':inherited['header_cells'],'header_parents':inherited['header_parents'],'inherited_from':inherited['table_id']}
 if not cells:return {'data_start':0,'stub_columns':[],'column_paths':{},'header_cells':[],'header_parents':[]}
 first=rows[min(rows)];key_value=all(re.search(r'[:：]',c['raw_text']) for c in first)
 hints=table.get('header_rows_candidate',[]);minimum=max([c['row_index']+c.get('row_span',1) for c in first],default=1)
 if first and all(scalar(c['raw_text'])['kind'] in ('number','percent','blank') for c in first):minimum=0
 if key_value:minimum=0
 start=None
 for ri,row in sorted(rows.items()):
  if ri<minimum:continue
  if any(scalar(c['raw_text'])['kind'] in ('number','percent') for c in row):start=ri;break
 if start is None:start=minimum
 headers=[c for c in cells if c['row_index']<start];paths={}
 for col in range(table['column_count']):
  paths[str(col)]=[c['cell_id'] for c in sorted(headers,key=lambda c:(c['row_index'],c['column_index'])) if c['column_index']<=col<c['column_index']+c.get('column_span',1)]
 first_body=rows.get(start,[])
 non_numeric=[c['column_index'] for c in first_body if scalar(c['raw_text'])['kind']=='text']
 # Multiple leading dimensions are preserved; no first-column numeric-axis requirement.
 suffix=max(non_numeric)+1 if non_numeric and max(non_numeric)<table['column_count']-1 else 1
 stubs=list(range(min(suffix,table['column_count']-1))) if not key_value else []
 if not non_numeric and table['column_count']>1:stubs=[0]
 if first_body and all(scalar(c['raw_text'])['kind']=='text' for c in first_body):stubs=[0] if not key_value and table['column_count']>1 else []
 parents=[]
 for child in headers:
  candidates=[p for p in headers if p['row_index']+p.get('row_span',1)<=child['row_index'] and p['column_index']<=child['column_index'] and p['column_index']+p.get('column_span',1)>=child['column_index']+child.get('column_span',1)]
  if candidates:
   nearest=max(p['row_index']+p.get('row_span',1) for p in candidates)
   parents.extend({'child':child['cell_id'],'parent':p['cell_id']} for p in candidates if p['row_index']+p.get('row_span',1)==nearest)
 return {'data_start':start,'stub_columns':stubs,'column_paths':paths,'header_cells':headers,'header_parents':parents}

def map_layout(layout,layout_hash):
 kind=layout.get('metadata',{}).get('document_type','other')
 if kind not in ('terms','brochure','premium_rate','cash_value'):raise ValueError('unsupported_document_branch')
 branch='numeric_tables' if kind in ('premium_rate','cash_value') else kind
 pages={p['page_number']:p for p in layout.get('pages',[])};physical=sorted(layout.get('tables',[]),key=lambda t:(t['page_number'],box(t)[1] if box(t) else 0))
 models=[];records=[];issues=[];processed=[];contexts=[]
 for original in physical:
  t=original;inherited=None;continuation=None
  if original.get('structure_status')=='fragment':
   prior=[(pt,pm) for pt,pm in processed if pt['page_number']==t['page_number']-1 and pt.get('cells')]
   if prior:
    bottom=max(box(pt)[3] for pt,pm in prior);prior=[p for p in prior if box(p[0])[3]==bottom]
   candidates=[]
   for pt,pm in prior:
    recovered=fragment_cells(t,pt,pages[t['page_number']].get('width',1),pages[pt['page_number']].get('width',1))
    if recovered:candidates.append((recovered,pm))
   if len(candidates)==1:t,inherited=candidates[0];continuation=inherited['table_id']
   else:
    issues.append({'table_id':t['table_id'],'page':t['page_number'],'reason':'ambiguous_continuation' if candidates else 'fragment_grid_unresolved'})
    models.append({'table_id':t['table_id'],'status':'unresolved_fragment','token_count':len(t.get('tokens',[])),'page_number':t['page_number']});continue
  # Some headerless continuations are detected as full grids; preserve their first row.
  if inherited is None and t.get('cells') and t.get('structure_status')!='fragment':
   first_row=min(c['row_index'] for c in t['cells']);first=[c for c in t['cells'] if c['row_index']==first_row]
   if first and all(scalar(c['raw_text'])['kind'] in ('number','percent','blank') for c in first):
    prior=[(pt,pm) for pt,pm in processed if pt['page_number']==t['page_number']-1 and pt.get('cells')]
    if prior:
     pt,pm=max(prior,key=lambda pair:box(pair[0])[3]);a=grid(pt);b=grid(t)
     previous=[c for c in pt['cells'] if c['column_index']==0 and re.fullmatch(r'\d+',clean(c['raw_text']))]
     current=[c for c in first if c['column_index']==0 and re.fullmatch(r'\d+',clean(c['raw_text']))]
     if a and b and len(a)==len(b) and previous and current and int(current[0]['raw_text'])>int(previous[-1]['raw_text']) and max(abs(x-y) for x,y in zip(a,b))<max(2,median(y-x for x,y in zip(a,a[1:]))*.1):inherited=pm;continuation=pm['table_id']
  st=structure(t,inherited);model={'table_id':t['table_id'],'logical_table_id':inherited['logical_table_id'] if inherited else 'logical-table:'+stable_hash([layout_hash,t['table_id']]),'page_number':t['page_number'],'status':'relations_mapped','row_count':t['row_count'],'column_count':t['column_count'],'continuation_of':continuation,**st}
  header_ids={c['cell_id'] for c in st['header_cells']};by_id={c['cell_id']:c for c in t['cells']};by_id.update({c['cell_id']:c for c in st['header_cells']})
  groups=defaultdict(list)
  for c in t['cells']:groups[c['row_index']].append(c)
  rowheads_byrow=defaultdict(list)
  for h in t['cells']:
   if h['column_index'] in st['stub_columns'] and h['row_index']>=st['data_start']:
    for ri in range(h['row_index'],h['row_index']+h.get('row_span',1)):rowheads_byrow[ri].append(h)
  context_by_id={x['context_id']:x for x in ([*inherited.get('context_candidates',[])] if inherited else [])+t.get('context_candidates',[]) if x.get('context_id')}
  table_context=list(context_by_id.values());refs=list(context_by_id)
  table_records=0;dispositions={}
  for c in t['cells']:
   if c['row_index']<st['data_start']:dispositions[c['cell_id']]='header';continue
   if c['column_index'] in st['stub_columns']:dispositions[c['cell_id']]='row_dimension';continue
   ri=c['row_index'];ci=c['column_index'];rowheads=rowheads_byrow.get(ri,[])
   colheads=st['column_paths'].get(str(ci),[]);value=scalar(c['raw_text'])
   records.append({'table_id':t['table_id'],'logical_table_id':model['logical_table_id'],'source_cell_id':c['cell_id'],'row_index':ri,'column_index':ci,'row_header_ids':[h['cell_id'] for h in rowheads],'column_header_ids':colheads,'row_dimensions':[{'label_refs':st['column_paths'].get(str(h['column_index']),[]),'source_cell_id':h['cell_id'],'raw':h['raw_text']} for h in rowheads],'column_path':[by_id[h]['raw_text'] for h in colheads],'context_refs':refs,'value':value,'source':{'document_version_id':layout.get('document_version_id'),'layout_sha256':layout_hash,'page_number':t['page_number'],'bbox':box(c),'token_indices':c.get('source_token_indices')},'business_status':'downstream_not_adapted'})
   dispositions[c['cell_id']]='blank' if value['kind']=='blank' else 'value';table_records+=1
  model['coverage']={'cells_total':len(t['cells']),'cells_disposed':len(dispositions),'records':table_records,'dispositions':dispositions}
  model['context_candidates']=table_context
  model['relation_method']='physical_spans_and_scalar_layout'
  model['semantic_resolution']={'business_fields':'deferred','units':'raw_context_candidates_only','header_roles':'structure_inferred'}
  model['source_table_ref']={'layout_sha256':layout_hash,'table_id':original['table_id']}
  if inherited:model['continuation_evidence']=['adjacent_pages','aligned_column_grid','increasing_source_axis']
  if inherited:model['derived_cells']=t['cells']
  # Parent row/column paths are shared; per-value references resolve them without copying text.
  models.append(model);processed.append((t,model))
 return {'policy':POLICY,'document_version_id':layout.get('document_version_id'),'layout_sha256':layout_hash,'document_branch':branch,'tables':models,'issues':issues,'status':'partial' if issues else 'mapped','record_count':len(records),'business_integration':'deferred','side_effects':['local_artifacts_only']},records
