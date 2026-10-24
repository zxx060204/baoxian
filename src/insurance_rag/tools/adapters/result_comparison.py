"""Compare already acquired analyses without retrieval, calculation or side effects."""
from pydantic import BaseModel, ConfigDict, Field
from insurance_rag.tools.registry import ToolSpec
from insurance_rag.tools.contracts import result


class Comparison(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    result_refs:list[str]=Field(min_length=2,max_length=8)
    objective_quote:str=Field(min_length=1,max_length=1000)


def compare_results(args,c):
    texts=[m['content'] for m in c['app'].journal.messages(c['run']) if m['role']=='user']
    if not any(args['objective_quote'] in text for text in texts):
        return result('conflict',reason_code='comparison_objective_requires_user_quote')
    groups={};evidence={};numeric=[]
    for ref in args['result_refs']:
        packet=c['app'].packet(ref,c)
        if packet['status'] not in ('ok','partial'):
            continue
        data=packet.get('data',{})
        table=data.get('benefit_table')
        document=data.get('document_analysis')
        if table:
            key=table['bundle_id'];group=groups.setdefault(key,{'bundle_id':key,'product':table['product'],'facts':[]})
            group.update(inputs=table['inputs'],columns=table['columns'],rows=table['rows'],issues=table.get('issues',[]))
        elif document:
            key=document['bundle_id'];group=groups.setdefault(key,{'bundle_id':key,'product':document['product_name'],'facts':[]})
            group['facts'].append({'text':document['text'],'claims':document.get('claims',[]),'result_ref':ref})
        else:
            continue
        for e in packet.get('evidence',[]):evidence[e['evidence_id']]=e
        numeric+=packet.get('numeric_sources',[])
    if len(groups)<2:
        return result('needs_clarification',reason_code='two_product_results_required',missing_fields=['result_refs'])
    products=list(groups.values());tables=[p for p in products if p.get('rows')]
    comparable=len(tables)==len(products) and all(p['inputs']==tables[0]['inputs'] for p in tables)
    metric=lambda col:(col.get('semantic_key') or col['id'],col['unit'])
    shared=set.intersection(*(set(metric(col) for col in p['columns']) for p in tables)) if tables else set()
    cells=[]
    for p in tables:
        for col in p['columns']:
            for row in p['rows']:
                value=row['values'].get(col['id'])
                cells.append({'product':p['product'],'bundle_id':p['bundle_id'],'metric':col['id'],
                              'label':col['name'],'year':row['year'],'value':value,'unit':col['unit'],
                              'comparable':bool(comparable and metric(col) in shared and value is not None)})
    issues=[]
    if tables and not comparable:issues.append('personal_inputs_differ_or_some_product_tables_missing')
    if tables and not shared:issues.append('no_shared_verified_metric')
    value={'objective':args['objective_quote'],'products':products,'cells':cells,
           'numeric_comparable':comparable and bool(shared),'issues':issues,
           'scope':'personalized' if tables else 'general_facts','ranking_performed':False}
    return {**result('partial' if issues else 'ok',data={'product_comparison':value},evidence=list(evidence.values())),
            'numeric_sources':numeric}


def spec():
    return ToolSpec('compare_analysis_results','只比较已取得的多款资料库/上传产品结果；不检索、不计算、不保存画像。result_refs为原文分析或已生成利益表。一般事实比较不要求画像；数值只在用户条件、指标与单位一致且有值时标记可比，不作收益排名。',Comparison,compare_results)
