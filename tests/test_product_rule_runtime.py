import copy,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from insurance_rag.agent.store import AgentStore,Forbidden
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import file_hash,write_json
from insurance_rag.ingestion.store import owner_key
from insurance_rag.illustration.schema import standard_metrics,POLICY
from insurance_rag.benefits.registry import concept_for

class ProductRuleRuntimeTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.store=AgentStore.sqlite(self.root/'db');self.app=SimpleNamespace(root=self.root,store=self.store)
  self.identity={'company_id':'c','product_id':'p','product_version_id':'v','product_name':'测试产品'}
  self.profile={'policy':POLICY,'bundle_id':'b','identity':self.identity,'metrics':standard_metrics(),'preparation':{'llm_calls':0}}
  self.inputs={'entry_age':30,'payment_years':5,'annual_premium_input':'100000','end_year':3,'scenario':'normal_annual_payment_no_changes_no_loans','base_amount_input':'451950'}
 def tearDown(self):self.store.close();self.temp.cleanup()
 def content(self,text):
  pdf=self.root/'source.pdf';pdf.write_bytes(b'%PDF-1.4 fixture');layout=self.root/'layout.json';write_json(layout,{'pages':[{'page_number':1,'raw_text':text}]})
  return {'bundle_id':'b','identity':self.identity,'documents':[{'document_type':'brochure','document_version_id':'d','source_file':'source.pdf','source_sha256':file_hash(pdf),'layout_file':'layout.json','layout_sha256':file_hash(layout)}],'capabilities':{}}
 def ready(self,code,tree,deps=None):
  m=next(m for m in self.profile['metrics'] if m['metric_code']==code);m.update(status='source_verified',acquisition_mode='source_formula',execution_readiness='ready',verification={'business':'passed'},expression_tree=tree,dependencies=deps or [],source_refs=[{'quote':'原文给付公式'}])
  return m
 def test_extraction_model_keeps_charge_accounting(self):
  from insurance_rag.agent.models import DeepSeekChat
  from insurance_rag.services.config import ChatSettings
  from insurance_rag.illustration.models import extraction_model
  original=DeepSeekChat(ChatSettings.from_environment({'DEEPSEEK_API_KEY':'test-key'}));extracted=extraction_model(original);self.assertIs(original.usage,extracted.usage);extracted.usage['requests']+=1;self.assertEqual(original.usage['requests'],1)
if __name__=='__main__':unittest.main()
