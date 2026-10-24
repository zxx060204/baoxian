"""Read current extraction results without falling back to historical illustration profiles."""
from .source_rules import validate_rules
from .source_examples import validate_examples,read_example

def rule_packet(app,content,*,include_evidence=False):
 ref=content.get('result_refs',{}).get('rules')
 if not ref:return {'product':{'bundle_id':content['bundle_id'],'name':content['identity']['product_name']},'rules':[],'state':'not_prepared','execution_readiness':'not_evaluated'}
 result=validate_rules(app.root,content['source_package_ref'],ref)
 if content.get('result_refs',{}).get('execution'):
  from .source_execution import load_execution
  execution,_=load_execution(app,content)
  targets=[]
  for item in execution['targets']:
   value={k:item[k] for k in ('id','name','method','state','unit','required_inputs','dependencies','issues','assumption','required_assumptions','lookup_role','role') if k in item}
   value['source_column']=item.get('source_column')
   if item.get('bindings'):value['bindings']=[{k:b[k] for k in ('name','kind','key','reason') if k in b} for b in item['bindings']]
   if item.get('formula'):
    f=item['formula'];value['formula']={k:f[k] for k in ('summary','expression_tree','output_unit','applicability','time_basis') if k in f}
    value['formula']['variables']=[{k:v[k] for k in ('name','meaning','unit','source_kind','binding','expression_tree') if v.get(k) is not None} for v in f['variables']]
    value['formula']['conditions']=[{k:v for k,v in c.items() if k!='evidence' and v is not None} for c in f.get('conditions',[])]
   if include_evidence:
    original=next((r for r in result['rules'] if r['target']['target_id']==item['id']),None)
    if original:value['original_rule']=original.get('rule',{})
   targets.append(value)
  return {'product':{'bundle_id':content['bundle_id'],'name':content['identity']['product_name']},'rules':targets,'state':execution['state'],'scope':'current_source_execution','execution_readiness':'per_target','source_package_id':result['source_package_id']}
 return {'product':{'bundle_id':content['bundle_id'],'name':content['identity']['product_name']},'rules':result['rules'],'source_package_id':result['source_package_id'],'state':'extraction_prepared','execution_readiness':'not_evaluated','scope':'acquisition_rules_only','availability_checks':False}

def example_packet(app,content,*,case_id=None,table_id=None,row_start=0,row_count=20,column_start=0,column_count=20,rule_id=None):
 ref=content.get('result_refs',{}).get('examples')
 if not ref:return {'bundle_id':content['bundle_id'],'cases':[],'state':'not_prepared','personalized':False}
 result=validate_examples(app.root,content['source_package_ref'],ref);cases=result['cases']
 if rule_id is not None:cases=[c for c in cases if any(col['column_id']==rule_id for col in c['columns'])]
 if case_id:
  if not any(c['case_id']==case_id for c in cases):raise ValueError('case_not_in_current_source')
  return {'bundle_id':content['bundle_id'],'case':read_example(app.root,content['source_package_ref'],ref,case_id,table_id=table_id,row_start=row_start,row_count=row_count,column_start=column_start,column_count=column_count),'state':'source_example','personalized':False}
 directory=[]
 for c in cases:
  neighbors=[]
  for n in c['nearby_tables']:
   neighbors.append({'position':n['position'],'relative_to_table_id':n['relative_to_table_id'],'association':n['association'],'read_ref':n['read_ref'],'cells':[{'row_index':cell['row_index'],'column_index':cell['column_index'],'raw_text':cell['raw_text']} for cell in n['cells']],'cells_included':n['cells_included']})
  directory.append({'case_id':c['case_id'],'document_version_id':c['document_version_id'],'tables':[{'table_id':t['table_id'],'page_number':t['page_number'],'row_count':t['row_count'],'column_count':t['column_count']} for t in c['tables']],'columns':c['columns'],'nearby_tables':neighbors,'context_preview':[{'context_id':x['context_id'],'raw_text':x['raw_text'][:600],'truncated':len(x['raw_text'])>600} for x in c['contexts'][:4]],'context_count':len(c['contexts']),'association':c['association'],'next_read':'provide_case_id_for_full_context_and_cell_window'})
 return {'bundle_id':content['bundle_id'],'state':'directory','cases':directory,'personalized':False,'note':'原文案例和空间上下文；不推断适用、不缩放、不插值、不补年。'}
