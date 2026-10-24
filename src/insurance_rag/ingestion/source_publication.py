"""Current-package publication: identity/result references and existing visibility controls only."""
from .source_package import load_package,read_bound
from .store import owner_key,get_asset
from .library import publish
from insurance_rag.agent.store import Forbidden
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import read_json,stable_hash


def prepare_source_bundle(app,owner,package_ref,*,index_ref=None,table_ref=None,rules_ref=None,examples_ref=None,execution_ref=None):
 package=load_package(app.root,package_ref);identity=package['identity']
 if identity.get('space_id')!=owner_key(owner):raise Forbidden('only_owner_can_publish_product')
 # Authority derives from owned uploads, never caller-supplied owner text in a file.
 for doc in package['source_documents']:
  if not doc.get('asset_id'):raise Forbidden('owned_source_asset_required')
  asset=get_asset(app,owner,doc['asset_id'])
  if asset['content']['sha256']!=doc['source_sha256'] or asset['content']['path']!=doc['source_file']:raise ValueError('publication_source_asset_mismatch')
 capabilities={'document_retrieval':{'status':'not_prepared'},'source_tables':{'status':'not_imported'},'formula_execution':{'status':'not_prepared'},'illustration':{'status':'not_prepared'}}
 references={};retrieval=None;tables=None
 if examples_ref:
  from .source_examples import validate_examples
  examples=validate_examples(app.root,package_ref,examples_ref);references['examples']=examples_ref
  capabilities['source_examples']={'status':'available' if examples['cases'] else 'not_applicable','case_count':len(examples['cases']),'scope':'literal_context_only'}
 if rules_ref:
  from .source_rules import validate_rules
  rules=validate_rules(app.root,package_ref,rules_ref)
  if examples_ref and rules['examples_ref']!=examples_ref:raise ValueError('rule_example_source_mismatch')
  if not examples_ref:
   from .source_examples import validate_examples
   examples=validate_examples(app.root,package_ref,rules['examples_ref']);references['examples']=rules['examples_ref']
   capabilities['source_examples']={'status':'available' if examples['cases'] else 'not_applicable','case_count':len(examples['cases']),'scope':'literal_context_only'}
  references['rules']=rules_ref
  capabilities['rule_extraction']={'status':'prepared','target_count':rules['target_count'],'rule_count':sum('rule' in r for r in rules['rules']),'methods':{method:sum(r.get('rule',{}).get('type')==method for r in rules['rules']) for method in ('formula','exact_table_lookup','illustration_reference')},'scope':'acquisition_guidance_only'}
  capabilities['formula_extraction']={'status':'prepared','target_count':rules['target_count'],'passed_count':sum(r['state']=='deterministic_checks_passed' for r in rules['rules']),'scope':'deterministic_checks_only'}
 if table_ref:
  from .source_table_store import integrity
  tables=read_json(read_bound(app.root,table_ref))
  if tables.get('scope')!='literal_source_tables_import' or tables.get('source_package_ref')!=package_ref or tables.get('source_package_id')!=package['package_id'] or tables.get('integrity')!='matched':raise ValueError('publication_table_source_mismatch')
  if app.store.postgres:
   row=app.store.db.execute('SELECT manifest FROM insurance_business.literal_batch WHERE batch_id=%s',(tables['table_batch_id'],)).fetchone()
   if not row or row[0].get('source_package_ref')!=package_ref or row[0].get('prepared_ref')!=tables.get('prepared_ref'):raise ValueError('publication_table_batch_mismatch')
   if any(row[0].get(k)!=tables.get(k) for k in ('table_count','cell_count','record_count','contract')):raise ValueError('publication_table_summary_mismatch')
   integrity(app.store.db,tables['table_batch_id'])
  references['tables']=table_ref;capabilities['source_tables']={'status':tables['state'],'table_batch_id':tables['table_batch_id'],'table_count':tables['table_count'],'cell_count':tables['cell_count'],'record_count':tables['record_count'],'scope':'literal_structure_only'}
 if index_ref is not None:
  result=read_json(read_bound(app.root,index_ref))
  if result.get('scope')!='current_source_index' or result.get('source_package_ref')!=package_ref or result.get('source_package_id')!=package['package_id']:raise ValueError('publication_result_source_mismatch')
  references['index']=index_ref;retrieval=result
  if result['state']=='ready' and (not result.get('index') or result.get('integrity')!='matched' or not result.get('embedding_binding')):raise ValueError('publication_index_reference_invalid')
  read_bound(app.root,result['chunks_ref'])
  capabilities['document_retrieval']={'status':'available' if result['state']=='ready' else result['state'],'chunk_count':result['chunk_count']}
 if execution_ref:
  from .source_execution import POLICY as EXECUTION_POLICY
  execution=read_json(read_bound(app.root,execution_ref))
  if execution.get('scope')!='current_source_execution' or execution.get('source_package_ref')!=package_ref or execution.get('source_package_id')!=package['package_id'] or execution.get('rules_ref')!=rules_ref or execution.get('versions',{}).get('policy')!=EXECUTION_POLICY:raise ValueError('publication_execution_source_mismatch')
  references['execution']=execution_ref
 bid='product-bundle:'+stable_hash(['current-source-business-v2',owner_key(owner),package_ref,references]);content={'scope':'current_source_business','bundle_id':bid,'identity':identity,'documents':package['source_documents'],'source_package_id':package['package_id'],'source_package_ref':package_ref,'result_refs':references,'retrieval':retrieval,'capabilities':capabilities}
 if tables:content['tables']=tables
 store=IntakeStore(app.store)
 try:
  prior=store.get('product_bundle',bid,owner)
  if prior['content']!=content:raise ValueError('current_source_bundle_changed')
  return prior
 except Forbidden:return store.create('product_bundle',bid,owner,identity['product_version_id'],content,'prepared')


def publish_source(app,owner,bundle_id,enabled=True):
 if type(enabled) is not bool:raise ValueError('invalid_publication_flag')
 store=IntakeStore(app.store);row=store.get('product_bundle',bundle_id,owner)
 if row['content'].get('scope')!='current_source_business':raise ValueError('current_source_business_bundle_required')
 if enabled and (row['content'].get('numeric') or {}).get('scope')=='current_source_numeric_import':raise ValueError('retired_semantic_numeric_results_not_accepted')
 package=load_package(app.root,row['content']['source_package_ref'])
 if package['package_id']!=row['content']['source_package_id']:raise ValueError('publication_package_changed')
 for ref in row['content']['result_refs'].values():read_bound(app.root,ref)
 return publish(app,owner,bundle_id,enabled)
