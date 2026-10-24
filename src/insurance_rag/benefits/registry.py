"""Business classification only. Product formulas never live in this registry."""
import re

VERSION='common-benefit-concepts-v3-composition'
CONCEPTS={
 'policy_year':{'title':'保单年度','family':'foundation'},
 'year_end_age':{'title':'被保险人年度末年龄','family':'foundation'},
 'annual_premium':{'title':'当年度保险费','family':'foundation'},
 'cumulative_premium':{'title':'累计保险费','family':'foundation'},
 'cash_value':{'title':'现金价值','family':'cash_value'},
 'effective_amount':{'title':'有效保险金额','family':'coverage_amount'},
 'base_amount':{'title':'基本保险金额','family':'coverage_amount','role':'summary_parameter'},
 'death_benefit':{'title':'身故保险金','family':'risk_benefit'},
 'disability_benefit':{'title':'全残保险金','family':'risk_benefit'},
 'accidental_disability_benefit':{'title':'意外全残保险金','family':'risk_benefit'},
 'death_or_disability_benefit':{'title':'身故或者全残给付','family':'risk_benefit'},
 'survival_benefit':{'title':'生存保险金','family':'periodic_survival'},
 'pension_annuity':{'title':'养老年金','family':'periodic_survival'},
 'maturity_benefit':{'title':'满期保险金','family':'maturity'},
 'dividend':{'title':'当年度红利','family':'bonus'},
 'accumulated_dividend':{'title':'累计红利','family':'bonus'},
 'paid_up_addition_amount':{'title':'交清增额保险金额','family':'bonus'},
 'paid_up_addition_cash_value':{'title':'交清增额现金价值','family':'bonus'},
 'account_value':{'title':'保单账户价值','family':'account'},
 'initial_charge':{'title':'初始费用','family':'account'},
 'cost_of_insurance':{'title':'保障成本','family':'account'},
 'persistency_bonus':{'title':'持续奖金','family':'account'},
}
ALIASES={
 '年龄（年末）':'year_end_age','年龄(年末)':'year_end_age',
 '现金价值':'cash_value','当年度末现金价值':'cash_value','有效保额':'effective_amount','当年保障额度':'effective_amount','当年度有效保险金额':'effective_amount',
 '养老保险金':'pension_annuity','养老年金给付':'pension_annuity','年金':'survival_benefit',
 '满期金':'maturity_benefit','满期生存保险金':'maturity_benefit','当年度末红利':'dividend','当年红利':'dividend',
 '累积红利':'accumulated_dividend','个人账户价值':'account_value',
 '身故或全残保险金':'death_or_disability_benefit','身故或者全残保险金':'death_or_disability_benefit','当年度末身故或者全残给付':'death_or_disability_benefit',
 '身故保险金给付':'death_benefit','当年度末身故给付':'death_benefit','当年度末身故给付金额':'death_benefit','养老年金给付':'pension_annuity',
 '意外全残保险金':'accidental_disability_benefit','风险保障费':'cost_of_insurance','持续奖励':'persistency_bonus',
}
for key,value in CONCEPTS.items():ALIASES[value['title']]=key
for key,meaning in {
 'survival_benefit':'保险期间内按约定日期多次给付的生存类现金流；只在保险期满给付的生存保险金归满期，不因名称包含生存就归本项。',
 'maturity_benefit':'仅保险期间届满给付的基础合同金额，可能称为满期保险金、生存保险金或生存给付。',
 'pension_annuity':'领取阶段按约定频率给付的养老年金；累计领取金额与当期给付不可合并。',
 'cash_value':'基础合同现金价值；不包含交清增额现金价值，合计金额须标明组成。',
 'paid_up_addition_cash_value':'交清增额部分现金价值，不等于基础合同现金价值。',
 'paid_up_addition_amount':'购买交清增额保险累计增加的保额；不是基础合同的有效保额。',
 'death_or_disability_benefit':'同一基础合同身故/全残金额；额外事故给付、增额部分与总额不能混入。',
 'dividend':'当期红利，不等于累计红利、红利分配政策、交清增额保额或其现金价值。',
}.items():CONCEPTS[key]['meaning']=meaning

def compact(text):return re.sub(r'\s+','',text).strip('：:。、')

def concept_for(name):
 key=compact(name)
 if key in ALIASES:return ALIASES[key]
 # Presentation prefixes/suffixes do not create a new monetary benefit.
 key=re.sub(r'^(?:当年度末|当年度|年度末|当年末|当年)','',key)
 if key=='生存给付':return None  # Maturity-only vs periodic needs product context.
 key=re.sub(r'给付(?:金额)?$','保险金',key) if key.endswith(('给付','给付金额')) else key
 return ALIASES.get(key)


def amount_component(name, context=''):
 """Use qualified names/headers or a local definition, never a remote mention."""
 name=compact(name);text=compact(context)
 if re.search('总利益|总给付|合计',name):return 'total'
 if '交清增额' in name:return 'paid_up_addition'
 # Merged column parent labels are explicit qualifiers.
 if ' / ' in context:
  parents=compact(' / '.join(context.split(' / ')[:-1]))
  if re.search('总利益|总给付|合计',parents):return 'total'
  if '交清增额' in parents:return 'paid_up_addition'
 # A definition such as "交清增额保险的身故保险金" qualifies its subject.
 if name and re.search(r'交清增额(?:保险|部分|利益)?(?:的|对应的|部分)?[^。；：:]{0,20}'+re.escape(name),text):return 'paid_up_addition'
 if name and re.search(re.escape(name)+r'.{0,160}(?:之和|合计)',text) and '保证利益演示' in text and '红利利益演示' in text:return 'total'
 return 'base_contract'

def column_name(text):
 parts=[p.strip() for p in text.split(' / ') if p.strip()]
 scenarios={'保证利益演示','红利利益演示','最低保证利益演示','万能结息利益演示','低','中','高','低档','中档','高档'}
 while len(parts)>1 and compact(parts[-1]) in scenarios:parts.pop()
 return parts[-1] if parts else ''

def role_for(code):
 if code in ('policy_year','year_end_age'):return 'annual_column'
 if code=='base_amount':return 'summary_parameter'
 if code in ('attained_age','surrender_value'):return 'internal_dependency'
 if code=='dividend':return 'reference'
 return CONCEPTS.get(code,{}).get('role','annual_column')
