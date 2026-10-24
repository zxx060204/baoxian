import unittest
from unittest.mock import patch
from types import SimpleNamespace
from insurance_rag.ingestion.source_execution import execute_target,assemble,lookup,derive_base,validate_result,decimal_text
from insurance_rag.ingestion.source_execution_bindings import normalized,validate_plan,record_axes
from insurance_rag.agent.store import Conflict
from insurance_rag.parsing.common import stable_hash

class SourceExecutionTests(unittest.TestCase):
 def setUp(self):
  self.app=SimpleNamespace();self.content={'bundle_id':'new','identity':{'product_name':'source'},'result_refs':{'execution':{'path':'execution','sha256':'bound'}}}
  self.inputs={'entry_age':35,'sex':'male','payment_years':3,'annual_premium_input':'10000','end_year':3,'scenario':'normal_annual_payment_no_changes_no_loans'}
  axes={'entry_age':35,'sex':'male','payment_years':3}
  self.records=[{'role':'base_amount','component':'base','axes':axes,'basis_kind':'annual_premium','basis_amount':'10000','value':'25000','source':{'cell_id':'base'}}]+[{'role':'cash_value','component':'base','axes':{**axes,'policy_year':year},'basis_kind':'annual_premium','basis_amount':'10000','value':str(year*1000),'source':{'cell_id':'cash'+str(year)}} for year in range(1,4)]
 def target(self,kind='formula'):
  return {'id':'t','name':'个人指标','method':kind,'state':'ready','unit':'CNY','required_inputs':[],'dependencies':[],'issues':[]}
 def run_target(self,t,inputs=None,records=None,operation=None):
  with patch('insurance_rag.ingestion.source_execution.load_execution',return_value=({'targets':[t]},self.records if records is None else records)):
   return execute_target(self.app,self.content,{'rule_id':'t'},inputs or self.inputs,operation=operation)
 def test_source_lookup_and_reference_only_assembly(self):
  t={**self.target('exact_table_lookup'),'lookup_role':'cash_value'}
  packet=self.run_target(t)['data']['table_values'];self.assertEqual(packet['exact_fractions'],{'1':'1000','2':'2000','3':'3000'})
  self.assertEqual(packet['sources']['2'][0]['cell_id'],'cash2')
  with patch('insurance_rag.ingestion.source_execution.load_execution',return_value=({'targets':[t]},[])):
   answer=assemble(self.app,self.content,{'value_refs':['verified']},[packet])
  self.assertEqual(answer['status'],'ok');self.assertEqual(answer['data']['benefit_table']['rows'][2]['values']['t'],'3000')
 def test_lookup_does_not_interpolate_or_choose_duplicate(self):
  with self.assertRaisesRegex(ValueError,'no_exact'):lookup(self.records,'cash_value',{**self.inputs,'entry_age':36},1)
  with self.assertRaisesRegex(ValueError,'ambiguous'):lookup(self.records+[self.records[1]],'cash_value',self.inputs,1)
 def test_sum_assured_basis_uses_exact_premium_inverse(self):
  records=[{'role':'premium','component':'base','axes':{'entry_age':35,'sex':'male','payment_years':3},'basis_kind':'sum_assured','basis_amount':'1000','value':'300','source':{'cell_id':'rate'}}]
  amount,src=derive_base(records,self.inputs,1);self.assertEqual(str(amount),'100000/3');self.assertIsNone(decimal_text(amount));self.assertEqual(src[0]['cell_id'],'rate')
 def test_non_guaranteed_reference_never_computes(self):
  t={**self.target(),'state':'reference_only'};self.assertEqual(self.run_target(t)['reason_code'],'source_target_not_executable')
 def test_bound_input_formula_and_change_conditions(self):
  t=self.target();t['formula']={'expression_tree':{'type':'multiply','args':[{'type':'parameter','name':'base'},{'type':'constant','value':'0.1'}]},'variables':[{'name':'base','unit':'CNY'}]};t['bindings']=[{'name':'base','kind':'table','key':'base_amount'}]
  value=self.run_target(t)['data']['table_values'];self.assertEqual(value['exact_fractions']['1'],'2500')
  changed=self.run_target(t,{**self.inputs,'annual_premium_input':'20000'})['data']['table_values'];self.assertEqual(changed['exact_fractions']['1'],'5000')
 def test_prior_year_uses_lazy_branch_and_current_source(self):
  t=self.target();t['formula']={'variables':[{'name':'year','unit':'dimensionless'},{'name':'base','unit':'CNY'},{'name':'previous','unit':'CNY'}],'expression_tree':{'type':'piecewise','branches':[{'condition':{'type':'compare','parameter':'year','operator':'eq','value':'1'},'value':{'type':'parameter','name':'base'}}],'otherwise':{'type':'multiply','args':[{'type':'parameter','name':'previous'},{'type':'constant','value':'1.1'}]}}};t['bindings']=[{'name':'year','kind':'input','key':'policy_year'},{'name':'base','kind':'table','key':'base_amount'},{'name':'previous','kind':'previous','key':'t'}]
  value=self.run_target(t)['data']['table_values'];self.assertEqual(value['exact_fractions'],{'1':'25000','2':'27500','3':'30250'})
 def test_condition_is_not_automatically_zero(self):
  t=self.target();t['formula']={'variables':[{'name':'year','unit':'dimensionless'}],'conditions':[{'parameter':'year','operator':'ge','value':'2'}],'expression_tree':{'type':'constant','value':'10','unit':'CNY'}};t['bindings']=[{'name':'year','kind':'input','key':'policy_year'}]
  value=self.run_target(t);self.assertEqual(value['status'],'partial');self.assertIsNone(value['data']['table_values']['values']['1']);self.assertNotIn('1',value['data']['table_values']['exact_fractions'])
 def test_result_tampering_and_cross_bundle_rejected(self):
  t={**self.target('exact_table_lookup'),'lookup_role':'cash_value'};value=self.run_target(t)['data']['table_values'];value['values']['1']='999'
  with self.assertRaises(Conflict):validate_result(value,self.content)
  value=self.run_target(t)['data']['table_values']
  with self.assertRaises(Conflict):validate_result(value,{**self.content,'bundle_id':'other'})
 def test_different_inputs_cannot_be_assembled(self):
  t={**self.target('exact_table_lookup'),'lookup_role':'cash_value'};a=self.run_target(t)['data']['table_values'];b=self.run_target(t,{**self.inputs,'annual_premium_input':'20000'})['data']['table_values'];b['rule_id']='other';b['id']='table-values:'+stable_hash({k:v for k,v in b.items() if k!='id'})
  with patch('insurance_rag.ingestion.source_execution.load_execution',return_value=({'targets':[t]},[])),self.assertRaisesRegex(Conflict,'inputs_mismatch'):assemble(self.app,self.content,{'value_refs':['a','b']},[a,b])
 def test_tool_operation_is_checked(self):
  self.assertEqual(self.run_target(self.target(),operation='lookup')['reason_code'],'exact_lookup_rule_required')
 def test_binding_rejects_hallucinated_basis_and_ranges(self):
  for raw in ('20至30','1.5','-2'):
   with self.assertRaises(ValueError):normalized(raw,'integer')
  self.assertEqual(normalized('第3保单年度','integer'),3);self.assertEqual(normalized('一次性交付','payment_years'),1)
  plan={'state':'bound','role':'cash_value','basis_kind':'annual_premium','basis_amount':'10000','currency':'CNY','component':'base','axes':[],'evidence':[{'source_id':'s','quote':'每1000元年交保费'}]}
  with self.assertRaisesRegex(ValueError,'basis_not_in_evidence'):validate_plan(plan,{'s':'每1000元年交保费'})

 def test_normal_plan_paid_count_stops_after_payment_term(self):
  t=self.target();t['formula']={'variables':[{'name':'premium','unit':'CNY'},{'name':'count','unit':'dimensionless'}],'expression_tree':{'type':'multiply','args':[{'type':'parameter','name':'premium'},{'type':'parameter','name':'count'}]}};t['bindings']=[{'name':'premium','kind':'input','key':'annual_premium_input'},{'name':'count','kind':'plan_paid_years','key':''}]
  value=self.run_target(t,{**self.inputs,'end_year':5})['data']['table_values'];self.assertEqual(value['exact_fractions']['5'],'30000')

 def test_official_case_payment_cap_is_not_personalized(self):
  t=self.target();t['formula']={'variables':[],'constants':[{'value':'10','evidence':{'quote':'保单描述：被保险人年龄40周岁，交费方式10年交'}}],'expression_tree':{'type':'constant','value':'10','unit':'CNY'}};t['bindings']=[]
  self.assertEqual(self.run_target(t)['reason_code'],'formula_constant_case_scope_unresolved')

 def test_declared_base_and_premium_conflict_blocks_all_affected_paths(self):
  lookup_target={**self.target('exact_table_lookup'),'lookup_role':'base_amount'}
  cash_target={**self.target('exact_table_lookup'),'lookup_role':'cash_value'}
  formula=self.target();formula['formula']={'expression_tree':{'type':'parameter','name':'base'},'variables':[{'name':'base','unit':'CNY'}]};formula['bindings']=[{'name':'base','kind':'table','key':'base_amount'}]
  for target in (lookup_target,cash_target,formula):
   with self.subTest(method=target.get('lookup_role',target['method'])):
    output=self.run_target(target,{**self.inputs,'base_amount_input':'100'})
    self.assertEqual(output['status'],'conflict');self.assertEqual(output['reason_code'],'user_base_amount_conflicts_with_exact_mapping');self.assertNotIn('table_values',output.get('data',{}))
  self.assertEqual(self.run_target(lookup_target,{**self.inputs,'base_amount_input':'25000'})['status'],'ok')
 def test_declared_amount_pair_without_source_relationship_stays_unresolved(self):
  target={**self.target('exact_table_lookup'),'lookup_role':'cash_value'}
  output=self.run_target(target,{**self.inputs,'base_amount_input':'100'},records=[])
  self.assertEqual(output['status'],'no_evidence');self.assertEqual(output['reason_code'],'declared_base_amount_mapping_unverified')

 def test_historical_amount_conflict_is_rejected_without_invalidating_consistent_results(self):
  target={**self.target('exact_table_lookup'),'lookup_role':'cash_value'};value=self.run_target(target)['data']['table_values']
  value['_audit']['inputs']={**self.inputs,'base_amount_input':'100'};value['id']='table-values:'+stable_hash({k:v for k,v in value.items() if k!='id'})
  with patch('insurance_rag.ingestion.source_execution.load_execution',return_value=({'targets':[target]},self.records)):
   with self.assertRaisesRegex(Conflict,'user_base_amount_conflicts'):validate_result(value,self.content,app=self.app)
   value['_audit']['inputs']['base_amount_input']='25000';value['id']='table-values:'+stable_hash({k:v for k,v in value.items() if k!='id'});validate_result(value,self.content,app=self.app)

