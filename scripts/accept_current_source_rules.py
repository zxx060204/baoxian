"""Run the current rule extractor against explicit source-package regression samples."""
import argparse,json,sys
from pathlib import Path
from types import SimpleNamespace
from dataclasses import replace
from collections import Counter
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.parsing.common import read_json,write_json
from insurance_rag.ingestion.source_package import reference,load_package
from insurance_rag.ingestion.source_rules import prepare_rules
from insurance_rag.runtime_env import load_env
from insurance_rag.agent.models import DeepSeekChat
from insurance_rag.services.config import ChatSettings

if __name__=='__main__':
 parser=argparse.ArgumentParser()
 parser.add_argument('--products',required=True,help='Explicit current source-package regression manifest')
 parser.add_argument('--output',default='data/current_source_rule_acceptance')
 args=parser.parse_args();root=Path(__file__).resolve().parents[1]
 load_env(root/'.env')
 settings=replace(ChatSettings.from_environment(),retries=0,timeout_seconds=120,max_output_tokens=6144)
 app=SimpleNamespace(root=root,model=DeepSeekChat(settings));out=root/args.output;out.mkdir(parents=True,exist_ok=True);reports=[]
 for item in read_json(root/args.products)['products']:
  load_package(root,item['package_ref']);indexes=[]
  for p in (root/'data/current_source_indexes').glob('*/result.json'):
   index=read_json(p)
   if index.get('source_package_ref')==item['package_ref'] and index.get('state')=='ready':indexes.append(reference(root,p))
  result=prepare_rules(app,item['package_ref'],index_ref=indexes[0] if indexes else None)
  reports.append({'product':item['name'],'package_ref':item['package_ref'],'result_ref':result['result_ref'],'targets':result['target_count'],'states':dict(Counter(r['state'] for r in result['rules'])),'methods':dict(Counter(r.get('rule',{}).get('type',r['state']) for r in result['rules'])),'model_calls':result['model_calls'],'rag_calls':result['rag_calls'],'review_calls':result['review_calls'],'model_binding':app.model.binding,'usage':dict(app.model.usage)})
  write_json(out/'results.json',{'reports':reports})
  print(json.dumps(reports[-1],ensure_ascii=False),flush=True)
