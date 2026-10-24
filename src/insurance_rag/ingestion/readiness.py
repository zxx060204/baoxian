"""Read-only status projection shared by Agent, API and the workbench."""
from copy import deepcopy

CURRENT_SCOPE = 'current_source_business'
TERMINAL_STATES = ('ready', 'partial', 'failed')
EXECUTION_NOT_PREPARED = 'current_source_execution_not_prepared'


def preparation_result(content):
    return deepcopy(content.get('result'))


def task_summary(row):
    c=row['content'];prepared=preparation_result(c) or {};caps=prepared.get('capabilities',{})
    complete=prepared.get('scope')=='current_source_preparation'
    return {'job_id':row['id'],'state':row['state'],'stage':c['stage'],'bundle_id':prepared.get('bundle_id') if complete else None,
            'product':prepared.get('identity',{}).get('product_name'),'error':c.get('error_code'),
            'scope':'current_source_preparation','status_scope':'business_preparation',
            'source_ready':bool(complete and prepared.get('mapping')),
            'qa_ready':complete and caps.get('document_qa',{}).get('status')=='available',
            'personal_calculation_ready':complete and caps.get('illustration',{}).get('ready_count',0)>0,
            'capabilities':caps,'downstream':prepared.get('downstream',{}),'errors':prepared.get('errors',{}),
            'published':False,'business_ready':bool(complete and prepared.get('bundle_id')),
            'note':'上传后自动准备本人资料包；各项能力和缺口分别报告。'}


def current_capabilities(app, content):
    """Project bound artifacts, never prepare modules or mutate a published bundle."""
    if content.get('scope') != CURRENT_SCOPE:
        raise ValueError('current_source_bundle_required')
    from .source_package import read_bound
    from insurance_rag.parsing.common import read_json
    package = read_json(read_bound(app.root, content['source_package_ref']))
    package_id = package['package_id']
    if content.get('source_package_id',package_id) != package_id:
        raise ValueError('capability_source_package_changed')
    refs = content.get('result_refs', {})
    def bound_result(name, scope):
        value=read_json(read_bound(app.root,refs[name]))
        if value.get('scope')!=scope or value.get('source_package_ref')!=content['source_package_ref'] or value.get('source_package_id')!=package_id:
            raise ValueError('capability_result_source_changed')
        return value
    capabilities = {'document_retrieval': {'status': 'not_prepared'},
                    'document_qa': {'status': 'not_prepared'},
                    'source_tables': {'status': 'not_imported'},
                    'rule_extraction': {'status': 'not_prepared'},
                    'source_examples': {'status': 'not_prepared'},
                    'formula_execution': {'status': 'not_prepared', 'reason_code': EXECUTION_NOT_PREPARED},
                    'illustration': {'status': 'not_prepared', 'reason_code': EXECUTION_NOT_PREPARED}}
    if refs.get('index'):
        from .source_index import POLICY as INDEX_POLICY
        indexed = bound_result('index','current_source_index')
        if indexed.get('policy')!=INDEX_POLICY:raise ValueError('capability_index_policy_changed')
        if indexed['state']=='ready' and (not indexed.get('index') or not indexed.get('embedding_binding') or indexed.get('integrity')!='matched' or indexed.get('chunk_count',0)<1):raise ValueError('capability_index_not_ready')
        read_bound(app.root,indexed['chunks_ref'])
        state = 'available' if indexed['state'] == 'ready' else indexed['state']
        capabilities['document_retrieval'] = {'status': state, 'chunk_count': indexed['chunk_count']}
        capabilities['document_qa'] = {'status': state, 'scope': 'source_answer_only',
                                       'availability_scope': 'prepared_artifacts_not_live_service_health'}
    if content.get('tables'):
        tables = bound_result('tables','literal_source_tables_import')
        if tables != content['tables']:raise ValueError('capability_table_binding_changed')
        capabilities['source_tables'] = {k: tables[k] for k in
            ('table_batch_id', 'table_count', 'cell_count', 'record_count')}
        capabilities['source_tables'].update(status=tables['state'], scope='literal_structure_only')
    if refs.get('examples'):
        from .source_examples import POLICY as EXAMPLES_POLICY
        examples = bound_result('examples','current_source_examples')
        if examples.get('policy')!=EXAMPLES_POLICY:raise ValueError('capability_example_policy_changed')
        capabilities['source_examples'] = {'status': 'available' if examples['cases'] else 'not_applicable',
                                            'case_count': len(examples['cases']), 'scope': 'literal_context_only'}
    if refs.get('rules'):
        from .source_rules import POLICY as RULES_POLICY
        rules = bound_result('rules','current_source_rules')
        if rules.get('versions',{}).get('policy')!=RULES_POLICY:raise ValueError('capability_rule_policy_changed')
        capabilities['rule_extraction'] = {'status': 'prepared', 'target_count': rules['target_count'],
            'rule_count': sum('rule' in r for r in rules['rules']),
            'failed_count': sum(r['state'] in ('invalid_output', 'provider_error') for r in rules['rules']),
            'methods': {method: sum(r.get('rule', {}).get('type') == method for r in rules['rules'])
                        for method in ('formula', 'exact_table_lookup', 'illustration_reference')},
            'scope': 'acquisition_guidance_only', 'execution_readiness': 'not_evaluated'}
    if refs.get('execution'):
        from .source_execution import load_execution
        execution,_=load_execution(app,content)
        targets=execution['targets'];ready=[t for t in targets if t['state']=='ready'];blocked=[t for t in targets if t['state']=='blocked']
        state='available' if ready and not blocked else 'partial' if ready else 'not_applicable' if not targets else 'blocked'
        summary={'status':state,'scope':'current_source_execution','ready_count':len(ready),'blocked_count':len(blocked),'reference_count':sum(t['state']=='reference_only' for t in targets),'targets':[{k:t[k] for k in ('id','name','method','state','required_inputs','dependencies','issues')} for t in targets]}
        capabilities['formula_execution']=summary
        capabilities['illustration']={**summary,'execution_readiness':'per_target_not_whole_product'}
    return capabilities
