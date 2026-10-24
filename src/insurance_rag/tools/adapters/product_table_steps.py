"""Explicit current-source lookup, rule evaluation and referenced table assembly."""
from decimal import Decimal
from pydantic import Field
from insurance_rag.tools.contracts import result
from insurance_rag.tools.registry import ToolSpec
from insurance_rag.agent.store import Conflict
from insurance_rag.ingestion.personal_inputs import IllustrationInputs
from .product_table_rules import ProductRuleRequest,current_source_parent

class ValuesRequest(ProductRuleRequest):
    rule_id: str = Field(min_length=1, max_length=200)
    inputs: IllustrationInputs
    input_quotes: dict[str, str] = Field(default_factory=dict)



class CalculateRequest(ValuesRequest):
    dependency_refs: list[str] = Field(default_factory=list, max_length=32)



class TableRequest(ProductRuleRequest):
    value_refs: list[str] = Field(min_length=1, max_length=128, description='传查表/计算工具返回的最外层data_ref；也接受当前条件下已取得的table-values ID。不得自写数值。')
    rule_ids: list[str] | None = Field(default=None, max_length=128, description='仅在需要显示尚未取得的列时明确列出；默认只组装传入的年度列。')



class ExampleRequest(ProductRuleRequest):
    case_id: str | None = None
    table_id: str | None = None
    row_start: int = Field(default=0,ge=0)
    row_count: int = Field(default=20,ge=1,le=200)
    column_start: int = Field(default=0,ge=0)
    column_count: int = Field(default=20,ge=1,le=200)
    rule_id: str | None = Field(default=None, min_length=1, max_length=200)



def _inputs(args, c):
    values = IllustrationInputs.model_validate(args['inputs']).model_dump(exclude_none=True)
    if Decimal(values['annual_premium_input'])<=0 or not Decimal(values['annual_premium_input']).is_finite():raise ValueError('invalid_annual_premium')
    if getattr(c['app'], 'journal', None) is not None and c['run'].get('run_id'):
        from insurance_rag.ingestion.personal_inputs import extract
        from insurance_rag.tools.adapters.profile import user_context_messages
        # Trial conditions belong to the task; retain prior statements and redactions.
        messages = user_context_messages(c, include_scenarios=True)
        known = {}
        for text in messages:
            found, _ = extract(text); known.update(found)
        known['annual_premium_input'] = known.pop('annual_premium', None)
        if known.get('scenario') == 'normal_payment_no_changes_no_loans':
            known['scenario'] = 'normal_annual_payment_no_changes_no_loans'
        missing = []
        import re
        for key, value in values.items():
            if value is None or known.get(key) == value: continue
            quote = args.get('input_quotes', {}).get(key, '')
            supported = bool(quote and any(quote in m for m in messages))
            if key in ('entry_age', 'sex', 'payment_years', 'annual_premium_input', 'end_year', 'scenario'):
                found, _ = extract(quote)
                field = 'annual_premium' if key == 'annual_premium_input' else key
                expected = 'normal_payment_no_changes_no_loans' if key == 'scenario' else value
                supported = supported and found.get(field) == expected
            elif key=='coverage_period':
                supported=supported and str(value) in quote
            elif type(value) is bool:
                terms = {'waiting_period_passed': ('等待期已过', '等待期未过'), 'adult_anniversary_passed': ('已过成年周年日', '未过成年周年日'), 'paid_anniversary_passed': ('已过交费期满周年日', '未过交费期满周年日'), 'benefit_anniversary_eligible': ('已核验符合给付周年日期间', '已核验不符合给付周年日期间'), 'no_special_surrender_provisions': ('无特殊退保约定','有特殊退保约定')}
                yes, no = terms[key]; supported = supported and (yes if value else no) in quote
            else:
                terms = {'base_amount_input': '基本保额|基本保险金额', 'annuity_start_year': '首次领取.*保单年度|起领年度', 'annuity_end_year': '最后领取.*保单年度|末次领取年度', 'annuity_start_age': '领取年龄', 'maturity_year': '满期.*保单年度|满期年度', 'pension_annuity_rate': '合同领取比例|约定领取比例'}
                supported = supported and bool(re.search(terms.get(key, r'(?!)'), quote)) and bool(re.search(r'(?<![\d.])' + re.escape(str(value)) + r'(?![\d.])', quote))
            if not supported: missing.append(key)
        if missing: return None, result('needs_clarification', reason_code='explicit_inputs_required', missing_fields=missing)
    return values, None




