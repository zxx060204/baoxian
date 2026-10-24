"""Literal mapped tables and spatial context, without semantic generation or review."""
import json
from pathlib import Path
from .source_package import load_package,read_bound,reference
from .table_contexts import document_contexts,POLICY as CONTEXT_POLICY,bbox
from insurance_rag.parsing.common import read_json,write_json,stable_hash

CONTRACT='literal-source-tables-v1'
POLICY='literal-source-table-preparation-v1'


def source_documents(root,package,*,document_types=None):
 docs={d['document_version_id']:d for d in package['source_documents']}
 for entry in package['documents']:
  if document_types is not None and entry['document_type'] not in document_types:continue
  doc=docs[entry['document_version_id']];model=read_json(read_bound(root,entry['model_ref']));layout=read_json(root/doc['layout_file'])
  contexts=document_contexts(layout,model)
  document={'document_version_id':doc['document_version_id'],'document_type':doc['document_type'],'source':doc,'contexts':contexts}
  physical={t['table_id']:t for t in layout['tables']};records={}
  with read_bound(root,model['records_ref']).open(encoding='utf8') as stream:
   for line in stream:
    r=json.loads(line);records.setdefault(r['table_id'],{})[r['source_cell_id']]=r
  tables=[];cells=[]
  for t in model['tables']:
   original=physical.get(t.get('source_table_ref',{}).get('table_id',t['table_id']),{})
   meta={k:t[k] for k in ('table_id','logical_table_id','page_number','status','row_count','column_count','continuation_of','data_start','stub_columns','column_paths','header_cells','header_parents','source_table_ref','coverage') if k in t}
   meta.update(document_version_id=doc['document_version_id'],document_type=doc['document_type'],source_file=doc['source_file'],source_sha256=doc['source_sha256'],layout_sha256=doc['layout_sha256'],model_ref=entry['model_ref'],bbox=bbox(original),context_relations=[{'context_id':c['context_id'],'position':r['position']} for c in contexts for r in c['relations'] if r['table_id']==t['table_id']],fragment_tokens=original.get('tokens',[]) if t['status']!='relations_mapped' else [])
   related={r['context_id'] for r in meta['context_relations']}
   meta['context_preview']=[{'context_id':c['context_id'],'raw_text':c['raw_text'][:400],'truncated':len(c['raw_text'])>400} for c in contexts if c['context_id'] in related][:4]
   candidates={c['cell_id']:(c,'physical') for c in original.get('cells',[])}
   for c in t.get('derived_cells',[]):candidates[c['cell_id']]=(c,'mapped_fragment')
   for c in t.get('header_cells',[]):
    if c['cell_id'] not in candidates:candidates[c['cell_id']]=(c,'mapped_header')
   mapped=records.get(t['table_id'],{});found=set()
   for cid,(c,origin) in candidates.items():
    content={k:c[k] for k in ('cell_id','row_index','column_index','row_span','column_span','raw_text','bounding_box','native_bounding_box','original_reference','source_block_ids') if k in c}
    content.update(document_version_id=doc['document_version_id'],table_id=t['table_id'],page_number=t['page_number'],origin=origin,row_span=c.get('row_span',1),column_span=c.get('column_span',1))
    if cid in mapped:
     r=mapped[cid];found.add(cid)
     content['mapping']={k:r[k] for k in ('row_header_ids','column_header_ids','row_dimensions','column_path','value','source') if k in r}
     content['source_record_hash']=stable_hash(r)
    cells.append(content)
   if found!=set(mapped):raise ValueError('literal_mapped_cells_missing')
   meta.update(cell_count=len(candidates),record_count=len(mapped));tables.append(meta)
  if sum(t['record_count'] for t in tables)!=entry['record_count']:raise ValueError('literal_source_record_count_mismatch')
  yield document,tables,cells


def content(root,package):
 documents=[];tables=[];cells=[]
 for doc,ts,cs in source_documents(root,package):documents.append(doc);tables.extend(ts);cells.extend(cs)
 return documents,tables,cells


def prepare_tables(root,package_ref):
 root=Path(root);package=load_package(root,package_ref);versions={'contract':CONTRACT,'policy':POLICY,'contexts':CONTEXT_POLICY}
 folder=root/'data/literal_tables'/stable_hash([package_ref,versions]);folder.mkdir(parents=True,exist_ok=True);path=folder/'result.json'
 if path.exists():
  ref=reference(root,path);validate_tables(root,package_ref,ref);return {**read_json(path),'result_ref':ref}
 docs,tables,cells=content(root,package);dp=folder/'documents.json';tp=folder/'tables.json';cp=folder/'cells.jsonl'
 write_json(dp,{'documents':docs});write_json(tp,{'tables':tables})
 with cp.open('w',encoding='utf8') as stream:
  for c in cells:stream.write(json.dumps(c,ensure_ascii=False,allow_nan=False)+'\n')
 result={'scope':'literal_source_tables','source_package_ref':package_ref,'source_package_id':package['package_id'],'versions':versions,'state':'partial' if any(t['status']!='relations_mapped' for t in tables) else 'ready' if tables else 'not_applicable','document_count':len(docs),'table_count':len(tables),'cell_count':len(cells),'record_count':sum(t['record_count'] for t in tables),'model_calls':0,'documents_ref':reference(root,dp),'tables_ref':reference(root,tp),'cells_ref':reference(root,cp),'content_hash':stable_hash([docs,tables,[[c['document_version_id'],c['table_id'],c['cell_id'],stable_hash(c)] for c in cells]])}
 write_json(path,result);return {**result,'result_ref':reference(root,path)}


def validate_tables(root,package_ref,result_ref):
 root=Path(root);package=load_package(root,package_ref);r=read_json(read_bound(root,result_ref))
 if r.get('scope')!='literal_source_tables' or r.get('source_package_ref')!=package_ref or r.get('source_package_id')!=package['package_id']:raise ValueError('literal_table_source_mismatch')
 if r.get('versions')!={'contract':CONTRACT,'policy':POLICY,'contexts':CONTEXT_POLICY}:raise ValueError('literal_table_policy_changed')
 docs,tables,cells=content(root,package)
 if read_json(read_bound(root,r['documents_ref']))!={'documents':docs} or read_json(read_bound(root,r['tables_ref']))!={'tables':tables}:raise ValueError('literal_table_metadata_changed')
 with read_bound(root,r['cells_ref']).open(encoding='utf8') as stream:
  for expected,line in zip(cells,stream,strict=True):
   if expected!=json.loads(line):raise ValueError('literal_table_cell_changed')
 expected={'document_count':len(docs),'table_count':len(tables),'cell_count':len(cells),'record_count':sum(t['record_count'] for t in tables),'model_calls':0,'state':'partial' if any(t['status']!='relations_mapped' for t in tables) else 'ready' if tables else 'not_applicable','content_hash':stable_hash([docs,tables,[[c['document_version_id'],c['table_id'],c['cell_id'],stable_hash(c)] for c in cells]])}
 if any(r.get(k)!=v for k,v in expected.items()):raise ValueError('literal_table_summary_changed')
 return package,r,docs,tables,cells
