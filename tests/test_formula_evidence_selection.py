import copy
import unittest
from insurance_rag.benefits.formula_evidence import catalog, bind, selection_schema, diagnostics, EvidenceSelectionError
from insurance_rag.illustration.preparation import validate_proposal


class FormulaEvidenceSelectionTests(unittest.TestCase):
    def setUp(self):
        self.source={'source_id':'s','text':'身故保险金等于累计保险费的160％。\n对应比例 160％ 140％ 120％。','source':{'document_version_id':'d','page_number':3}}
        self.catalog=catalog([self.source])

    def proposal(self):
        nid=next(k for k,n in self.catalog['numbers'].items() if n['raw_value']=='160％')
        eid=self.catalog['numbers'][nid]['evidence_id']
        return {'formulas':[{'metric_code':'death_benefit','summary':'按比例给付','time_basis':'年度末','complete':True,
                             'evidence':[{'evidence_id':eid}], 'expression_tree':{'type':'multiply','args':[
                                 {'type':'parameter','name':'cumulative_premium'}, {'type':'constant','number_id':nid,'unit':'dimensionless'}]}}]}

    def test_exact_quotes_and_conversion_are_program_owned(self):
        raw=self.proposal();original=copy.deepcopy(raw);bound=bind(raw,self.catalog)
        p=bound['formulas'][0]
        self.assertEqual(raw,original)
        self.assertEqual(p['constants'][0]['raw_value'],'160％')
        self.assertEqual(p['constants'][0]['value'],'1.6')
        self.assertEqual(p['evidence'][0]['quote'],self.source['text'])
        result=validate_proposal(bound,self.catalog['validation_sources'],'death_benefit')
        self.assertEqual(result['execution_readiness'],'candidate')
        self.assertEqual(result['source_refs'][0]['raw_quote'],self.source['text'])

    def test_adjacent_percent_tokens_are_not_rejected_as_part_of_next_number(self):
        raw=self.proposal();nid=next(k for k,n in self.catalog['numbers'].items() if n['raw_value']=='140％')
        raw['formulas'][0]['expression_tree']['args'][1]['number_id']=nid
        self.assertEqual(validate_proposal(bind(raw,self.catalog),self.catalog['validation_sources'],'death_benefit')['execution_readiness'],'candidate')

    def test_model_cannot_write_quote_or_numeric_value(self):
        raw=self.proposal();raw['formulas'][0]['evidence']=[{'source_id':'s','quote':'改写原文'}]
        with self.assertRaisesRegex(EvidenceSelectionError,'evidence_id_required'):bind(raw,self.catalog)
        raw=self.proposal();raw['formulas'][0]['expression_tree']['args'][1]['value']='999'
        with self.assertRaisesRegex(EvidenceSelectionError,'model_numeric_value_not_allowed'):bind(raw,self.catalog)

    def test_unknown_ids_fail_with_precise_location(self):
        raw=self.proposal();raw['formulas'][0]['expression_tree']['args'][1]['number_id']='foreign-number'
        with self.assertRaises(EvidenceSelectionError) as caught:bind(raw,self.catalog)
        detail=diagnostics(caught.exception,raw,self.catalog,'initial_validation')
        self.assertEqual(detail['number_id'],'foreign-number')
        self.assertEqual(detail['field'],'formulas[0].expression_tree.args[1]')
        self.assertEqual(detail['selected_evidence'][0]['document_id'],'d')
        self.assertEqual(detail['selected_evidence'][0]['page'],3)

    def test_threshold_uses_selected_number_and_boolean_does_not_need_fake_number(self):
        c=catalog([{'source_id':'s','text':'年满18周岁后，等待期后给付。','source':{}}]);nid=next(k for k,n in c['numbers'].items() if n['value']=='18')
        raw={'formulas':[{'conditions':[{'parameter':'entry_age','operator':'ge','number_id':nid,'evidence':{'evidence_id':'e1'}},
                                       {'parameter':'waiting_period_passed','operator':'eq','value':'true','evidence':{'evidence_id':'e1'}}]}]}
        p=bind(raw,c)['formulas'][0]
        self.assertEqual(p['conditions'][0]['value'],'18')
        self.assertEqual(p['conditions'][1]['value'],'true')
        raw['formulas'][0]['conditions'][0]={'parameter':'entry_age','operator':'ge','value':'18'}
        with self.assertRaisesRegex(EvidenceSelectionError,'number_id_required'):bind(raw,c)

    def test_catalog_preserves_ranges_and_does_not_offer_illustration_grid_numbers(self):
        text='原文'+('甲'*395)+'160％。\n1 100000 200000 300000 400000 500000\n十年后给付。'
        c=catalog([{'source_id':'s','text':text,'source':{}}])
        for span in c['spans'].values():
            a,b=span['character_range'];self.assertEqual(span['quote'],text[a:b])
        self.assertIn('160％',[n['raw_value'] for n in c['numbers'].values()])
        self.assertIn('十',[n['raw_value'] for n in c['numbers'].values()])
        self.assertNotIn('100000',[n['raw_value'] for n in c['numbers'].values()])

    def test_schema_does_not_ask_model_to_rewrite_proofs(self):
        schema=selection_schema('death_benefit')
        self.assertEqual(set(schema['$defs']['Citation']['properties']),{'evidence_id'})
        self.assertNotIn('constants',schema['$defs']['FormulaProposal']['properties'])
        self.assertEqual(schema['$defs']['FormulaProposal']['properties']['metric_code']['enum'],['death_benefit'])

    def test_unsupported_chinese_scale_does_not_become_a_fake_partial_token(self):
        c=catalog([{'source_id':'s','text':'一万元，160％和18周岁。','source':{}}])
        self.assertNotIn('一',[n['raw_value'] for n in c['numbers'].values()])

    def test_constant_failure_reports_exact_proof_and_source(self):
        self.source['text']='身故保险金等于累计保险费的160倍。';self.catalog=catalog([self.source]);raw={'formulas':[{'metric_code':'death_benefit','summary':'按比例给付','time_basis':'年度末','complete':True,'evidence':[{'evidence_id':'e1'}],'expression_tree':{'type':'multiply','args':[{'type':'parameter','name':'cumulative_premium'},{'type':'constant','number_id':'n1','unit':'year'}]}}]}
        with self.assertRaises(ValueError) as caught:validate_proposal(bind(raw,self.catalog),self.catalog['validation_sources'],'death_benefit')
        detail=diagnostics(caught.exception,raw,self.catalog,'final_validation')
        self.assertEqual(detail['reason'],'constant_unit_not_supported')
        self.assertEqual(detail['field'],'constants[0]')
        self.assertEqual(detail['raw_value'],'160')
        self.assertEqual(detail['selected_numbers'][0]['source']['page'],3)

    def test_all_colliding_variables_are_named_for_one_bounded_repair(self):
        raw=self.proposal();raw['formulas'][0]['variables']=[
            {'name':name,'meaning':'原文对应金额','unit':'CNY','source_kind':'metric','binding':'base_amount','evidence':[{'evidence_id':'e1'}]}
            for name in ('cash_value','cumulative_premium')]
        with self.assertRaises(ValueError) as caught:validate_proposal(bind(raw,self.catalog),self.catalog['validation_sources'],'death_benefit')
        self.assertEqual(caught.exception.detail['field'],'variables[0].name')
        self.assertEqual([c['name'] for c in caught.exception.detail['collisions']],['cash_value','cumulative_premium'])

    def test_fullwidth_decimal_conversion_keeps_original_numeric_token(self):
        source={'source_id':'s','text':'有效保险金额按基本保险金额的1．0175倍递增。','source':{'document_version_id':'d','page_number':1}}
        c=catalog([source]);nid=next(k for k,n in c['numbers'].items() if n['raw_value']=='1．0175')
        raw=self.proposal();p=raw['formulas'][0];p['metric_code']='effective_amount';p['expression_tree']['args'][0]['name']='base_amount';p['expression_tree']['args'][1]['number_id']=nid
        bound=bind(raw,c)
        self.assertEqual(bound['formulas'][0]['constants'][0]['raw_value'],'1．0175')
        self.assertEqual(validate_proposal(bound,c['validation_sources'],'effective_amount')['execution_readiness'],'candidate')

    def test_percent_cannot_be_selected_as_a_currency_or_year_constant(self):
        raw=self.proposal();raw['formulas'][0]['expression_tree']['args'][1]['unit']='CNY'
        with self.assertRaisesRegex(EvidenceSelectionError,'selected_percent_unit_conflict'):bind(raw,self.catalog)

    def test_selected_occurrence_retains_its_own_origin_instead_of_first_match(self):
        source={'source_id':'page1','text':('重复句。'*120)+'重复句。','source':{'document_version_id':'d','page_number':1},'page_character_range':[0,484]}
        c=catalog([source]);eid=list(c['spans'])[-1];span=c['spans'][eid]
        raw={'formulas':[{'metric_code':'death_benefit','summary':'尚无公式','time_basis':'年度末','complete':False,'missing':['缺少关系'],'evidence':[{'evidence_id':eid}]}]}
        result=validate_proposal(bind(raw,c),c['validation_sources'],'death_benefit')
        ref=result['source_refs'][0]
        self.assertEqual(ref['selection_origin']['character_range'],span['character_range'])
        self.assertEqual(ref['page_character_range'],span['character_range'])
        self.assertGreater(ref['page_character_range'][0],0)

    def test_grouped_money_and_percent_are_whole_tokens(self):
        source={'source_id':'s','text':'额外给付100,000元，比例为1，000％。','source':{}}
        c=catalog([source]);tokens={n['raw_value']:n['value'] for n in c['numbers'].values()}
        self.assertEqual(tokens['100,000'],'100000')
        self.assertEqual(tokens['1，000％'],'10')
        self.assertNotIn('000',tokens)
        raw={'formulas':[{'metric_code':'death_benefit','summary':'固定额外金额','time_basis':'事件时','complete':True,'evidence':[{'evidence_id':'e1'}],
                         'expression_tree':{'type':'constant','number_id':next(k for k,n in c['numbers'].items() if n['raw_value']=='100,000'),'unit':'CNY'}}]}
        source['source']={'document_version_id':'d','page_number':1}
        c=catalog([source])
        self.assertEqual(validate_proposal(bind(raw,c),c['validation_sources'],'death_benefit')['execution_readiness'],'candidate')


if __name__=='__main__':unittest.main()
