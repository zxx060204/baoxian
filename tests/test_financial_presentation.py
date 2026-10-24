import copy,unittest
from insurance_rag.agent.presentation import display_result
class FinancialPresentationTests(unittest.TestCase):
 def value(self,text,semantic='base_amount',name='基本保险金额'):
  return {'text':text,'explanation':text,'benefit_table':{'id':'verified','columns':[{'id':'b','name':name,'unit':'CNY','semantic_key':'current_source:base_contract:'+semantic}],'rows':[{'year':1,'values':{'b':'1343'}}],'_audit':{'inputs':{'entry_age':60}}}}
 def test_stock_amount_annual_flow_explanation_omitted_without_changing_verified_data(self):
  value=self.value('查得基本保险金额：第1至第5保单年度均为1343元/年（精确值见表）。');before=copy.deepcopy(value);out=display_result(value)
  self.assertNotIn('元/年',out['display_text']);self.assertNotIn('explanation',out);self.assertIn('model_stock_unit_explanation_omitted',out['warnings']);self.assertEqual(out['benefit_table'],before['benefit_table']);self.assertEqual(value,before)
 def test_legitimate_annual_premium_explanation_is_preserved(self):
  text='基本保险金额由年交保费10000元/年换算，结果为1343元。';out=display_result(self.value(text));self.assertEqual(out['text'],text);self.assertNotIn('warnings',out)
 def test_explicit_negation_of_annual_payment_is_preserved(self):
  text='基本保险金额不是每年领取1343元/年。';out=display_result(self.value(text));self.assertEqual(out['text'],text);self.assertNotIn('warnings',out)
 def test_verified_cash_value_is_a_stock_amount(self):
  out=display_result(self.value('基本合同现金价值为2051元/年。','cash_value','基本合同现金价值'));self.assertIn('model_stock_unit_explanation_omitted',out['warnings'])
 def test_no_verified_stock_column_does_not_guess_the_unit(self):
  text='年度保险费10000元/年。';out=display_result(self.value(text,'premium','年度保险费'));self.assertEqual(out['text'],text);self.assertNotIn('warnings',out)
