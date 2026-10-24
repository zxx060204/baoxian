"""Rename structural identities and compare literal context grouping, without model IO."""
import argparse,copy
from pathlib import Path
from insurance_rag.parsing.common import read_json,write_json
from insurance_rag.ingestion.source_package import load_package,read_bound
from insurance_rag.ingestion.table_contexts import document_contexts


def normalized(contexts,order):
 return [{'raw_text':c['raw_text'],'page_number':c['page_number'],'bbox':c['bbox'],'position':c['document_position'],'members':[{'raw_text':m['raw_text'],'bbox':m['bbox']} for m in c['members']],'relations':[{'table':order[r['table_id']],'position':r['position']} for r in c['relations']]} for c in contexts]


def main():
 p=argparse.ArgumentParser();p.add_argument('--packages',required=True);p.add_argument('--output',required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1];reports=[]
 for item in read_json(root/a.packages)['packages']:
  pkg=load_package(root,item['package_ref'])
  for doc in pkg['source_documents']:
   entry=next(e for e in pkg['documents'] if e['document_version_id']==doc['document_version_id']);layout=read_json(root/doc['layout_file']);model=read_json(read_bound(root,entry['model_ref']));original=document_contexts(layout,model);ids={}
   def collect(x):
    if isinstance(x,list):
     for v in x:collect(v)
    elif isinstance(x,dict):
     for k,v in x.items():
      if k in ('table_id','block_id','context_id','document_version_id','logical_table_id') and isinstance(v,str):ids.setdefault(v,'replacement-'+str(len(ids)))
      collect(v)
   collect(layout);collect(model)
   def renamed(x):
    if isinstance(x,str):return ids.get(x,x)
    if isinstance(x,list):return [renamed(v) for v in x]
    if isinstance(x,dict):return {k:renamed(v) for k,v in x.items()}
    return x
   other=renamed(model);actual=document_contexts(renamed(layout),other)
   if normalized(original,{t['table_id']:i for i,t in enumerate(model['tables'])})!=normalized(actual,{t['table_id']:i for i,t in enumerate(other['tables'])}):raise ValueError('literal_context_depends_on_source_identity')
   reports.append({'document_version_id':doc['document_version_id'],'contexts':len(original),'tables':len(model['tables']),'renamed_ids':len(ids),'passed':True})
 write_json(root/a.output,{'documents':reports,'passed':True});print('literal_context_identity_passed',len(reports),'documents')


if __name__=='__main__':main()
