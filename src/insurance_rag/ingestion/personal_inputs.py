"""Explicit personal plan parameters shared by current-source tools."""
from pydantic import BaseModel,ConfigDict,Field,field_validator
from typing import Literal
from decimal import Decimal

class IllustrationInputs(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 entry_age:int=Field(ge=0,le=120)
 payment_years:int=Field(ge=1,le=120)
 annual_premium_input:str=Field(min_length=1,max_length=40,pattern=r'^\d+(?:\.\d+)?$')
 end_year:int=Field(ge=1,le=120)
 scenario:Literal['normal_annual_payment_no_changes_no_loans']
 sex:Literal['male','female']|None=None
 waiting_period_passed:bool|None=None
 adult_anniversary_passed:bool|None=None
 paid_anniversary_passed:bool|None=None
 base_amount_input:str|None=Field(default=None,min_length=1,max_length=40,pattern=r'^\d+(?:\.\d+)?$')
 annuity_start_year:int|None=Field(default=None,ge=1,le=120)
 annuity_start_age:int|None=Field(default=None,ge=0,le=120)
 annuity_end_year:int|None=Field(default=None,ge=1,le=120)
 maturity_year:int|None=Field(default=None,ge=1,le=120)
 pension_annuity_rate:str|None=Field(default=None,min_length=1,max_length=40)
 benefit_anniversary_eligible:bool|None=None
 coverage_period:str|None=Field(default=None,max_length=100)
 no_special_surrender_provisions:bool|None=None
 @field_validator('annual_premium_input','base_amount_input')
 @classmethod
 def positive_amount(cls,value):
  if value is not None and Decimal(value)<=0:raise ValueError('positive_amount_required')
  return value

import re
from decimal import Decimal
def chinese_number(text):
 if re.fullmatch(r'\d+(?:\.\d+)?万',text):return Decimal(text[:-1])*10000
 if re.fullmatch(r'\d+(?:\.\d+)?',text):return Decimal(text)
 digits={'零':0,'〇':0,'一':1,'二':2,'两':2,'三':3,'四':4,'五':5,'六':6,'七':7,'八':8,'九':9};total=part=current=0
 for c in text:
  if c in digits:current=digits[c]
  elif c in '十百千':part+=(current or 1)*{'十':10,'百':100,'千':1000}[c];current=0
  elif c=='万':total+=(part+current or 1)*10000;part=current=0
  else:raise ValueError('unsupported_number_expression')
 return Decimal(total+part+current)

NUM=r'[0-9零〇一二两三四五六七八九十百千万]+(?:\.[0-9]+)?'
def extract(text):
 values={};quotes={}
 patterns={'entry_age':r'(?:投保年龄(?:为)?|今年|年龄(?:为)?|男性|女性|男|女)\s*('+NUM+r')\s*岁',
  'payment_years':r'(?:交费期(?:为)?|缴费期(?:为)?|交费|缴费|交|缴)\s*('+NUM+r')\s*年',
  'end_year':r'(?:演示|测算|展示)(?:期限|期间)?(?:为|至|到)?\s*('+NUM+r')\s*年',
  'annual_premium':r'(?:每年交|每年缴|年交保费|年交|年缴)\s*(?:调整为|变更为|改为|为|是)?\s*('+NUM+r')\s*(万元|万|元)?'}
 for field,pattern in patterns.items():
  matches=[m for m in re.finditer(pattern,text) if not re.search(r'不(?:是|要)|并非',text[max(0,m.start()-4):m.start()])]
  if field=='payment_years':
   suffix=r'(?<![第0-9零〇一二两三四五六七八九十百千万])('+NUM+r')\s*年(?:交清|缴清|交|缴)'
   matches += [m for m in re.finditer(suffix,text) if not re.search(r'不(?:是|要)|并非',text[max(0,m.start()-4):m.start()])]
   matches.sort(key=lambda m:m.start())
  if not matches and field=='entry_age':
   ages=list(re.finditer('('+NUM+r')\s*岁',text))
   if len(ages)==1 and not re.search(r'到|至|不是',text[max(0,ages[0].start()-3):ages[0].start()]):matches=ages
  if matches:
   m=matches[-1]
   if field=='annual_premium' and (re.search(r'预算|最多|不超过|考虑',text[max(0,m.start()-6):m.start()]) or not m[2] and not m[1].endswith('万')):continue
   n=chinese_number(m[1]);n=n*(10000 if field=='annual_premium' and (m[2] or '') in ('万','万元') else 1)
   if field!='annual_premium' and n!=int(n):continue
   values[field]=format(n,'f') if field=='annual_premium' else int(n);quotes[field]=m[0]
 sex=[m for m in re.finditer(r'男性|女性|男|女',text) if not re.search(r'不是|并非',text[max(0,m.start()-3):m.start()])]
 if sex:values['sex']='male' if sex[-1][0].startswith('男') else 'female';quotes['sex']=sex[-1][0]
 if re.search(r'正常(?:年交|年缴|交费|缴费)',text) and re.search(r'无贷款|没有贷款',text) and re.search(r'无变更|没有变更|无贷款(?:或|和|及)变更',text):
  values['scenario']='normal_payment_no_changes_no_loans';quotes['scenario']=text
 return values,quotes