class BindingAxisRegression(unittest.TestCase):
 def test_swapped_age_and_payment_axes_rejected_even_when_positive(self):
  from insurance_rag.ingestion.source_execution_bindings import validate_rows,record_axes
  plan={'state':'bound','axes':[{'name':'entry_age','source':'row','index':0,'normalizer':'integer','value':''},{'name':'payment_years','source':'row','index':1,'normalizer':'payment_years','value':''}]}
  cells=[{'mapping':{'row_dimensions':[{'raw':'3','label_refs':['term']},{'raw':'35','label_refs':['age']}],'column_path':[]}}]
  with self.assertRaisesRegex(ValueError,'header_mismatch'):validate_rows(cells,plan,{'header:term':'交费期间','header:age':'投保年龄'})
  plan['axes'][0]['index']=1;plan['axes'][1]['index']=0
  validate_rows(cells,plan,{'header:term':'交费期间','header:age':'投保年龄'})
  self.assertEqual(record_axes(cells[0]['mapping'],plan),{'entry_age':35,'payment_years':3})
  cells[0]['mapping']['row_dimensions'][0]['raw']='0'
  with self.assertRaisesRegex(ValueError,'domain_invalid'):record_axes(cells[0]['mapping'],plan)

if __name__=='__main__':unittest.main()
