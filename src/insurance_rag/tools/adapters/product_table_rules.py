"""Read current-source execution rules without preparing or calculating."""
from pydantic import BaseModel,ConfigDict,Field
from insurance_rag.tools.contracts import result
from insurance_rag.tools.registry import ToolSpec

class ProductRuleRequest(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 bundle_id:str=Field(min_length=1,max_length=200)

class RulesRequest(ProductRuleRequest):
 rule_ids:list[str]|None=Field(default=None,max_length=32)

def current_source_parent(args,context):
 from insurance_rag.ingestion.store import bundle
 parent=bundle(context['app'],context['owner'],args['bundle_id'])
 if parent['content'].get('scope')!='current_source_business':raise ValueError('retired_bundle_requires_migration')
 return parent

def get_product_table_rules(args,context):
 from insurance_rag.ingestion.source_rule_reading import rule_packet
 parent=current_source_parent(args,context);packet=rule_packet(context['app'],parent['content'])
 if args.get('rule_ids'):packet['rules']=[r for r in packet['rules'] if r.get('id',r.get('target',{}).get('target_id')) in args['rule_ids']]
 return result('ok' if packet['rules'] else 'no_evidence',data={'table_rules':packet})

def spec():return ToolSpec('get_product_table_rules','只读当前来源逐目标规则、执行状态、required_inputs和dependencies。先取得依赖的查表/计算data_ref再计算。blocked保留缺口，reference_only仅展示原例；检查通过不等于个人执行就绪。',RulesRequest,get_product_table_rules,version='7')
