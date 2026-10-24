import tempfile,unittest
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from insurance_rag.agent.store import AgentStore,Forbidden
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.ingestion.store import asset_folder,register_asset,owner_key
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.ingestion.source_package import PREPARATION_POLICY
from insurance_rag.parsing.common import file_hash,write_json
from test_generic_source_mapping import layout,table,cell

class CurrentSourceAPITests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=AgentStore.sqlite(self.root/'db');self.owner='alice';self.app=SimpleNamespace(root=self.root,store=self.store,model=Mock(binding={'provider':'test'}))
  folder=asset_folder(self.root,'alice');folder.mkdir(parents=True);pdf=folder/'s.pdf';pdf.write_bytes(b'%PDF-1.7 test');asset=register_asset(self.app,'alice',pdf)
  t=table([cell(0,0,'被保险人投保年龄'),cell(0,1,'被保险人男性'),cell(1,0,'35'),cell(1,1,'100')],2,2);t['context_candidates']=[{'context_id':'purpose','raw_text':'本表现金价值表','position':'above','page_number':1},{'context_id':'unit','raw_text':'本表单位：元','position':'above','page_number':1}];write_json(self.root/'layout.json',layout([t]))
  doc={'document_type':'cash_value','document_version_id':'d','asset_id':asset['id'],'source_file':pdf.relative_to(self.root).as_posix(),'source_sha256':file_hash(pdf),'layout_file':'layout.json','layout_sha256':file_hash(self.root/'layout.json'),'pages':1}
  mapped=prepare_mapping(self.app,'alice',{'identity':{'product_id':'p','product_version_id':'v','product_name':'new','space_id':owner_key('alice')},'documents':[doc]},self.root)
  IntakeStore(self.store).create('preparation_job','job','alice','v',{'preparation_policy':PREPARATION_POLICY,'result':{'mapping':mapped}},'ready')
  @contextmanager
  def application():yield self.app
  api=FastAPI()
  @api.exception_handler(Forbidden)
  async def forbidden(request,exc):return JSONResponse({'detail':'unavailable'},status_code=404)
  @api.exception_handler(ValueError)
  async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=422)
  from insurance_rag.api.product_ingestion import install
  install(api,application,lambda:self.owner,start_workers=False,library_admin=True);self.client=TestClient(api)
 def tearDown(self):self.client.close();self.store.close();self.tmp.cleanup()
 def test_prepare_and_index_are_explicit_separate_actions(self):
  with patch('insurance_rag.ingestion.source_index.build_source_index',return_value={'state':'ready'}) as index:
   result=self.client.post('/v1/product-ingestion/jobs/job/table-preparation',json={});self.assertEqual(result.status_code,200);self.assertEqual(result.json()['record_count'],1);index.assert_not_called()
   contexts=self.client.post('/v1/product-ingestion/jobs/job/table-contexts',json={'result_ref':result.json()['result_ref']});self.assertEqual(contexts.status_code,200);self.assertEqual(len(contexts.json()['tables']),1)
   indexed=self.client.post('/v1/product-ingestion/jobs/job/search-index',json={});self.assertEqual(indexed.status_code,200);index.assert_called_once()
 def test_register_publish_read_and_cancel_without_preparing_other_modules(self):
  registered=self.client.post('/v1/product-ingestion/jobs/job/business-bundle',json={});self.assertEqual(registered.status_code,200);bid=registered.json()['bundle_id']
  detail=self.client.get('/v1/product-ingestion/bundles/'+bid);self.assertEqual(detail.status_code,200);self.assertEqual(detail.json()['scope'],'current_source_business');self.assertEqual(detail.json()['capabilities']['document_retrieval']['status'],'not_prepared')
  rules=self.client.get('/v1/product-ingestion/bundles/'+bid+'/rules');self.assertEqual(rules.status_code,200);self.assertNotIn('profile_id',rules.json());self.assertEqual(rules.json()['execution_readiness'],'not_evaluated')
  for enabled in (True,False):
   response=self.client.put('/v1/product-ingestion/bundles/'+bid+'/publication',json={'published':enabled});self.assertEqual(response.status_code,200);self.assertEqual(len(self.client.get('/v1/product-library').json()['products']),1 if enabled else 0)
 def test_cross_owner_action_cannot_use_current_source_job(self):
  self.owner='bob';response=self.client.post('/v1/product-ingestion/jobs/job/table-preparation',json={});self.assertEqual(response.status_code,404);self.app.model.structured.assert_not_called()

 def test_old_semantic_actions_are_removed_and_never_call_model(self):
  for action in ('numeric-preparation','numeric-contexts','numeric-import','formula-preparation','formula-results'):
   body={} if action=='numeric-preparation' else {'result_ref':{'path':'unused','sha256':'a'*64}}
   response=self.client.post('/v1/product-ingestion/jobs/job/'+action,json=body);self.assertEqual(response.status_code,404)
  self.app.model.structured.assert_not_called()
  self.owner='bob';self.assertEqual(self.client.post('/v1/product-ingestion/jobs/job/numeric-preparation',json={}).status_code,404)

 def test_examples_rules_are_explicit_and_readable_without_execution_or_publication(self):
  with patch('insurance_rag.ingestion.source_index.build_source_index',side_effect=AssertionError('implicit index')),patch('insurance_rag.ingestion.source_publication.publish_source',side_effect=AssertionError('implicit publish')):
   examples=self.client.post('/v1/product-ingestion/jobs/job/example-preparation',json={});self.assertEqual(examples.status_code,200)
   rules=self.client.post('/v1/product-ingestion/jobs/job/rule-preparation',json={});self.assertEqual(rules.status_code,200);self.assertEqual(rules.json()['target_count'],0)
   self.app.model.structured.assert_not_called()
   refs={'rules_ref':rules.json()['result_ref'],'examples_ref':examples.json()['result_ref']}
   read=self.client.post('/v1/product-ingestion/jobs/job/rule-results',json={'result_ref':refs['rules_ref']});self.assertEqual(read.status_code,200)
   registered=self.client.post('/v1/product-ingestion/jobs/job/business-bundle',json=refs);self.assertEqual(registered.status_code,200);bid=registered.json()['bundle_id']
   with patch('insurance_rag.ingestion.source_examples.prepare_examples',side_effect=AssertionError('implicit example generation')):
    rules_only=self.client.post('/v1/product-ingestion/jobs/job/business-bundle',json={'rules_ref':refs['rules_ref']});self.assertEqual(rules_only.status_code,200)
   detail=self.client.get('/v1/product-ingestion/bundles/'+bid);self.assertEqual(detail.json()['extraction']['coverage']['status'],'source_results_prepared');self.assertEqual(detail.json()['result_refs']['examples'],refs['examples_ref'])
   self.assertEqual(self.client.get('/v1/product-ingestion/bundles/'+bid+'/rules').status_code,200)
   self.assertEqual(self.client.get('/v1/product-ingestion/bundles/'+bid+'/illustration-examples').status_code,200)
   self.assertEqual(self.client.get('/v1/product-library').json()['products'],[])
   self.assertEqual(registered.json()['capabilities']['rule_extraction']['rule_count'],0)
   self.owner='bob'
   for action,body in [('example-preparation',{}),('rule-preparation',{}),('rule-results',{'result_ref':refs['rules_ref']})]:
    self.assertEqual(self.client.post('/v1/product-ingestion/jobs/job/'+action,json=body).status_code,404)

if __name__=='__main__':unittest.main()
