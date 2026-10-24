"""Deterministic validation feedback for one model review, without contract-semantic heuristics."""
POLICY='one-validation-review-v1'

def feedback(raw,error):
 result={'code':error,'unit_conflicts':[]}
 if not isinstance(raw,dict):return result
 if error=='invalid_formula_envelope':result.update(expected_top_level=['formulas'],actual_top_level=list(raw)[:20])
 for i,formula in enumerate(raw.get('formulas',[]) if isinstance(raw.get('formulas'),list) else []):
  if not isinstance(formula,dict):continue
  variables={v['name']:v for v in formula.get('variables',[]) if isinstance(v,dict) and isinstance(v.get('name'),str)}
  def walk(value,path):
   if len(result['unit_conflicts'])>=12:return
   if isinstance(value,list):
    for j,child in enumerate(value):walk(child,f'{path}[{j}]')
   elif isinstance(value,dict):
    left=variables.get(value.get('parameter'),{}) if isinstance(value.get('parameter'),str) else {};right=variables.get(value.get('right_parameter'),{}) if isinstance(value.get('right_parameter'),str) else {}
    if left and right and left.get('unit')!=right.get('unit'):
     result['unit_conflicts'].append({'path':path,'left':{k:left.get(k) for k in ('name','meaning','unit')},'right':{k:right.get(k) for k in ('name','meaning','unit')}})
    for k,child in value.items():walk(child,path+'.'+k)
  walk(formula,f'formulas[{i}]')
 return result

INSTRUCTION='本次是该目标唯一一次程序校验错误重审。根据validation_error及原始证据纠正previous_output，重新输出完整schema结果。保留原文变量含义、条件和来源，不能为了单位一致而把日期改称年龄，不能通过改名掩盖类型冲突；应纠正比较所用变量或有来源支持的表达。不保证必须生成公式，证据不足应如实返回。不要输出解释字段或自选查表/参考分支。'
