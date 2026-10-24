import unittest
from fractions import Fraction
from insurance_rag.illustration.expressions import inspect_expression,evaluate
from insurance_rag.benefits.formula_evidence import selection_schema

def p(name):return {'type':'parameter','name':name}
def op(kind,*args):return {'type':kind,'args':list(args)}
class FlexibleExpressionTests(unittest.TestCase):
 def test_nary_max_min_sum_and_product_execute_exactly(self):
  units={'a':'CNY','b':'CNY','c':'CNY','ratio':'dimensionless'};env={'a':10,'b':20,'c':30,'ratio':'0.5'}
  tree=op('max',p('a'),p('b'),op('multiply',op('sum',p('a'),p('b'),p('c')),p('ratio'),p('ratio')))
  self.assertEqual(inspect_expression(tree,units)[0],'CNY');self.assertEqual(evaluate(tree,env,units,'CNY')['value'],'20')
  self.assertEqual(evaluate(op('min',p('a'),p('b'),p('c')),env,units,'CNY')['value'],'10')
 def test_nested_piecewise_and_unary_operations(self):
  units={'a':'CNY','b':'CNY','eligible':'boolean'};env={'a':10,'b':25,'eligible':True}
  branch={'type':'piecewise','branches':[{'condition':{'type':'compare','parameter':'eligible','operator':'eq','value':'true'},'value':op('subtract',p('a'),p('b'))}],'otherwise':p('a')}
  self.assertEqual(evaluate({'type':'abs','arg':branch},env,units,'CNY')['value'],'15')
  self.assertEqual(evaluate({'type':'negate','arg':op('subtract',p('a'),p('b'))},env,units,'CNY')['value'],'15')
 def test_rate_time_and_explicit_count_are_distinct_valid_amounts(self):
  units={'rate':'CNY/year','duration':'year','premium':'CNY','count':'dimensionless'};env={'rate':10,'duration':3,'premium':10,'count':3}
  for tree in (op('multiply',p('rate'),p('duration')),op('multiply',p('premium'),p('count')),op('multiply',p('premium'),{'type':'year_count','arg':p('duration')})):
   self.assertEqual(evaluate(tree,env,units,'CNY')['value'],'30')
  with self.assertRaisesRegex(ValueError,'unsupported_compound_unit'):inspect_expression(op('multiply',p('premium'),p('duration')),units)
 def test_compound_intermediate_units_can_cancel(self):
  tree=op('divide',op('multiply',p('amount'),p('years'),p('ratio')),p('years'))
  units={'amount':'CNY','years':'year','ratio':'dimensionless'}
  self.assertEqual(evaluate(tree,{'amount':100,'years':5,'ratio':'0.8'},units,'CNY')['value'],'80')
 def test_ordered_binary_operators_are_not_ambiguous(self):
  for kind in ('subtract','divide','power'):
   with self.assertRaisesRegex(ValueError,'binary_operator_requires_two_arguments'):inspect_expression(op(kind,p('x'),p('x'),p('x')),{'x':'dimensionless'})
 def test_large_reasonable_tree_and_negative_integer_power(self):
  tree=op('sum',*[p('amount') for _ in range(64)])
  self.assertEqual(evaluate(tree,{'amount':1},{'amount':'CNY'},'CNY')['value'],'64')
  self.assertEqual(evaluate(op('power',p('x'),p('n')),{'x':2,'n':-2},{'x':'dimensionless','n':'dimensionless'},'dimensionless')['value'],'0.25')
 def test_mixed_unit_sum_and_empty_arguments_are_rejected(self):
  with self.assertRaisesRegex(ValueError,'expression_unit_mismatch'):inspect_expression(op('sum',p('a'),p('t')),{'a':'CNY','t':'year'})
  with self.assertRaisesRegex(ValueError,'invalid_expression_arity'):inspect_expression(op('max'),{})
 def test_schema_exposes_nary_and_rate_units(self):
  schema=selection_schema('fixture');nodes=schema['$defs']['SelectedExpression']['anyOf']
  nary=next(n for n in nodes if 'max' in n['properties']['type'].get('enum',[]))
  self.assertGreater(nary['properties']['args']['maxItems'],2)
  self.assertIn('CNY/year',schema['$defs']['VariableDefinition']['properties']['unit']['enum'])

 def test_rate_unit_survives_current_evidence_binding_and_formula_checks(self):
  from types import SimpleNamespace
  from test_current_source_rules import sources,proposal,target
  from insurance_rag.ingestion.source_rules import extract_rule_target
  def invoke(system,payload):
   raw=proposal(payload,tree=op('multiply',p('unregistered_amount'),p('duration')))
   variables=raw['formulas'][0]['variables'];variables[0]['unit']='CNY/year'
   variables.append({**variables[0],'name':'duration','meaning':'持续时间','unit':'year'})
   return raw
  result=extract_rule_target(target(),sources(),SimpleNamespace(structured=invoke))
  self.assertEqual(result['rule']['type'],'formula');self.assertEqual(result['formula']['output_unit'],'CNY')
