"""Fixed source preparation steps inside the existing durable ingestion worker."""
from insurance_rag.parsing.common import stable_hash
from .source_tables import prepare_tables
from .source_table_store import import_tables
from .source_index import build_source_index
from .source_examples import prepare_examples
from .source_rules import prepare_rules,validate_rules
from insurance_rag.parsing.common import read_json

def current_rules(app,package_ref,index_ref):
 for path in sorted((app.root/'data/source_rules').glob('*/result.json'),key=lambda p:p.stat().st_mtime,reverse=True):
  saved=read_json(path)
  if saved.get('source_package_ref')!=package_ref:continue
  try:
   from .source_package import reference
   ref=reference(app.root,path);checked=validate_rules(app.root,package_ref,ref)
   if any(r['state'] in ('invalid_output','provider_error') for r in checked['rules']):continue
   return {**checked,'result_ref':ref}
  except ValueError:continue
 return prepare_rules(app,package_ref,index_ref=index_ref)
from .source_execution import prepare_execution,POLICY as EXECUTION_POLICY,PROMPT as EXECUTION_PROMPT
from .source_publication import prepare_source_bundle
from .readiness import current_capabilities

POLICY='source-business-preparation-v5'

def complete_source(app,owner,package_ref,step):
 outputs={};errors={}
 def attempt(name,fn):
  try:
   key=name+'_'+stable_hash([POLICY,EXECUTION_POLICY if name=='execution_preparation' else None,stable_hash(EXECUTION_PROMPT) if name=='execution_preparation' else None,package_ref,{k:v.get('result_ref') for k,v in outputs.items()}])[:12]
   value=step(key,fn);outputs[name]=value;return value
  except (ValueError,RuntimeError,OSError) as exc:
   from insurance_rag.reliability.diagnostics import OperationFailure
   errors[name]=exc.diagnostic if isinstance(exc,OperationFailure) else str(exc)[:160] if isinstance(exc,ValueError) else type(exc).__name__;return None
 tables=attempt('table_preparation',lambda:prepare_tables(app.root,package_ref))
 imported=attempt('table_import',lambda:import_tables(app.store.db,app.root,package_ref,tables['result_ref'])) if tables and app.store.postgres else None
 if tables and not app.store.postgres:errors['table_import']='postgres_required'
 index=attempt('search_index',lambda:build_source_index(app,package_ref))
 examples=attempt('example_preparation',lambda:prepare_examples(app.root,package_ref))
 rules=attempt('rule_preparation',lambda:current_rules(app,package_ref,index_ref=index['result_ref'] if index and index.get('state')=='ready' else None)) if examples else None
 execution=attempt('execution_preparation',lambda:prepare_execution(app,package_ref,rules_ref=rules['result_ref'] if rules else None,tables_ref=tables['result_ref'] if tables else None))
 parent=step('bundle_registration_'+stable_hash([package_ref,{k:v.get('result_ref') for k,v in outputs.items()}])[:12],lambda:prepare_source_bundle(app,owner,package_ref,index_ref=index['result_ref'] if index else None,table_ref=imported['result_ref'] if imported else None,examples_ref=examples['result_ref'] if examples else None,rules_ref=rules['result_ref'] if rules else None,execution_ref=execution['result_ref'] if execution else None))
 capabilities=current_capabilities(app,parent['content'])
 return {'bundle_id':parent['id'],'capabilities':capabilities,'downstream':{name:{'state':value.get('state','prepared'),'result_ref':value.get('result_ref')} for name,value in outputs.items()},'errors':errors,'published':False,'execution_state':execution['state'] if execution else 'not_prepared'}
