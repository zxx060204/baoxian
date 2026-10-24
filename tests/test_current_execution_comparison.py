import unittest
from types import SimpleNamespace
from insurance_rag.tools.adapters.result_comparison import compare_results
class CurrentComparisonTests(unittest.TestCase):
 def compare(self,keys=True,changed=False):
  packets={}
  for idx in range(2):
   column={'id':'different-source-column-'+str(idx),'name':'基本保险金额','unit':'CNY'}
   if keys:column['semantic_key']='current_source:base_contract:base_amount'
   table={'bundle_id':str(idx),'product':str(idx),'inputs':{'annual_premium_input':'20000' if idx and changed else '10000'},'columns':[column],'rows':[{'year':1,'values':{column['id']:'9392'}}],'issues':[]}
   packets[str(idx)]={'status':'ok','data':{'benefit_table':table}}
  app=SimpleNamespace(packet=lambda ref,c:packets[ref],journal=SimpleNamespace(messages=lambda run:[{'role':'user','content':'比较基本保险金额'}]))
  return compare_results({'result_refs':['0','1'],'objective_quote':'比较基本保险金额'},{'app':app,'run':{}})['data']['product_comparison']
 def test_semantic_lookup_roles_compare_across_source_ids(self):
  value=self.compare();self.assertTrue(value['numeric_comparable']);self.assertTrue(all(c['comparable'] for c in value['cells']))
 def test_title_alone_and_changed_inputs_are_not_comparable(self):
  self.assertFalse(self.compare(keys=False)['numeric_comparable']);self.assertFalse(self.compare(changed=True)['numeric_comparable'])

class ContinuationContextTests(unittest.TestCase):
 def test_historical_summary_preserves_bundle_and_conditions(self):
  from insurance_rag.agent.context import ContextEngine
  value={'bundle_id':'product-bundle:source','product':'示例','inputs':{'annual_premium_input':'10000'},'columns':[],'_audit':{'internal':True}}
  output=ContextEngine().wire({'status':'ok','data':{'analysis_record':{'result_id':'analysis','content':{'benefit_table':value}}}},'packet-reference')
  product=output['data']['analysis_record']['products'][0]
  self.assertEqual(product['bundle_id'],'product-bundle:source');self.assertEqual(product['inputs'],value['inputs']);self.assertNotIn('_audit',product)
 def test_premium_correction_and_invalid_currency_text(self):
  from insurance_rag.ingestion.personal_inputs import extract,IllustrationInputs
  self.assertEqual(extract('把明确年交保费改为20000元')[0]['annual_premium'],'20000')
  from pydantic import ValidationError
  for amount in ('20000元','NaN','0','-1'):
   with self.assertRaises(ValidationError):IllustrationInputs(entry_age=0,payment_years=1,annual_premium_input=amount,end_year=5,scenario='normal_annual_payment_no_changes_no_loans')
