"""Explicit current-source actions. Each invocation performs only its named preparation/write."""
import argparse
from pathlib import Path
from insurance_rag.parsing.common import read_json,write_json


def main():
 p=argparse.ArgumentParser()
 p.add_argument('action',choices=['prepare-tables','import-tables','index','prepare-examples','prepare-rules'])
 p.add_argument('--packages',required=True,help='JSON containing packages (package_ref, optional index_ref)')
 p.add_argument('--output',required=True);p.add_argument('--prepared');args=p.parse_args()
 root=Path(__file__).resolve().parents[1];items=read_json(root/args.packages)['packages'];out=root/args.output;out.mkdir(parents=True,exist_ok=True);results=[]
 def save(result):
  results.append(result);write_json(out/'results.json',{'results':results})
 if args.action in ('prepare-tables','prepare-examples'):
  if args.action=='prepare-tables':
   from insurance_rag.ingestion.source_tables import prepare_tables as prepare
  else:
   from insurance_rag.ingestion.source_examples import prepare_examples as prepare
  for item in items:
   r=prepare(root,item['package_ref']);save(r);print(args.action,r['result_ref']['path'],flush=True)
 else:
  from insurance_rag.services.bootstrap import open_services
  with open_services() as app:
   if args.action=='import-tables':
    from insurance_rag.ingestion.source_table_store import import_tables
    if not args.prepared:p.error('--prepared required')
    prepared=read_json(root/args.prepared)['results']
    for item in items:
     r=next(r for r in prepared if r['source_package_id']==item['package_id']);save(import_tables(app.store.db,root,item['package_ref'],r['result_ref']));print('imported',results[-1]['table_batch_id'],flush=True)
   elif args.action=='index':
    from insurance_rag.ingestion.source_index import build_source_index
    for item in items:
     save(build_source_index(app,item['package_ref']));print('indexed',results[-1]['chunk_count'],flush=True)
   else:
    from insurance_rag.ingestion.source_rules import prepare_rules
    for item in items:
     save(prepare_rules(app,item['package_ref'],index_ref=item.get('index_ref')));print('rules',results[-1]['target_count'],results[-1]['model_calls'],results[-1]['rag_calls'],flush=True)


if __name__=='__main__':main()