def validate_packet(packet,c):
 from insurance_rag.ingestion.source_execution import validate_result,load_execution
 from insurance_rag.ingestion.store import bundle
 for key in ('table_values','benefit_table'):
  value=packet.get('data',{}).get(key)
  if value is None:continue
  if value.get('_audit',{}).get('scope')!='current_source_execution':raise Conflict('retired_execution_result')
  content=bundle(c['app'],c['owner'],value['bundle_id'])['content'];validate_result(value,content,app=c['app']);load_execution(c['app'],content)

def personal(args,c,operation):
 from insurance_rag.ingestion.source_execution import execute_target
 parent=current_source_parent(args,c)
 if not parent['content'].get('result_refs',{}).get('execution'):return result('unavailable',reason_code='current_source_execution_not_prepared')
 inputs,error=_inputs(args,c)
 if error:return error
 dependencies={}
 for ref in args.get('dependency_refs',[]):
  item=c['app'].packet(ref,c).get('data',{}).get('table_values')
  if not item:raise ValueError('dependency_values_required')
  if item['rule_id'] in dependencies:raise Conflict('duplicate_dependency_reference')
  dependencies[item['rule_id']]=item
 return execute_target(c['app'],parent['content'],args,inputs,dependencies,operation=operation)

def query_product_values(args,c):return personal(args,c,'lookup')
def calculate_product_values(args,c):return personal(args,c,'evaluate')

def build_benefit_table(args,c):
 from insurance_rag.ingestion.source_execution import assemble
 parent=current_source_parent(args,c)
 if not parent['content'].get('result_refs',{}).get('execution'):return result('unavailable',reason_code='current_source_execution_not_prepared')
 packets=[c['app'].packet(ref,c).get('data',{}).get('table_values') for ref in args['value_refs']]
 if any(p is None for p in packets):raise ValueError('verified_values_required')
 return assemble(c['app'],parent['content'],args,packets)

def read_product_example(args,c):
 from insurance_rag.ingestion.source_rule_reading import example_packet
 parent=current_source_parent(args,c)
 packet=example_packet(c['app'],parent['content'],**{k:args[k] for k in ('case_id','table_id','row_start','row_count','column_start','column_count','rule_id') if k in args})
 return result('ok' if packet.get('cases') or packet.get('case') else 'no_evidence',data={'product_example':packet})

def specs():
    return [ToolSpec('query_product_values', '按新来源执行定义精确取得一个个人查表指标；rule_id使用get_product_table_rules中的目标ID。，按end_year批量返回。status=ok表示已取得数值；循环小数保存在exact_fractions，display_values可展示。取得所需列后直接把data_ref交给build_benefit_table。金额输入须用户明确，预算不等于保费。', ValuesRequest, query_product_values, timeout=120, version='3'),
            ToolSpec('calculate_product_values', '按新来源执行定义计算Agent指定的一个指标；先读取规则及变量绑定。依赖必须用dependency_refs显式传入；输入必须来自用户明确条件。缺输入、数据或不满足适用条件时返回具体原因，不隐式查表或生成整表。', CalculateRequest, calculate_product_values, timeout=120, version='3'),
            ToolSpec('build_benefit_table', '按value_refs对齐新来源已有列并组装年度表。不会查表、计算公式或补值；金额整数展示；缺列保持缺口；不能传原例或模型自写金额。', TableRequest, build_benefit_table, timeout=120, version='3'),
            ToolSpec('read_product_example', '按需读取说明书原例，保留原条件、原表头和原数值。当前来源先返回目录；传case_id及可选table_id、行列窗口读取完整上下文和原值，按has_more继续读取，附近小表只表示空间关系。没有提取到规则时可只传bundle_id。非当前用户结果，不可用于个人计算依赖。original_page_only表示仅定位原页，表格尚未结构化。特殊情况咨询使用product_documents的analyze。', ExampleRequest, read_product_example, timeout=120, version='3')]
