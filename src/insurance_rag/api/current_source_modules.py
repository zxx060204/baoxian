"""Owner-scoped, explicit current-source actions for the product workbench."""
from fastapi import Depends
from pydantic import BaseModel,ConfigDict,Field
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.ingestion.source_package import PREPARATION_POLICY,load_package

class Reference(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 path:str=Field(min_length=1,max_length=1024)
 sha256:str=Field(pattern=r'^[a-f0-9]{64}$')
class PreparedInput(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 result_ref:Reference
class RuleInput(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 index_ref:Reference|None=None

class ExampleReadInput(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 result_ref:Reference
 case_id:str
 table_id:str|None=None
 row_start:int=Field(default=0,ge=0)
 row_count:int=Field(default=20,ge=1,le=200)
 column_start:int=Field(default=0,ge=0)
 column_count:int=Field(default=20,ge=1,le=200)

class BundleInput(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 execution_ref:Reference|None=None
 rules_ref:Reference|None=None
 examples_ref:Reference|None=None
 table_ref:Reference|None=None
 index_ref:Reference|None=None
class TableReadInput(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 document_id:str=Field(min_length=1,max_length=200)
 table_id:str=Field(min_length=1,max_length=200)
 row_start:int=Field(default=0,ge=0)
 row_count:int=Field(default=20,ge=1,le=200)
 column_start:int=Field(default=0,ge=0)
 column_count:int=Field(default=20,ge=1,le=200)

def current_package(app,owner,job_id):
 job=IntakeStore(app.store).get('preparation_job',job_id,owner)
 if job['content'].get('preparation_policy')!=PREPARATION_POLICY or job['state'] not in ('ready','partial'):raise ValueError('current_source_preparation_required')
 ref=job['content']['result']['mapping']['package_ref'];load_package(app.root,ref);return ref


def install(api,application,principal):
 @api.post('/v1/product-ingestion/jobs/{id}/example-preparation')
 def example_preparation(id:str,owner=Depends(principal)):
  from insurance_rag.ingestion.source_examples import prepare_examples
  with application() as app:return prepare_examples(app.root,current_package(app,owner,id))
 @api.post('/v1/product-ingestion/jobs/{id}/example-read')
 def example_read(id:str,body:ExampleReadInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_examples import read_example
  with application() as app:
   args=body.model_dump();ref=args.pop('result_ref');return read_example(app.root,current_package(app,owner,id),ref,**args)
 @api.post('/v1/product-ingestion/jobs/{id}/rule-preparation')
 def rule_preparation(id:str,body:RuleInput=RuleInput(),owner=Depends(principal)):
  from insurance_rag.ingestion.source_rules import prepare_rules
  with application() as app:return prepare_rules(app,current_package(app,owner,id),index_ref=body.index_ref.model_dump() if body.index_ref else None)
 @api.post('/v1/product-ingestion/jobs/{id}/rule-results')
 def rule_results(id:str,body:PreparedInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_rules import validate_rules
  with application() as app:return validate_rules(app.root,current_package(app,owner,id),body.result_ref.model_dump())
 @api.post('/v1/product-ingestion/jobs/{id}/table-preparation')
 def table_preparation(id:str,owner=Depends(principal)):
  from insurance_rag.ingestion.source_tables import prepare_tables
  with application() as app:return prepare_tables(app.root,current_package(app,owner,id))
 @api.post('/v1/product-ingestion/jobs/{id}/table-contexts')
 def table_contexts(id:str,body:PreparedInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_tables import validate_tables
  with application() as app:
   _,_,docs,tables,_=validate_tables(app.root,current_package(app,owner,id),body.result_ref.model_dump())
   return {'documents':docs,'tables':tables,'association':'spatial_only_not_business_applicability'}
 @api.post('/v1/product-ingestion/jobs/{id}/table-import')
 def table_import(id:str,body:PreparedInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_table_store import import_tables
  with application() as app:return import_tables(app.store.db,app.root,current_package(app,owner,id),body.result_ref.model_dump())
 @api.post('/v1/product-ingestion/jobs/{id}/search-index')
 def search_index(id:str,owner=Depends(principal)):
  from insurance_rag.ingestion.source_index import build_source_index
  with application() as app:return build_source_index(app,current_package(app,owner,id))
 @api.post('/v1/product-ingestion/jobs/{id}/execution-preparation')
 def execution_preparation(id:str,body:BundleInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_tables import prepare_tables
  from insurance_rag.ingestion.source_execution import prepare_execution
  with application() as app:
   ref=current_package(app,owner,id);tables=prepare_tables(app.root,ref)
   return prepare_execution(app,ref,rules_ref=body.rules_ref.model_dump() if body.rules_ref else None,tables_ref=tables['result_ref'])
 @api.post('/v1/product-ingestion/jobs/{id}/business-bundle')
 def business_bundle(id:str,body:BundleInput,owner=Depends(principal)):
  from insurance_rag.ingestion.source_publication import prepare_source_bundle
  with application() as app:
   row=prepare_source_bundle(app,owner,current_package(app,owner,id),table_ref=body.table_ref.model_dump() if body.table_ref else None,index_ref=body.index_ref.model_dump() if body.index_ref else None,execution_ref=body.execution_ref.model_dump() if body.execution_ref else None,rules_ref=body.rules_ref.model_dump() if body.rules_ref else None,examples_ref=body.examples_ref.model_dump() if body.examples_ref else None)
   from insurance_rag.ingestion.readiness import current_capabilities
   return {'bundle_id':row['id'],'state':row['state'],'capabilities':current_capabilities(app,row['content'])}
 @api.get('/v1/product-ingestion/bundles/{id}/tables')
 def tables(id:str,text:str='',offset:int=0,limit:int=30,owner=Depends(principal)):
  from insurance_rag.ingestion.store import bundle
  from insurance_rag.ingestion.source_table_store import list_tables
  with application() as app:
   row=bundle(app,owner,id);binding=row['content'].get('tables')
   return list_tables(app.store.db,binding['table_batch_id'],text=text,offset=offset,limit=limit) if binding else {'status':'not_imported','tables':[]}
 @api.post('/v1/product-ingestion/bundles/{id}/table-read')
 def table_read(id:str,body:TableReadInput,owner=Depends(principal)):
  from insurance_rag.ingestion.store import bundle
  from insurance_rag.ingestion.source_table_store import read_table
  with application() as app:
   row=bundle(app,owner,id);binding=row['content'].get('tables')
   return read_table(app.store.db,binding['table_batch_id'],**body.model_dump()) if binding else {'status':'not_imported','cells':[]}
