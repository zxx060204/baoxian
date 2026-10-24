"""Transactional literal table storage and bounded coordinate reads."""
import json
from pathlib import Path
from .source_tables import validate_tables,CONTRACT
from .source_package import reference
from insurance_rag.parsing.common import stable_hash,write_json

SCHEMA='''CREATE SCHEMA IF NOT EXISTS insurance_business;
CREATE TABLE IF NOT EXISTS insurance_business.literal_batch(batch_id text PRIMARY KEY, manifest jsonb NOT NULL);
CREATE TABLE IF NOT EXISTS insurance_business.literal_document(batch_id text REFERENCES insurance_business.literal_batch, document_id text, content jsonb NOT NULL, PRIMARY KEY(batch_id,document_id));
CREATE TABLE IF NOT EXISTS insurance_business.literal_table(batch_id text, document_id text, table_id text, content jsonb NOT NULL, PRIMARY KEY(batch_id,document_id,table_id), FOREIGN KEY(batch_id,document_id) REFERENCES insurance_business.literal_document);
CREATE TABLE IF NOT EXISTS insurance_business.literal_cell(batch_id text, document_id text, table_id text, cell_id text, row_index integer NOT NULL, column_index integer NOT NULL, row_span integer NOT NULL, column_span integer NOT NULL, content jsonb NOT NULL, PRIMARY KEY(batch_id,document_id,table_id,cell_id), FOREIGN KEY(batch_id,document_id,table_id) REFERENCES insurance_business.literal_table);
CREATE INDEX IF NOT EXISTS literal_cell_window ON insurance_business.literal_cell(batch_id,document_id,table_id,row_index,column_index);'''


def integrity(db,bid):
 row=db.execute('SELECT manifest FROM insurance_business.literal_batch WHERE batch_id=%s',(bid,)).fetchone()
 if not row:raise ValueError('literal_batch_missing')
 m=row[0];docs=[r[0] for r in db.execute('SELECT content FROM insurance_business.literal_document WHERE batch_id=%s ORDER BY document_id',(bid,)).fetchall()];tables=[r[0] for r in db.execute('SELECT content FROM insurance_business.literal_table WHERE batch_id=%s ORDER BY document_id,table_id',(bid,)).fetchall()];hashes=[];record_count=0
 with db.transaction():
  with db.cursor(name='literal_integrity') as cursor:
   cursor.itersize=500;cursor.execute('SELECT document_id,table_id,cell_id,row_index,column_index,row_span,column_span,content FROM insurance_business.literal_cell WHERE batch_id=%s ORDER BY document_id,table_id,cell_id',(bid,))
   for did,tid,cid,ri,ci,rs,cs,c in cursor:
    if (did,tid,cid,ri,ci,rs,cs)!=(c['document_version_id'],c['table_id'],c['cell_id'],c['row_index'],c['column_index'],c['row_span'],c['column_span']):raise ValueError('literal_cell_projection_changed')
    hashes.append([did,tid,cid,stable_hash(c)]);record_count+=int('mapping' in c)
 digest=stable_hash([docs,tables,hashes])
 if digest!=m['database_content_hash'] or len(docs)!=m['document_count'] or len(tables)!=m['table_count'] or len(hashes)!=m['cell_count'] or record_count!=m['record_count']:raise ValueError('literal_database_content_changed')
 return {'integrity':'matched','table_count':len(tables),'cell_count':len(hashes),'record_count':record_count}


