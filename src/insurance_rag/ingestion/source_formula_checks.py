"""Deterministic selected-evidence and typed-expression checks, without business heuristics."""
from copy import deepcopy
from insurance_rag.benefits.formula_evidence import bind,selection_schema
from insurance_rag.illustration.schema import Proposals
from insurance_rag.illustration.expressions import inspect_expression
from insurance_rag.illustration.logic import validate_condition

POLICY='current-source-formula-checks-v2'
UNITS=('CNY','year','dimensionless','CNY/year')

def schema(target):
 result=selection_schema(target);f=result['$defs']['FormulaProposal'];f['properties']['output_unit']={'type':'string','enum':list(UNITS)};f['required'].append('output_unit');return result

def validate_candidate(raw,catalog,target):
 if not isinstance(raw,dict) or set(raw)!={'formulas'} or not isinstance(raw['formulas'],list) or len(raw['formulas'])>1:raise ValueError('invalid_formula_envelope')
 if not raw['formulas']:return {'state':'evidence_insufficient','missing':['no_formula_in_selected_evidence']}
 candidate=deepcopy(raw);f=candidate['formulas'][0]
 if not isinstance(f,dict):raise ValueError('invalid_formula_object')
 unit=f.pop('output_unit',None)
 if unit not in UNITS:raise ValueError('invalid_declared_output_unit')
 bound=bind(candidate,catalog);parsed=Proposals.model_validate(bound)
 if len(parsed.formulas)!=1:raise ValueError('one_target_formula_required')
 p=parsed.formulas[0]
 if p.metric_code!=target:raise ValueError('formula_target_mismatch')
 variables=[v.model_dump() for v in p.variables];units={v['name']:v['unit'] for v in variables}
 if len(units)!=len(variables):raise ValueError('duplicate_variable_declaration')
 # A declaration is a syntax/type contract, never a demand for a registered metric or value.
 for v in variables:
  if v['expression_tree'] is not None and inspect_expression(v['expression_tree'],units)[0]!=v['unit']:raise ValueError('variable_expression_unit_mismatch')
 if p.expression_tree is not None:
  actual,refs=inspect_expression(p.expression_tree,units)
  if actual is not None and actual!=unit:raise ValueError('result_unit_mismatch')
 else:refs=set()
 for condition in p.conditions:refs.update(validate_condition({k:v for k,v in condition.model_dump(exclude_none=True).items() if k!='evidence'},units))
 if p.applicability:refs.update(validate_condition(p.applicability,units))
 def unavailable(value):
  if isinstance(value,dict):return value.get('type')=='unavailable' or any(unavailable(x) for x in value.values())
  if isinstance(value,list):return any(unavailable(x) for x in value)
  return False
 if unavailable(p.expression_tree) or any(unavailable(v['expression_tree']) for v in variables):return {'state':'evidence_insufficient','missing':p.missing or ['expression_contains_unavailable_branch'],'selected_formula':p.model_dump()}
 if not p.complete or p.missing:return {'state':'evidence_insufficient','missing':p.missing or ['formula_evidence_incomplete'],'selected_formula':p.model_dump()}
 if p.expression_tree is None:raise ValueError('formula_expression_missing')
 return {'state':'deterministic_checks_passed','formula':{**p.model_dump(),'output_unit':unit},'symbol_references':sorted(refs),'verification':{'source':'selected_current_evidence','structure':'passed','numbers':'program_bound','units':'passed','business_semantics':'not_evaluated','dependency_readiness':'not_evaluated','policy':POLICY},'execution_readiness':'not_evaluated'}
