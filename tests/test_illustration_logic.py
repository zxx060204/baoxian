import copy,unittest
from insurance_rag.illustration.logic import evaluate_condition,validate_condition
from insurance_rag.illustration.expressions import evaluate,inspect_expression,MissingExpression
from insurance_rag.illustration.schema import standard_metrics,UNITS
from insurance_rag.illustration.preparation import validate_proposal

def p(name):return {'type':'parameter','name':name}
def c(value,unit='dimensionless'):return {'type':'constant','value':str(value),'unit':unit}
def op(kind,a,b):return {'type':kind,'args':[a,b]}
def cmp(key,operator,value=None,right=None):return {'type':'compare','parameter':key,'operator':operator,**({'right_parameter':right} if right else {'value':str(value)})}

class TypedIllustrationTests(unittest.TestCase):
 def test_compound_and_right_parameter_comparison(self):
  condition={'type':'all','args':[cmp('waiting_period_passed','eq','true'),cmp('policy_year','gt',right='payment_years')]}
  self.assertEqual(evaluate_condition(condition,{'waiting_period_passed':True,'policy_year':4,'payment_years':3},UNITS),(True,[]))
  self.assertFalse(evaluate_condition(condition,{'waiting_period_passed':False,'policy_year':4,'payment_years':3},UNITS)[0])
 def test_missing_boolean_never_becomes_false(self):
  value,missing=evaluate_condition({'type':'not','arg':cmp('waiting_period_passed','eq','true')},{},UNITS)
  self.assertIsNone(value);self.assertEqual(missing,['waiting_period_passed'])
 def test_decisive_and_or_do_not_require_irrelevant_input(self):
  unknown=cmp('waiting_period_passed','eq','true');known=cmp('policy_year','eq','1')
  self.assertEqual(evaluate_condition({'type':'any','args':[unknown,known]},{'policy_year':1},UNITS),(True,[]))
  self.assertEqual(evaluate_condition({'type':'all','args':[unknown,known]},{'policy_year':2},UNITS),(False,[]))
 def test_condition_units_and_unknown_operators_rejected(self):
  with self.assertRaisesRegex(ValueError,'condition_unit_mismatch'):validate_condition(cmp('policy_year','eq',right='cumulative_premium'),UNITS)
  with self.assertRaises(ValueError):validate_condition(cmp('policy_year','execute','1'),UNITS)
 def test_date_boundary_is_explicit(self):
  self.assertTrue(evaluate_condition(cmp('event','ge',right='boundary'),{'event':'2027-01-01','boundary':'2027-01-01'},{'event':'date','boundary':'date'})[0])
  self.assertFalse(evaluate_condition(cmp('event','gt',right='boundary'),{'event':'2027-01-01','boundary':'2027-01-01'},{'event':'date','boundary':'date'})[0])
 def test_money_cannot_be_added_to_age(self):
  with self.assertRaisesRegex(ValueError,'unit_mismatch'):inspect_expression(op('add',p('cash_value'),p('attained_age')),UNITS)
 def test_branch_outputs_need_same_units(self):
  tree={'type':'piecewise','branches':[{'condition':cmp('policy_year','eq','1'),'value':p('cash_value')}],'otherwise':p('attained_age')}
  with self.assertRaisesRegex(ValueError,'branch_unit_mismatch'):inspect_expression(tree,UNITS)
 def test_exact_year_growth_without_model_arithmetic(self):
  tree=op('multiply',p('base_amount'),op('power',c('1.0175'),op('subtract',p('policy_year'),c(1,'year'))))
  result=evaluate(tree,{'base_amount':'10000','policy_year':2},UNITS,'CNY');self.assertEqual(result['value'],'10175')
 def test_power_is_bounded_and_integer_only(self):
  for exponent in ('121','0.5','-121'):
   with self.assertRaisesRegex(ValueError,'integer_exponent'):evaluate(op('power',c('1.0175'),c(exponent)),{},UNITS,'dimensionless')
 def test_unknown_branch_does_not_use_otherwise(self):
  tree={'type':'piecewise','branches':[{'condition':cmp('waiting_period_passed','eq','true'),'value':c('1','CNY')}],'otherwise':c('0','CNY')}
  with self.assertRaises(MissingExpression):evaluate(tree,{},UNITS,'CNY')
 def test_unsupported_branch_returns_missing_not_zero(self):
  tree={'type':'piecewise','branches':[{'condition':cmp('policy_year','eq','1'),'value':c('1','CNY')}],'otherwise':{'type':'unavailable','reason':'not_verified'}}
  with self.assertRaisesRegex(MissingExpression,'not_verified'):evaluate(tree,{'policy_year':2},UNITS,'CNY')
 def test_product_variable_proposal_is_typed_and_stays_candidate(self):
  text='身故保险金为累计保险费的160%。';citation={'source_id':'s','quote':text};source={'source_id':'s','text':text,'source':{'document_version_id':'d','page_number':1}}
  raw={'formulas':[{'metric_code':'death_benefit','summary':'按比例计算','evidence':[citation],'expression_tree':op('multiply',p('paid'),c('1.6')),'variables':[{'name':'paid','meaning':'对应累计已交保费','unit':'CNY','source_kind':'metric','binding':'cumulative_premium','evidence':[citation]}],'constants':[{'value':'1.6','raw_value':'160%','conversion':'percent','evidence':citation}],'time_basis':'给付时点','complete':True}]}
  result=validate_proposal(raw,[source],'death_benefit');self.assertEqual(result['dependencies'],['cumulative_premium']);self.assertEqual(result['execution_readiness'],'candidate')
  raw['formulas'][0]['variables'][0]['unit']='year'
  with self.assertRaisesRegex(ValueError,'binding_unit_mismatch'):validate_proposal(raw,[source],'death_benefit')
 def test_number_substring_is_not_a_constant_proof(self):
  text='身故保险金为累计保险费的160%。';citation={'source_id':'s','quote':text};source={'source_id':'s','text':text,'source':{'document_version_id':'d','page_number':1}}
  raw={'formulas':[{'metric_code':'death_benefit','summary':'错误常数','evidence':[citation],'expression_tree':op('multiply',p('cumulative_premium'),c('60')),'constants':[{'value':'60','raw_value':'60','evidence':citation}],'time_basis':'给付时点','complete':True}]}
  with self.assertRaisesRegex(ValueError,'constant_not_literal'):validate_proposal(raw,[source],'death_benefit')
 def test_assembled_table_preview_can_be_referenced_without_paging(self):
  from insurance_rag.agent.context import ContextEngine
  packet={'status':'ok','data':{'benefit_table':{'id':'result','product':'产品','columns':[{'id':'cash_value','name':'现金价值'}],'issues':[],'rows':[{'year':1,'values':{'cash_value':'100'*6000}}]}}}
  preview=ContextEngine().wire(packet,'ref');self.assertTrue(preview['reference_ready']);self.assertFalse(preview['read_before_answer_required']);self.assertEqual(preview['result_summary']['benefit_table']['rows'],1)

if __name__=='__main__':unittest.main()
