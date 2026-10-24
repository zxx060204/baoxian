import copy,tempfile,unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from insurance_rag.agent.store import AgentStore,Forbidden,Conflict
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.illustration.schema import standard_metrics,POLICY
from insurance_rag.illustration.preparation import validate_proposal,eligible_formula_source

class IllustrationModelTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.agent=AgentStore.sqlite(self.root/'db');self.store=IntakeStore(self.agent)
  self.app=SimpleNamespace(root=self.root,store=self.agent,model=SimpleNamespace(binding='fixture',structured=lambda *a: self.fail('unexpected_model_call')))
  self.identity={'company_id':'c','product_id':'p','product_version_id':'v','product_name':'产品'}
  self.content={'bundle_id':'b','identity':self.identity,'documents':[],'capabilities':{}}
  self.profile={'policy':POLICY,'bundle_id':'b','identity':self.identity,'metrics':standard_metrics(),'preparation':{}}
  self.inputs={'entry_age':36,'payment_years':3,'annual_premium_input':'10000','end_year':5,'scenario':'normal_annual_payment_no_changes_no_loans'}
 def tearDown(self):self.agent.close();self.tmp.cleanup()
 def metric(self,code):return next(m for m in self.profile['metrics'] if m['metric_code']==code)
 def source(self):return {'source_id':'s','text':'身故保险金为累计保险费的160%。','source':{'document_version_id':'d','source_sha256':'hash','page_number':1}}
 def proposal(self):return {'formulas':[{'metric_code':'death_benefit','summary':'按比例给付','evidence':[{'source_id':'s','quote':self.source()['text']}],'expression_tree':{'type':'multiply','args':[{'type':'parameter','name':'cumulative_premium'},{'type':'constant','value':'1.6'}]},'constants':[{'value':'1.6','raw_value':'160%','conversion':'percent','evidence':{'source_id':'s','quote':'累计保险费的160%。'}}],'time_basis':'演示时点','complete':True}]}
 def test_invented_constant_rejected(self):
  p=self.proposal();p['formulas'][0]['expression_tree']['args'][1]['value']='1.7'
  with self.assertRaisesRegex(ValueError,'unproven_formula_constant'):validate_proposal(p,[self.source()],'death_benefit')
 def test_case_data_and_headers_cannot_prove_age_formula(self):
  source=self.source();source['text']='友女士35周岁，年交保费14900元。被保险人年度末年龄。'
  p={'formulas':[{'metric_code':'year_end_age','summary':'年龄相加','evidence':[{'source_id':'s','quote':source['text']}],'expression_tree':{'type':'add','args':[{'type':'parameter','name':'entry_age'},{'type':'parameter','name':'policy_year'}]},'time_basis':'年末','complete':True}]}
  self.assertFalse(eligible_formula_source(source))
  with self.assertRaisesRegex(ValueError,'age_relation_not_explicit'):validate_proposal(p,[source],'year_end_age')
 def test_incorrect_percent_conversion_rejected(self):
  p=self.proposal();p['formulas'][0]['constants'][0]['value']='160'
  with self.assertRaisesRegex(ValueError,'conversion_mismatch'):validate_proposal(p,[self.source()],'death_benefit')
 def test_partial_formula_does_not_become_executable(self):
  p=self.proposal();p['formulas'][0]['complete']=False;p['formulas'][0]['missing']=['缺少分支']
  self.assertEqual(validate_proposal(p,[self.source()],'death_benefit')['missing'],['缺少分支'])
 def test_preparation_scope_does_not_reuse_legacy_completed_job(self):
  from insurance_rag.ingestion.store import register_asset,asset_folder,create_job
  folder=asset_folder(self.root,'alice');folder.mkdir(parents=True);path=folder/'old.pdf';path.write_bytes(b'%PDF-1.4')
  asset=register_asset(self.app,'alice',path);assets=[{'asset_id':asset['id'],'document_type':'terms'}]
  self.store.create('preparation_job','legacy-job','alice','',{'assets':assets,'hints':{'product_name':'','company':'','version_label':''}},'ready')
  job=create_job(self.app,'alice','new',assets);self.assertNotEqual(job['id'],'legacy-job');from insurance_rag.ingestion.source_package import PREPARATION_POLICY
  self.assertEqual(job['content']['preparation_policy'],PREPARATION_POLICY)
 def test_retired_illustration_repreparation_endpoint_is_removed(self):
  from insurance_rag.ingestion.store import register_asset,asset_folder,create_job
  from fastapi.testclient import TestClient
  from insurance_rag.api.product_backend import create_app
  owner='local-product-administrator';folder=asset_folder(self.root,owner);folder.mkdir(parents=True);path=folder/'source.pdf';path.write_bytes(b'%PDF-1.4')
  asset=register_asset(self.app,owner,path);job=create_job(self.app,owner,'first',[{'asset_id':asset['id'],'document_type':'terms'}]);self.assertIn('sha256',job['content']['assets'][0])
  self.store.create('product_bundle','b',owner,'v',{**self.content,'job_id':job['id']},'ready')
  @contextmanager
  def factory():yield self.app
  with TestClient(create_app(factory,start_workers=False)) as client:
   client.get('/');response=client.post('/v1/product-ingestion/bundles/b/illustration-preparations',json={},headers={'Origin':'http://testserver'});self.assertEqual(response.status_code,404,response.text)
   self.assertEqual(len(self.store.list('preparation_job')),1)

if __name__=='__main__':unittest.main()
