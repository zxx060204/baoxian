"""Prepare immutable source relations; all business actions are explicit consumers."""
from pathlib import Path
import json
from insurance_rag.parsing.common import read_json,write_json,file_hash,workspace_path
from insurance_rag.parsing.generic_mapping import map_layout,POLICY
from .source_package import SCHEMA,TEXT_POLICY,policies,package_id,reference,read_bound,check_layout,load_package

SOURCE_FIELDS=('document_type','document_id','document_version_id','product_version_id','company_id','source_file','source_sha256','layout_file','layout_sha256','pages','asset_id','parse_report_file')

def prepare_mapping(app,owner,content,folder,*,force=False):
 documents=[{k:d[k] for k in SOURCE_FIELDS if k in d} for d in content['documents']];identity=content.get('identity',{})
 pid=package_id(documents,identity);output=Path(app.root)/'data/source_packages'/pid.split(':',1)[1];manifest=output/'source_package.json'
 if manifest.exists():
  ref=reference(app.root,manifest);saved=load_package(app.root,ref)
  return {**saved,'package_ref':ref}
 output.mkdir(parents=True,exist_ok=True);reports=[];total=0
 for d in documents:
  read_bound(app.root,{'path':d['source_file'],'sha256':d['source_sha256']},'mapping_source_changed');lp=read_bound(app.root,{'path':d['layout_file'],'sha256':d['layout_sha256']},'mapping_source_changed')
  layout=read_json(lp);check_layout(layout,d)
  model,records=map_layout(layout,d['layout_sha256']);prefix=d['document_version_id'];modelpath=output/(prefix+'.model.json');recordpath=output/(prefix+'.records.jsonl');textpath=output/(prefix+'.text.json')
  with recordpath.open('w',encoding='utf8') as stream:
   for record in records:stream.write(json.dumps(record,ensure_ascii=False)+'\n')
  model['records_ref']=reference(app.root,recordpath);write_json(modelpath,model)
  write_json(textpath,{'policy':TEXT_POLICY,'document_version_id':d['document_version_id'],'layout_sha256':d['layout_sha256'],'blocks':[b for b in layout.get('blocks',[]) if b.get('block_type') not in ('image','table') and b.get('raw_text','').strip()] if d['document_type'] in ('terms','brochure') else [],'indexing':'not_requested'})
  reports.append({'document_type':d['document_type'],'document_version_id':d['document_version_id'],'state':model['status'],'model_ref':reference(app.root,modelpath),'text_ref':reference(app.root,textpath),'record_count':len(records),'issues':model['issues']})
  total+=len(records)
 report={'schema_version':SCHEMA,'scope':'source_relations','policies':policies(),'package_id':pid,'identity':identity,'source_documents':documents,'structure_status':'partial' if any(d['state']=='partial' for d in reports) else 'mapped','mode':'generic_json_relations','documents':reports,'record_count':total,'side_effects':['local_artifacts_only']}
 write_json(manifest,report);ref=reference(app.root,manifest);load_package(app.root,ref)
 return {**report,'package_ref':ref}

