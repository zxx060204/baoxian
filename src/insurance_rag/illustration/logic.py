"""Bounded compound predicates with three-valued logic and explicit units."""
from datetime import date
from decimal import Decimal,InvalidOperation
from fractions import Fraction

def validate_condition(node,units,*,max_nodes=80):
 count=0;dependencies=set()
 def walk(n,depth=0):
  nonlocal count
  count+=1
  if count>max_nodes or depth>10 or not isinstance(n,dict):raise ValueError('condition_tree_limit')
  kind=n.get('type','compare')
  if kind in ('all','any') and set(n)=={'type','args'} and isinstance(n['args'],list) and 1<=len(n['args'])<=20:
   for child in n['args']:walk(child,depth+1)
  elif kind=='not' and set(n)=={'type','arg'}:walk(n['arg'],depth+1)
  elif kind=='compare':
   allowed={'type','parameter','operator','value','right_parameter'}
   if set(n)-allowed or not {'parameter','operator'}<=set(n) or ('value' in n)==('right_parameter' in n):raise ValueError('invalid_condition_shape')
   key=n['parameter'];op=n['operator']
   if key not in units or op not in ('lt','le','eq','ne','ge','gt'):raise ValueError('unknown_condition_parameter')
   dependencies.add(key);unit=units[key]
   if 'right_parameter' in n:
    right=n['right_parameter']
    if right not in units or units[right]!=unit:raise ValueError('condition_unit_mismatch')
    dependencies.add(right)
   else:
    value=n['value']
    if not isinstance(value,str) or len(value)>80:raise ValueError('invalid_condition_value')
    if unit=='boolean':
     if value not in ('true','false') or op not in ('eq','ne'):raise ValueError('invalid_boolean_condition')
    elif unit=='date':date.fromisoformat(value)
    else:
     try:d=Decimal(value)
     except InvalidOperation:raise ValueError('invalid_condition_number') from None
     if not d.is_finite() or abs(d.adjusted())>100 or d.as_tuple().exponent < -100:raise ValueError('invalid_condition_number')
   if unit=='boolean' and op not in ('eq','ne'):raise ValueError('invalid_boolean_condition')
  else:raise ValueError('unsupported_condition_node')
 walk(node);return dependencies

def evaluate_condition(node,env,units):
 validate_condition(node,units);missing=set()
 def walk(n):
  kind=n.get('type','compare')
  if kind in ('all','any'):
   results=[walk(child) for child in n['args']]
   if kind=='all':return False if False in results else None if None in results else True
   return True if True in results else None if None in results else False
  if kind=='not':
   result=walk(n['arg']);return None if result is None else not result
  key=n['parameter'];right=n.get('right_parameter')
  keys=[key]+([right] if right else [])
  absent=[k for k in keys if k not in env or env[k] is None]
  if absent:missing.update(absent);return None
  unit=units[key]
  def cast(v):
   if unit=='boolean':
    if type(v) is bool:return v
    if v in ('true','false'):return v=='true'
    raise ValueError('boolean_condition_required')
   if unit=='date':return date.fromisoformat(v)
   if isinstance(v,Fraction):return v
   try:d=Decimal(str(v))
   except InvalidOperation:raise ValueError('invalid_condition_number') from None
   if not d.is_finite():raise ValueError('invalid_condition_number')
   return d
  a=cast(env[key]);b=cast(env[right] if right else n['value']);op=n['operator']
  return {'lt':lambda:a<b,'le':lambda:a<=b,'eq':lambda:a==b,'ne':lambda:a!=b,'ge':lambda:a>=b,'gt':lambda:a>b}[op]()
 result=walk(node)
 return result,sorted(missing) if result is None else []
