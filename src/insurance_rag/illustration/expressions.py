"""Typed, bounded exact expression execution; no model code or dynamic functions."""
from decimal import Decimal,localcontext,InvalidOperation
from fractions import Fraction
from .logic import validate_condition,evaluate_condition

class MissingExpression(ValueError):pass

def numeric(value):
 if type(value) is bool or len(str(value))>200:raise ValueError('invalid_expression_number')
 try:d=Decimal(str(value))
 except InvalidOperation:raise ValueError('invalid_expression_number') from None
 if not d.is_finite() or abs(d.adjusted())>200 or d.as_tuple().exponent < -200:raise ValueError('invalid_expression_number')
 return Fraction(d)

POLICY='typed-expressions-v2'
# Intermediate dimensions can cancel across nested products/quotients.
DIMENSIONS={'dimensionless':(0,0),'CNY':(1,0),'year':(0,1),'CNY/year':(1,-1)}
MAX_NODES=2000
MAX_DEPTH=64
MAX_ARGS=128

def inspect_expression(tree,units):
 dependencies=set();nodes=0
 def dimension(unit):
  if unit not in DIMENSIONS:raise ValueError('unknown_numeric_variable')
  return DIMENSIONS[unit]
 def walk(n,depth=0):
  nonlocal nodes
  nodes+=1
  if nodes>MAX_NODES or depth>MAX_DEPTH or not isinstance(n,dict):raise ValueError('expression_tree_limit')
  kind=n.get('type')
  if kind=='parameter' and set(n)=={'type','name'}:
   if n['name'] not in units:raise ValueError('unknown_numeric_variable')
   dependencies.add(n['name']);return dimension(units[n['name']])
  if kind=='constant' and {'type','value'}<=set(n) and set(n)<= {'type','value','unit'}:
   numeric(n['value']);unit=n.get('unit','dimensionless')
   if unit not in DIMENSIONS:raise ValueError('invalid_constant_unit')
   return dimension(unit)
  if kind=='unavailable' and set(n)=={'type','reason'} and isinstance(n['reason'],str):return None
  if kind=='year_count' and set(n)=={'type','arg'}:
   if walk(n['arg'],depth+1)!=DIMENSIONS['year']:raise ValueError('year_count_requires_year_unit')
   return DIMENSIONS['dimensionless']
  if kind in ('negate','abs') and set(n)=={'type','arg'}:return walk(n['arg'],depth+1)
  if kind in ('add','sum','subtract','multiply','product','divide','min','max','power') and set(n)=={'type','args'} and isinstance(n['args'],list):
   arity=len(n['args'])
   if kind in ('subtract','divide','power'):
    if arity!=2:raise ValueError('binary_operator_requires_two_arguments')
   elif not 1<=arity<=MAX_ARGS:raise ValueError('invalid_expression_arity')
   dimensions=[walk(c,depth+1) for c in n['args']];present=[u for u in dimensions if u is not None]
   if not present:return None
   if kind in ('add','sum','subtract','min','max'):
    if len(set(present))!=1:raise ValueError('expression_unit_mismatch')
    return present[0]
   if kind in ('multiply','product'):return tuple(sum(d[i] for d in present) for i in (0,1))
   if kind=='divide':
    a,b=dimensions
    if a is None or b is None:return a or b
    return tuple(a[i]-b[i] for i in (0,1))
   a,b=dimensions
   if a is None or b is None:return a or b
   if a!=DIMENSIONS['dimensionless'] or b not in (DIMENSIONS['dimensionless'],DIMENSIONS['year']):raise ValueError('invalid_power_units')
   return DIMENSIONS['dimensionless']
  if kind=='piecewise' and set(n)=={'type','branches','otherwise'} and isinstance(n['branches'],list) and 0<len(n['branches'])<=64:
   result_units=[]
   for branch in n['branches']:
    if not isinstance(branch,dict) or set(branch)!= {'condition','value'}:raise ValueError('invalid_expression_branch')
    dependencies.update(validate_condition(branch['condition'],units));result_units.append(walk(branch['value'],depth+1))
   result_units.append(walk(n['otherwise'],depth+1));present={u for u in result_units if u is not None}
   if len(present)>1:raise ValueError('branch_unit_mismatch')
   return next(iter(present),None)
  raise ValueError('unsupported_expression_node')
 result=walk(tree)
 if result is None:return None,dependencies
 names={v:k for k,v in DIMENSIONS.items()}
 if result not in names:raise ValueError('unsupported_compound_unit')
 return names[result],dependencies

def evaluate(tree,env,units,expected_unit):
 unit,_=inspect_expression(tree,units)
 if unit!=expected_unit:raise ValueError('result_unit_mismatch')
 trace=[]
 def walk(n):
  kind=n['type']
  if kind=='parameter':
   if n['name'] not in env:raise MissingExpression('missing_variable:'+n['name'])
   value=env[n['name']]
   result=value if isinstance(value,Fraction) else numeric(value)
  elif kind=='constant':result=numeric(n['value'])
  elif kind=='unavailable':raise MissingExpression(n['reason'])
  elif kind=='year_count':
   result=walk(n['arg'])
   if result.denominator!=1 or not 0<=result<=120:raise ValueError('invalid_complete_year_count')
  elif kind=='piecewise':
   chosen=n['otherwise'];branch_index=None
   for i,branch in enumerate(n['branches']):
    match,missing=evaluate_condition(branch['condition'],env,units)
    if match is None:raise MissingExpression('missing_condition_inputs:'+','.join(missing))
    if match:chosen=branch['value'];branch_index=i;break
   trace.append({'operation':'branch','selected':branch_index});result=walk(chosen)
  elif kind in ('negate','abs'):
   value=walk(n['arg']);result=-value if kind=='negate' else abs(value)
  else:
   values=[walk(c) for c in n['args']]
   if kind in ('add','sum'):result=sum(values,Fraction(0))
   elif kind in ('multiply','product'):
    result=Fraction(1)
    for value in values:result*=value
   elif kind in ('min','max'):result=(min if kind=='min' else max)(values)
   else:
    a,b=values
    if kind=='power':
     if b.denominator!=1 or not -120<=b<=120:raise ValueError('invalid_integer_exponent')
     result=a**int(b)
    else:result=a-b if kind=='subtract' else a/b
  if len(str(abs(result.numerator)))>1000 or len(str(result.denominator))>1000:raise ValueError('expression_number_limit')
  trace.append({'operation':kind,'exact_fraction':str(result)});return result
 value=walk(tree)
 if value<0:raise ValueError('negative_final_value')
 denominator=value.denominator;places=0
 for factor in (2,5):
  power=0
  while denominator%factor==0:denominator//=factor;power+=1
  places=max(places,power)
 decimal=None
 if denominator==1:
  with localcontext() as ctx:
   ctx.prec=max(50,len(str(abs(value.numerator)))+places+10);decimal=format(Decimal(value.numerator)/Decimal(value.denominator),'f')
 return {'value':decimal,'exact_fraction':str(value),'steps':trace}
