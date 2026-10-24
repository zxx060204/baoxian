"""Program-owned acquisition guidance. No product identities or downstream availability checks."""
import re
from copy import deepcopy

POLICY='column-acquisition-rules-v1'
# Full business labels only: never infer an acquisition method from a substring.
PREMIUM={'保费','保险费','年交保费','年交保险费','年缴保费','年缴保险费','年度保费','年度保险费','年初保费','年初保险费','当期保费','当期保险费','应交保费','应交保险费'}
CASH={'现金价值','保单现金价值'}
SURRENDER={'退保金','退保金额','退保金(现金价值)','现金价值(退保金)'}
SCENARIO=re.compile(r'^(?:(?:最低)?保证利益演示|红利利益演示|万能结息利益演示|[低中高](?:档|档利益演示)?)$')

def business_label(target):
 parts=[re.sub(r'\s+','',x).replace('（','(').replace('）',')') for x in target['header_path']]
 parts=[x for x in parts if not SCENARIO.fullmatch(x)]
 name=parts[-1] if parts else ''
 return re.sub(r'^(?:当年度末|当年度|年度末|当年末|当年)','',name)

def lookup_kind(target):
 label=business_label(target)
 if label in PREMIUM:return 'premium'
 if label in CASH:return 'cash_value'
 if label in SURRENDER:return 'surrender_value'
 return None

def source_column(target):
 return {k:deepcopy(target[k]) for k in ('target_id','case_id','document_version_id','logical_table_id','column_index','title','header_path','header_ids') if k in target}

def rule_for(outcome):
 target=outcome['target'];kind=lookup_kind(target);base={'policy':POLICY,'source_column':source_column(target)}
 if kind:
  rule={**base,'type':'exact_table_lookup','table_type':'premium_rate' if kind=='premium' else 'cash_value','business_field':kind,'basis':'configured_acquisition_policy','guidance':'方法为详细查表：按具体条件精确查询对应费率表获取原始保险费。' if kind=='premium' else '方法为详细查表：按具体条件精确查询对应现金价值表。'}
  if kind=='surrender_value':
   rule.update(basis='system_default',assumption='默认无特殊约定时，退保金等于现金价值。',applicability='no_special_provisions',guidance='默认无特殊约定时退保金等于现金价值，按具体条件精确查询现金价值表；如资料存在特殊约定，应按原文约定处理，不能无条件套用默认关系。',special_provisions='not_semantically_determined',context_source_ids=[s['source_id'] for s in outcome.get('source_catalog',[])])
  return rule
 state=outcome.get('formula_state',outcome['state'])
 if state=='deterministic_checks_passed':return {**base,'type':'formula','basis':'source_formula','guidance':'按提取公式及其原文条件计算；实际输入与工具调用由后续Agent处理。','formula':deepcopy(outcome['formula'])}
 if state=='evidence_insufficient':return {**base,'type':'illustration_reference','basis':'no_explicit_formula_in_collected_evidence','guidance':'当前资料证据中未发现明确公式，建议获取说明书利益演示表的对应列原始数据进行展示；不得据此缩放、插值或推算个人结果。','example_ref':{k:target[k] for k in ('case_id','logical_table_id','column_index','target_id') if k in target},'missing':deepcopy(outcome.get('missing',[]))}
 return None

def finalize_rule(outcome):
 result=deepcopy(outcome);rule=rule_for(result)
 if rule is not None:
  if lookup_kind(result['target']):result['state']='rule_extracted'
  elif result['state']=='evidence_insufficient':result['formula_state']='evidence_insufficient';result['state']='rule_extracted'
  result['rule']=rule
 return result