def import_tables(db,root,package_ref,result_ref):
 package,r,docs,tables,cells=validate_tables(root,package_ref,result_ref)
 manifest={'contract':CONTRACT,'source_package_ref':package_ref,'source_package_id':package['package_id'],'product_version_id':package['identity']['product_version_id'],'prepared_ref':result_ref,**{k:r[k] for k in ('document_count','table_count','cell_count','record_count','content_hash')}}
 manifest['database_content_hash']=stable_hash([sorted(docs,key=lambda d:d['document_version_id']),sorted(tables,key=lambda t:(t['document_version_id'],t['table_id'])),sorted([[c['document_version_id'],c['table_id'],c['cell_id'],stable_hash(c)] for c in cells])]);bid=stable_hash(manifest)
 with db.transaction():
  db.execute(SCHEMA);db.execute('SELECT pg_advisory_xact_lock(hashtext(%s))',('literal-tables:'+bid,))
  existing=db.execute('SELECT manifest FROM insurance_business.literal_batch WHERE batch_id=%s',(bid,)).fetchone()
  if existing:
   if existing[0]!=manifest:raise ValueError('literal_batch_conflict')
  else:
   db.execute('INSERT INTO insurance_business.literal_batch VALUES (%s,%s::jsonb)',(bid,json.dumps(manifest)))
   for doc in docs:db.execute('INSERT INTO insurance_business.literal_document VALUES (%s,%s,%s::jsonb)',(bid,doc['document_version_id'],json.dumps(doc,ensure_ascii=False)))
   for t in tables:db.execute('INSERT INTO insurance_business.literal_table VALUES (%s,%s,%s,%s::jsonb)',(bid,t['document_version_id'],t['table_id'],json.dumps(t,ensure_ascii=False)))
   with db.cursor().copy('COPY insurance_business.literal_cell FROM STDIN') as writer:
    for c in cells:writer.write_row((bid,c['document_version_id'],c['table_id'],c['cell_id'],c['row_index'],c['column_index'],c['row_span'],c['column_span'],json.dumps(c,ensure_ascii=False)))
  check=integrity(db,bid)
 result={'scope':'literal_source_tables_import','contract':CONTRACT,'state':r['state'],'source_package_ref':package_ref,'source_package_id':package['package_id'],'prepared_ref':result_ref,'table_batch_id':bid,**check};path=Path(root)/'data/literal_table_imports'/(bid+'.json');write_json(path,result);return {**result,'result_ref':reference(Path(root),path)}


def list_tables(db,bid,*,text='',offset=0,limit=30):
 if type(offset) is not int or offset<0 or type(limit) is not int or not 1<=limit<=100:raise ValueError('invalid_table_page')
 rows=db.execute("SELECT content FROM insurance_business.literal_table WHERE batch_id=%s AND (%s='' OR strpos(content::text,%s)>0) ORDER BY document_id,(content->>'page_number')::int,table_id OFFSET %s LIMIT %s",(bid,text,text,offset,limit+1)).fetchall();items=[]
 for (t,) in rows[:limit]:
  item={k:t[k] for k in ('document_version_id','document_type','table_id','page_number','row_count','column_count','status','context_preview','source_file','bbox','record_count','cell_count')}
  item['header_preview']=[{k:h[k] for k in ('cell_id','row_index','column_index','row_span','column_span','raw_text') if k in h} for h in t.get('header_cells',[])[:12]];item['headers_truncated']=len(t.get('header_cells',[]))>12;items.append(item)
 return {'status':'matched' if items else 'not_found','tables':items,'offset':offset,'next_offset':offset+limit if len(rows)>limit else None,'scope':'literal_structure_only'}


def read_table(db,bid,document_id,table_id,*,row_start=0,row_count=20,column_start=0,column_count=20):
 if any(type(v) is not int or v<0 for v in (row_start,column_start)) or any(type(v) is not int or v<1 or v>200 for v in (row_count,column_count)) or row_count*column_count>4000:raise ValueError('invalid_table_window')
 row=db.execute('SELECT content FROM insurance_business.literal_table WHERE batch_id=%s AND document_id=%s AND table_id=%s',(bid,document_id,table_id)).fetchone()
 if not row:return {'status':'not_found','cells':[]}
 table=row[0];doc=db.execute('SELECT content FROM insurance_business.literal_document WHERE batch_id=%s AND document_id=%s',(bid,document_id)).fetchone()[0];ids={r['context_id'] for r in table['context_relations']}
 contexts=[c for c in doc['contexts'] if c['context_id'] in ids or c['document_position'] in ('opening','unlocated')]
 rows=db.execute('SELECT content FROM insurance_business.literal_cell WHERE batch_id=%s AND document_id=%s AND table_id=%s AND row_index<%s AND row_index+row_span>%s AND column_index<%s AND column_index+column_span>%s ORDER BY row_index,column_index,cell_id',(bid,document_id,table_id,row_start+row_count,row_start,column_start+column_count,column_start)).fetchall()
 return {'status':'matched','table':table,'source':doc['source'],'contexts':contexts,'cells':[r[0] for r in rows],'window':{'row_start':row_start,'row_count':row_count,'column_start':column_start,'column_count':column_count},'has_more_rows':row_start+row_count<table['row_count'],'has_more_columns':column_start+column_count<table['column_count'],'scope':'literal_source_not_business_interpretation'}
