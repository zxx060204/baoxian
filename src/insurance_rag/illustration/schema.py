"""One contract for SQL, deterministic derivation, source formulas and official references."""
from typing import Literal,Annotated
from pydantic import BaseModel, ConfigDict, Field, model_validator

POLICY='illustration-model-v17'
CATALOG={
 'policy_year':'保单年度', 'year_end_age':'被保险人年度末年龄',
 'annual_premium':'当年度保险费', 'cumulative_premium':'累计保险费',
 'cash_value':'当年度末现金价值', 'surrender_value':'当年度末退保金',
 'base_amount':'基本保险金额', 'effective_amount':'有效保险金额',
 'attained_age':'给付计算到达年龄',
 'death_benefit':'身故保险金', 'disability_benefit':'全残保险金',
 'dividend':'红利',
}
Mode=Literal['sql_lookup','deterministic_derived','source_formula','official_case','scheduled_formula']
# Membership is authorized against a source-bound product profile.
MetricCode=Annotated[str,Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')]
INPUTS={'entry_age','payment_years','annual_premium_input','policy_year','waiting_period_passed','adult_anniversary_passed','paid_anniversary_passed','normal_payment_no_changes_no_loans'}
UNITS={**{k:('year' if k in ('policy_year','year_end_age','attained_age') else 'CNY') for k in CATALOG},'entry_age':'year','payment_years':'year','annual_premium_input':'CNY','waiting_period_passed':'boolean','adult_anniversary_passed':'boolean','paid_anniversary_passed':'boolean','normal_payment_no_changes_no_loans':'boolean'}
UNITS.update(annuity_start_year='year',annuity_end_year='year',maturity_year='year',base_amount_input='CNY')
INPUTS |= {'annuity_start_year','annuity_end_year','maturity_year','base_amount_input'}
UNITS['annuity_start_age']='year';INPUTS.add('annuity_start_age')
UNITS.update(pension_annuity_rate='dimensionless',benefit_anniversary_eligible='boolean')
INPUTS |= {'pension_annuity_rate','benefit_anniversary_eligible'}

class Strict(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Citation(Strict):
 source_id:str
 quote:str=Field(min_length=1,max_length=2000)
class Predicate(Strict):
 parameter:str
 operator:Literal['lt','le','eq','ne','ge','gt']
 value:str|None=Field(default=None,min_length=1,max_length=80)
 right_parameter:str|None=None
 evidence:Citation
 @model_validator(mode='after')
 def valid(self):
  if (self.value is None)==(self.right_parameter is None):raise ValueError('one_condition_rhs_required')
  return self
class ConstantProof(Strict):
 value:str=Field(min_length=1,max_length=80)
 raw_value:str=Field(min_length=1,max_length=80)
 conversion:Literal['identity','percent']= 'identity'
 evidence:Citation
 unit:Literal['CNY','year','dimensionless','CNY/year']='dimensionless'
class VariableDefinition(Strict):
 name:str=Field(pattern=r'^[a-z][a-z0-9_]{0,63}$')
 meaning:str=Field(min_length=1,max_length=200)
 unit:Literal['CNY','year','dimensionless','CNY/year','boolean','date']
 source_kind:Literal['metric','input','expression']
 binding:str|None=None
 expression_tree:dict|None=None
 evidence:list[Citation]=Field(min_length=1,max_length=6)
class FormulaProposal(Strict):
 metric_code:MetricCode
 summary:str=Field(min_length=1,max_length=240)
 evidence:list[Citation]=Field(min_length=1,max_length=12)
 expression_tree:dict|None=None
 constants:list[ConstantProof]=Field(default_factory=list,max_length=20)
 conditions:list[Predicate]=Field(default_factory=list,max_length=12)
 applicability:dict|None=None
 variables:list[VariableDefinition]=Field(default_factory=list,max_length=20)
 guarantee_status:Literal['guaranteed','non_guaranteed','unknown']='unknown'
 time_basis:str=Field(min_length=1,max_length=100)
 special_summary:str=Field(default='',max_length=300)
 # Fail closed until ALL branch prerequisites and referenced list items are supplied.
 complete:bool
 missing:list[str]=Field(default_factory=list,max_length=20)
class Proposals(Strict):
 formulas:list[FormulaProposal]=Field(default_factory=list,max_length=10)

class Metric(Strict):
 normalization_notes:list[dict]=Field(default_factory=list)
 metric_code:MetricCode
 title:str
 acquisition_mode:Mode
 status:Literal['available','source_verified','missing_evidence','mapping_required','official_reference_only','not_applicable','invalid_formula']
 unit:str
 required_inputs:list[str]=Field(default_factory=list)
 dependencies:list[str]=Field(default_factory=list)
 expression_tree:dict|None=None
 conditions:list[dict]=Field(default_factory=list)
 source_refs:list[dict]=Field(default_factory=list)
 display_labels:list[str]=Field(default_factory=list)
 display_summary:str=''
 missing_reason:str|None=None
 lookup_binding:dict|None=None
 official_case_binding:dict|None=None
 time_basis:str|None=None
 verification:dict=Field(default_factory=dict)
 variables:list[dict]=Field(default_factory=list)
 applicability:dict|None=None
 guarantee_status:Literal['guaranteed','non_guaranteed','unknown']='unknown'
 personalization:Literal['personal_amount','official_reference_only']='personal_amount'
 execution_readiness:Literal['ready','candidate','blocked']='blocked'
 execution_scope:dict=Field(default_factory=dict)
 layer:Literal['common','extension']='common'
 common_concept:str|None=None
 output_role:Literal['annual_column','summary_parameter','internal_dependency','qualitative','reference']='annual_column'
 default_scenario:dict=Field(default_factory=dict)
 exceptions_notice:dict=Field(default_factory=dict)
 classification:dict=Field(default_factory=dict)
 typed_config:dict=Field(default_factory=dict)
 @model_validator(mode='after')
 def valid(self):
  if self.acquisition_mode=='source_formula' and self.status=='source_verified' and (not self.expression_tree or not self.source_refs):raise ValueError('formula_evidence_required')
  return self

def standard_metrics():
 result=[]
 for code,title in CATALOG.items():
  mode='deterministic_derived' if code in ('policy_year','year_end_age','surrender_value','annual_premium','cumulative_premium') else 'sql_lookup' if code in ('cash_value','base_amount') else 'official_case' if code=='dividend' else 'source_formula'
  basic=mode=='deterministic_derived';mapping=mode=='sql_lookup'
  result.append(Metric(metric_code=code,title=title,acquisition_mode=mode,
   status='available' if basic else 'mapping_required' if mapping else 'missing_evidence',
   unit='year' if code in ('policy_year','year_end_age','attained_age') else 'CNY',
   required_inputs=['entry_age'] if code=='year_end_age' else [] if code in ('policy_year','surrender_value') else ['annual_premium_input','payment_years'] if basic else [],
   dependencies=['cash_value'] if code=='surrender_value' else [],
   execution_readiness='ready' if basic else 'blocked',personalization='official_reference_only' if code=='dividend' else 'personal_amount',
   display_labels=['year_end_display_age_convention'] if code=='year_end_age' else ['default_cash_value_assumption'] if code=='surrender_value' else ['planned_annual_payment'] if basic and code!='policy_year' else ['official_reference_only'] if code=='dividend' else [],
   display_summary='展示口径：投保年龄＋保单年度；不是给付公式中的到达年龄，也不是按出生日期核算的实际周岁。' if code=='year_end_age' else '未找到明确退保金规则时，按系统约定默认等同现金价值；这是演示假设，不是合同保证。' if code=='surrender_value' else '按用户明确选择的正常年交计划计算，不等同实际历史交费记录。' if basic and code!='policy_year' else '',
   missing_reason=None if basic else 'exact_business_mapping_required' if mapping else 'no_supported_official_case' if code=='dividend' else 'source_formula_not_prepared').model_dump())
 return result


def foundation_metrics():
 """Plan coordinates and exact-data dependencies, not a benefit catalog."""
 result=[]
 for code in ('policy_year','year_end_age','annual_premium','cumulative_premium','base_amount','cash_value'):
  lookup=code in ('base_amount','cash_value')
  result.append(Metric(metric_code=code,title=CATALOG[code],unit='year' if code in ('policy_year','year_end_age') else 'CNY',
   acquisition_mode='sql_lookup' if lookup else 'deterministic_derived',status='mapping_required' if lookup else 'available',
   execution_readiness='blocked' if lookup else 'ready',missing_reason='exact_business_mapping_required' if lookup else None,
   required_inputs=[] if lookup or code=='policy_year' else ['entry_age'] if code=='year_end_age' else ['annual_premium_input','payment_years'],
   output_role='summary_parameter' if code=='base_amount' else 'annual_column',common_concept=code,
   classification={'method':'plan_convention_or_exact_data_dependency'},
   display_summary='展示口径：投保年龄＋保单年度；不代替合同给付年龄。' if code=='year_end_age' else '按用户明确的正常年交计划计算。' if not lookup else '').model_dump())
 return result
