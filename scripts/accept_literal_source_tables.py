"""Live literal storage, original-coordinate gold, transactional and retrieval checks."""
import argparse,copy,re
from pathlib import Path
from unittest.mock import patch
from pypdf import PdfReader
from insurance_rag.parsing.common import read_json,write_json,file_hash
from insurance_rag.services.bootstrap import open_services
from insurance_rag.ingestion.source_tables import prepare_tables,validate_tables
from insurance_rag.ingestion.source_table_store import import_tables,read_table,list_tables,integrity
from insurance_rag.ingestion.source_package import read_bound,reference
from insurance_rag.ingestion.source_index import validate_index_result,retrieve_source
from insurance_rag.indexing.scoped_index import providers


class InjectedFailure(RuntimeError):pass
class FailingConnection:
 def __init__(self,db):self.db=db
 def __getattr__(self,k):return getattr(self.db,k)
 def execute(self,sql,args=()):
  if sql.startswith('INSERT INTO insurance_business.literal_table'):raise InjectedFailure('literal_table_failure')
  return self.db.execute(sql,args)


def main():
 p=argparse.ArgumentParser();p.add_argument('--packages',required=True);p.add_argument('--prepared',required=True);p.add_argument('--imported',required=True);p.add_argument('--indexes',required=True);p.add_argument('--gold',required=True);p.add_argument('--output',required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1];out=root/a.output;out.mkdir(parents=True,exist_ok=True)
 items=read_json(root/a.packages)['packages'];prepared=read_json(root/a.prepared)['results'];imports=read_json(root/a.imported)['results'];indexes=read_json(root/a.indexes)['results'];gold=read_json(root/a.gold)['samples'];reports=[];checks=[];texts={};grouping=[]
 with patch('insurance_rag.agent.models.DeepSeekChat.structured',side_effect=AssertionError('literal_intake_must_not_call_llm')):
  with open_services() as app:
   publication=app.store.db.execute('SELECT id,revision,content_hash,state FROM insurance_onboarding.library_product ORDER BY id').fetchall()
   for item in items:
    r=next(r for r in prepared if r['source_package_id']==item['package_id']);imported=next(i for i in imports if i['source_package_id']==item['package_id']);bid=imported['table_batch_id'];print('checking',item['identity']['product_name'],flush=True)
    if prepare_tables(root,item['package_ref'])!=r:raise ValueError('literal_prepare_not_idempotent')
    if import_tables(app.store.db,root,item['package_ref'],r['result_ref'])!=imported:raise ValueError('literal_import_not_idempotent')
    check=integrity(app.store.db,bid);directory=list_tables(app.store.db,bid,limit=100)
    if len(directory['tables'])!=r['table_count']:raise ValueError('literal_table_directory_count_changed')
    docs=read_json(read_bound(root,r['documents_ref']))['documents']
    for doc in docs:
     for c in doc['contexts']:
      if '费率计算如' in c['raw_text'] and '下：' in c['raw_text']:
       grouping.append({'document':doc['document_version_id'],'context_id':c['context_id'],'raw_text':c['raw_text'],'member_count':len(c['members']),'relations':c['relations'],'passed':len(c['members'])>=2})
    for sample in [g for g in gold if g['package_ref']==item['package_ref']]:
     key=(sample['source_file'],sample['page'])
     if key not in texts:texts[key]=PdfReader(root/sample['source_file']).pages[sample['page']-1].extract_text()
     original=bool(re.search(sample['pdf_line'],texts[key],re.M)) if sample['pdf_line_regex'] else sample['pdf_line'] in ' '.join(texts[key].split())
     packet=read_table(app.store.db,bid,sample['document_id'],sample['table_id'],row_start=sample['row_index'],row_count=1,column_start=sample['column_index'],column_count=1);cells=[c for c in packet.get('cells',[]) if c['cell_id']==sample['source_cell_id']]
     source_doc=next(d for d in docs if d['document_version_id']==sample['document_id']);related={r['context_id'] for r in packet['table']['context_relations']};expected_contexts=[c for c in source_doc['contexts'] if c['context_id'] in related or c['document_position'] in ('opening','unlocated')]
     # A title/unit may be inside the table's header cells. Do not invent external text.
     contexts_match=packet['contexts']==expected_contexts
     passed=original and len(cells)==1 and cells[0]['raw_text']==sample['expected_raw'] and bool(packet['table']['header_cells']) and contexts_match
     checks.append({'sample':sample['sample'],'expected_raw':sample['expected_raw'],'actual_raw':cells[0]['raw_text'] if cells else None,'row':sample['row_index'],'column':sample['column_index'],'table_id':sample['table_id'],'original_pdf_line_present':original,'external_context_count':len(packet['contexts']),'expected_external_context_count':len(expected_contexts),'contexts_match':contexts_match,'header_count':len(packet['table']['header_cells']),'passed':bool(passed)})
    if read_table(app.store.db,bid,'not-in-package','not-in-package')['status']!='not_found':raise ValueError('literal_unknown_table_exposed')
    indexed=next(i for i in indexes if i['source_package_id']==item['package_id']);client,_,_=providers(app);validate_index_result(root,item['package_ref'],indexed['result_ref'],client=client);retrieval=retrieve_source(app,item['package_ref'],indexed['result_ref'],'保险责任',top_k=2)
    if retrieval['status']!='matched' or retrieval['degraded']:raise ValueError('literal_source_retrieval_failed')
    reports.append({'product':item['identity']['product_name'],'prepared_ref':r['result_ref'],'import_ref':imported['result_ref'],'table_batch_id':bid,**check,'directory_count':len(directory['tables']),'prepare_idempotent':True,'import_idempotent':True,'model_calls':r['model_calls'],'retrieval':{'status':retrieval['status'],'routes':retrieval['routes'],'degraded':retrieval['degraded']}})
    write_json(out/'progress.json',{'reports':reports,'gold':checks})
   smallest=min(imports,key=lambda i:i['cell_count']);r=next(r for r in prepared if r['source_package_id']==smallest['source_package_id']);copy=out/'rollback-prepared-copy.json';copy.write_bytes(read_bound(root,r['result_ref']).read_bytes());copy_ref=reference(root,copy);before=app.store.db.execute('SELECT count(*) FROM insurance_business.literal_batch').fetchone()[0]
   try:import_tables(FailingConnection(app.store.db),root,smallest['source_package_ref'],copy_ref)
   except InjectedFailure:pass
   else:raise ValueError('literal_rollback_not_exercised')
   if app.store.db.execute('SELECT count(*) FROM insurance_business.literal_batch').fetchone()[0]!=before:raise ValueError('literal_failed_batch_not_rolled_back')
   after=app.store.db.execute('SELECT id,revision,content_hash,state FROM insurance_onboarding.library_product ORDER BY id').fetchall()
   if after!=publication:raise ValueError('literal_acceptance_changed_publication')
 result={'scope':'literal_coordinate_acceptance_not_canonical_business_acceptance','gold_ref':{'path':a.gold,'sha256':file_hash(root/a.gold)},'reports':reports,'original_coordinate_gold':checks,'gold_passed':sum(c['passed'] for c in checks),'gold_total':len(gold),'context_grouping':grouping,'rollback':'passed','publication':'unchanged','passed':len(checks)==len(gold) and all(c['passed'] for c in checks) and bool(grouping) and all(c['passed'] for c in grouping)};write_json(out/'results.json',result)
 if not result['passed']:raise ValueError('literal_acceptance_failed_see_results')
 print('literal_acceptance_passed',result['gold_passed'],sum(r['record_count'] for r in reports),'records',sum(r['cell_count'] for r in reports),'cells',flush=True)


if __name__=='__main__':main()
