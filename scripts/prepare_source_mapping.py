"""Offline parser/mapper entry point. No DB, model, index or publication calls."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from types import SimpleNamespace
from insurance_rag.parsing.common import read_json,write_json,file_hash,workspace_path
from insurance_rag.parsing.table_contract import enrich,POLICY as TABLE_POLICY
from insurance_rag.parsing.models import PARSER_VERSION
from insurance_rag.parsing.table_evidence import partial_report
from insurance_rag.ingestion.mapping import prepare_mapping

def prepare(root,manifest,output,reparse=False):
 root=Path(root).resolve();output=workspace_path(output,root);output.mkdir(parents=True,exist_ok=True);body=read_json(workspace_path(manifest,root,must_exist=True));products=body if isinstance(body,list) else [body];results=[]
 for i,product in enumerate(products):
  folder=output/str(i);folder.mkdir(parents=True,exist_ok=True);documents=[]
  for original in product['documents']:
   source=workspace_path(original['source_file'],root,must_exist=True);lp=workspace_path(original['layout_file'],root,must_exist=True)
   if file_hash(source)!=original['source_sha256'] or file_hash(lp)!=original['layout_sha256']:raise ValueError('input_source_changed')
   layout=read_json(lp)
   if reparse:
    from insurance_rag.parsing.native import extract_document
    from insurance_rag.parsing.models import ParseConfig
    native=extract_document(source,ParseConfig(ocr_mode='auto',extract_tables=True),original['source_sha256'],document_type=original['document_type'])
    native['parser_version']=PARSER_VERSION
    native.update({k:v for k,v in layout.items() if k in ('metadata','schema_version','document_id','document_version_id')});layout=native
   layout,_=enrich(layout,source,original['source_sha256'],original['document_type']);target=folder/(original['source_sha256']+'.layout.json');write_json(target,layout)
   report=folder/(original['source_sha256']+'.parse_report.json');write_json(report,{'document_version_id':original['document_version_id'],'layout_file':target.relative_to(root).as_posix(),'layout_sha256':file_hash(target),'partial_tables':partial_report(layout),'parser_version':layout.get('parser_version'),'table_preparation_policy':TABLE_POLICY,'execution_mode':'offline_native_reparse' if reparse else 'existing_layout_enrichment'})
   documents.append({**original,'layout_file':target.relative_to(root).as_posix(),'layout_sha256':file_hash(target),'parse_report_file':report.relative_to(root).as_posix()})
  content={'bundle_id':'offline-source-mapping:'+str(i),'documents':documents,'identity':product.get('identity',{})}
  result=prepare_mapping(SimpleNamespace(root=root),None,content,folder)
  results.append({'documents':documents,'mapping':result});write_json(output/'results.json',results)
  print(json.dumps({'document_group':i,'structure':result['structure_status'],'records':result['record_count'],'issues':sum(len(d['issues']) for d in result['documents'])},ensure_ascii=False),flush=True)
 return results
if __name__=='__main__':
 parser=argparse.ArgumentParser();parser.add_argument('--manifest',required=True);parser.add_argument('--output',required=True);parser.add_argument('--reparse',action='store_true');args=parser.parse_args();prepare(Path(__file__).resolve().parents[1],args.manifest,args.output,args.reparse)
