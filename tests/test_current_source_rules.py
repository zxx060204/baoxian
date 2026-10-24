import copy, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from insurance_rag.benefits.formula_evidence import catalog
from insurance_rag.ingestion.source_formula_checks import validate_candidate
from insurance_rag.ingestion.source_rules import extract_target, prepare_rules
from insurance_rag.ingestion.source_examples import prepare_examples, read_example
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.parsing.common import write_json, file_hash
from test_generic_source_mapping import layout, table, cell

def target():
    return {'target_id': 'column_fixture', 'title': '给付', 'header_path': ['给付']}

def sources(text='给付取某项金额的160%。'):
    return [{'source_id': 'fresh', 'text': text, 'source': {'document_version_id': 'doc', 'page_number': 1}}]

def proposal(payload, *, unit='CNY', tree=None):
    eid = payload['source_catalog']['evidence'][0][0]
    nid = payload['source_catalog']['numbers'][0][0] if payload['source_catalog']['numbers'] else None
    return {'formulas': [{'metric_code': payload['target']['target_id'], 'output_unit': unit, 'summary': '原文关系', 'evidence': [{'evidence_id': eid}], 'expression_tree': tree or {'type': 'multiply', 'args': [{'type': 'parameter', 'name': 'unregistered_amount'}, {'type': 'constant', 'number_id': nid, 'unit': 'dimensionless'}]}, 'variables': [{'name': 'unregistered_amount', 'meaning': '原文金额，当前值尚未取得', 'unit': 'CNY', 'source_kind': 'metric', 'binding': 'not_in_any_registry', 'evidence': [{'evidence_id': eid}]}], 'time_basis': '原文约定时点', 'complete': True}]}

class FormulaFlowTests(unittest.TestCase):

    def test_unknown_dependency_can_be_declared_without_registered_formula_or_value(self):
        model = SimpleNamespace(structured=lambda s, p: proposal(p))
        r = extract_target(target(), sources(), model)
        self.assertEqual(r['state'], 'deterministic_checks_passed')
        self.assertEqual(r['formula']['expression_tree']['args'][1]['value'], '1.6')
        self.assertEqual(r['verification']['dependency_readiness'], 'not_evaluated')

    def test_insufficient_evidence_gets_one_rag_and_one_more_extraction(self):
        calls = []

        def invoke(s, p):
            calls.append(p)
            return {'formulas': []} if len(calls) == 1 else proposal(p)
        retrieve = Mock(return_value=sources())
        r = extract_target(target(), sources(), SimpleNamespace(structured=invoke), retrieve=retrieve)
        self.assertEqual(r['state'], 'deterministic_checks_passed')
        self.assertEqual(r['model_calls'], 2)
        self.assertEqual(r['rag_calls'], 1)
        retrieve.assert_called_once()

    def test_still_missing_stops_after_one_rag(self):
        model = Mock()
        model.structured.return_value = {'formulas': []}
        retrieve = Mock(return_value=sources())
        r = extract_target(target(), sources(), model, retrieve=retrieve)
        self.assertEqual(r['state'], 'evidence_insufficient')
        self.assertEqual(model.structured.call_count, 2)
        retrieve.assert_called_once()

    def test_no_local_evidence_searches_first_and_extracts_once(self):
        r = extract_target(target(), [], SimpleNamespace(structured=lambda s, p: proposal(p)), retrieve=lambda *a: sources())
        self.assertEqual((r['rag_calls'], r['model_calls']), (1, 1))

    def test_invalid_output_has_one_review_and_no_evidence_search(self):
        model = Mock()
        model.structured.return_value = {'wrong': []}
        retrieve = Mock()
        r = extract_target(target(), sources(), model, retrieve=retrieve)
        self.assertEqual(r['state'], 'invalid_output')
        self.assertEqual(model.structured.call_count, 2)
        self.assertEqual(r['review_calls'], 1)
        retrieve.assert_not_called()

    def test_unit_error_does_not_trigger_rag(self):
        model = SimpleNamespace(structured=lambda s, p: proposal(p, unit='dimensionless'))
        retrieve = Mock()
        r = extract_target(target(), sources(), model, retrieve=retrieve)
        self.assertEqual(r['state'], 'invalid_output')
        self.assertEqual(r['error'], 'result_unit_mismatch')
        retrieve.assert_not_called()

    def test_unknown_number_id_rejected(self):

        def invoke(s, p):
            raw = proposal(p)
            raw['formulas'][0]['expression_tree']['args'][1]['number_id'] = 'not-a-source-number'
            return raw
        r = extract_target(target(), sources(), SimpleNamespace(structured=invoke))
        self.assertEqual(r['state'], 'invalid_output')

    def test_no_keyword_semantic_gate(self):

        def invoke(s, p):
            return proposal(p, tree={'type': 'parameter', 'name': 'unregistered_amount'})
        r = extract_target(target(), sources('没有传统公式关键词的原始说明。'), SimpleNamespace(structured=invoke))
        self.assertEqual(r['state'], 'deterministic_checks_passed')

    def test_unavailable_branch_is_not_a_complete_formula(self):
        r = extract_target(target(), sources(), SimpleNamespace(structured=lambda s, p: proposal(p, tree={'type': 'unavailable', 'reason': '原文尚缺'})))
        self.assertEqual(r['state'], 'evidence_insufficient')
        self.assertIn('expression_contains_unavailable_branch', r['missing'])

    def test_provider_error_is_not_evidence_gap(self):
        model = Mock()
        model.structured.side_effect = RuntimeError('deepseek_http_402')
        retrieve = Mock()
        r = extract_target(target(), sources(), model, retrieve=retrieve)
        self.assertEqual(r['state'], 'provider_error')
        retrieve.assert_not_called()

class CurrentExampleTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = SimpleNamespace(root=self.root)
        (self.root / 'source.pdf').write_bytes(b'%PDF original')

    def tearDown(self):
        self.tmp.cleanup()

    def shifted(self, id, cells, rows, cols, y):
        t = table(cells, rows, cols, id=id)
        for c in t['cells']:
            c['cell_id'] = id + '-' + c['cell_id']
            c['native_bounding_box'][1] += y
            c['native_bounding_box'][3] += y
        t['native_bounding_box'][1] += y
        t['native_bounding_box'][3] += y
        return t

    def package(self):
        male = self.shifted('plan-m', [cell(0, 0, '性别：男'), cell(0, 1, '保费：100')], 1, 2, 0)
        female = self.shifted('plan-f', [cell(0, 0, '性别：女'), cell(0, 1, '保费：90')], 1, 2, 100)

        def annual(id, y):
            return self.shifted(id, [cell(0, 0, '保单年度'), cell(0, 1, '给付'), cell(1, 0, '1'), cell(1, 1, '160')], 2, 2, y)
        data = layout([male, annual('annual-m', 40), female, annual('annual-f', 140)], 'brochure')
        data['blocks'] = [{'block_id': 'unlisted-prose', 'block_type': 'paragraph', 'raw_text': '正文还有额外保险金，但不是演示列。', 'page_number': 1, 'native_bounding_box': [0, 180, 100, 190]}]
        lp = self.root / 'layout.json'
        write_json(lp, data)
        doc = {'document_version_id': 'd', 'document_type': 'brochure', 'source_file': 'source.pdf', 'source_sha256': file_hash(self.root / 'source.pdf'), 'layout_file': lp.name, 'layout_sha256': file_hash(lp)}
        return prepare_mapping(self.app, None, {'identity': {}, 'documents': [doc]}, self.root)['package_ref']

    def test_only_illustration_columns_no_prose_targets(self):
        ref = self.package()
        r = prepare_examples(self.root, ref)
        self.assertEqual(len(r['cases']), 2)
        self.assertEqual([t['title'] for t in r['targets']], ['给付', '给付'])
        self.assertNotIn('额外保险金', str(r['targets']))

    def test_nearby_small_tables_preserved_without_merging_male_and_female_conditions(self):
        ref = self.package()
        r = prepare_examples(self.root, ref)
        first = next((c for c in r['cases'] if c['tables'][0]['table_id'] == 'annual-m'))
        before = [t for t in first['nearby_tables'] if t['position'] == 'before']
        self.assertEqual(len(before), 1)
        self.assertIn('性别：男', str(before[0]['cells']))
        self.assertNotIn('性别：女', str(before[0]['cells']))
        read = read_example(self.root, ref, r['result_ref'], first['case_id'], row_count=1)
        self.assertTrue(read['has_more_rows'])
        self.assertEqual(read['nearby_tables'], first['nearby_tables'])
        self.assertEqual(read['condition_interpretation'], 'literal_context_only')

    def test_new_formula_path_does_not_import_old_formula_or_profile_functions(self):
        ref = self.package()
        self.app.model = SimpleNamespace(binding={'provider': 'fixture'}, structured=lambda s, p: proposal(p, tree={'type': 'parameter', 'name': 'unregistered_amount'}))
        with patch('insurance_rag.illustration.preparation.validate_proposal', side_effect=AssertionError('old semantic gate used')):
            r = prepare_rules(self.app, ref)
        self.assertEqual(r['target_count'], 2)
        self.assertTrue(all((x['state'] == 'deterministic_checks_passed' for x in r['rules'])))
        self.assertFalse(r['historical_rule_reuse'])

    def test_current_rule_provider_does_not_hide_transport_retries(self):
        from insurance_rag.agent.models import DeepSeekChat
        from insurance_rag.services.config import ChatSettings
        ref = self.package()
        self.app.model = DeepSeekChat(ChatSettings('deepseek', 'https://api.deepseek.com', 'deepseek-flash', 'test-only-key', retries=3))
        with patch.object(DeepSeekChat, 'structured', side_effect=lambda s, p: proposal(p, tree={'type': 'parameter', 'name': 'unregistered_amount'})) as call:
            result = prepare_rules(self.app, ref)
        self.assertEqual(call.call_count, 2)
        self.assertEqual(result['versions']['model']['retries'], 0)
        self.assertEqual(self.app.model.settings.retries, 3)

    def test_rule_read_replays_checks_and_rejects_changed_evidence(self):
        from insurance_rag.ingestion.source_rules import validate_rules
        from insurance_rag.ingestion.source_package import reference
        from insurance_rag.parsing.common import read_json
        ref = self.package()
        self.app.model = SimpleNamespace(binding={'provider': 'fixture'}, structured=lambda s, p: proposal(p, tree={'type': 'parameter', 'name': 'unregistered_amount'}))
        result = prepare_rules(self.app, ref)
        validate_rules(self.root, ref, result['result_ref'])
        changed = read_json(self.root / result['result_ref']['path'])
        changed['rules'][0]['source_catalog'][0]['text'] = 'historical injected evidence'
        path = self.root / 'changed.json'
        write_json(path, changed)
        with self.assertRaisesRegex(ValueError, 'current_rule_evidence_changed'):
            validate_rules(self.root, ref, reference(self.root, path))
        changed = copy.deepcopy(result)
        changed.pop('result_ref')
        changed['rules'][0]['formula']['summary'] = 'changed after validation'
        write_json(path, changed)
        with self.assertRaisesRegex(ValueError, 'current_(formula_checks|acquisition_rule)_changed'):
            validate_rules(self.root, ref, reference(self.root, path))

    def test_identity_does_not_select_header_or_context_extraction(self):
        from insurance_rag.ingestion.source_package import load_package
        first = self.package()
        package = load_package(self.root, first)
        doc = package['source_documents'][0].copy()
        doc['document_version_id'] = 'different-document'
        from insurance_rag.parsing.common import read_json
        data = read_json(self.root / doc['layout_file'])
        data['document_version_id'] = doc['document_version_id']
        write_json(self.root / 'identity-replaced.json', data)
        doc['layout_file'] = 'identity-replaced.json'
        doc['layout_sha256'] = file_hash(self.root / doc['layout_file'])
        second = prepare_mapping(self.app, None, {'identity': {'company_name': 'random-company', 'product_name': 'random-product'}, 'documents': [doc]}, self.root)['package_ref']

        def shape(ref):
            result = prepare_examples(self.root, ref)
            return [{'headers': [x['header_path'] for x in c['columns']], 'context': [x['raw_text'] for x in c['contexts']], 'neighbors': [(n['position'], [v['raw_text'] for v in n['cells']]) for n in c['nearby_tables']]} for c in result['cases']]
        self.assertEqual(shape(first), shape(second))
        from insurance_rag.ingestion.source_examples import validate_examples
        with self.assertRaisesRegex(ValueError, 'current_example_source_mismatch'):
            validate_examples(self.root, second, prepare_examples(self.root, first)['result_ref'])

    def test_agent_tools_read_new_case_and_rules_without_old_profile(self):
        from insurance_rag.ingestion.source_rule_reading import example_packet
        from insurance_rag.tools.adapters.product_table_rules import get_product_table_rules
        from insurance_rag.tools.adapters.product_table_steps import read_product_example
        ref = self.package()
        self.app.model = SimpleNamespace(binding={'provider': 'fixture'}, structured=lambda s, p: proposal(p, tree={'type': 'parameter', 'name': 'unregistered_amount'}))
        rules = prepare_rules(self.app, ref)
        content = {'scope': 'current_source_business', 'bundle_id': 'current', 'identity': {'product_name': 'fixture'}, 'source_package_ref': ref, 'result_refs': {'rules': rules['result_ref'], 'examples': rules['examples_ref']}}
        self.app.store = object()
        context = {'app': self.app, 'owner': 'alice', 'state': {}}
        case = example_packet(self.app, content)['cases'][0]
        with patch('insurance_rag.ingestion.store.bundle', return_value={'content': content}):
            rule_read = get_product_table_rules({'bundle_id': 'current'}, context)
            self.assertIn('deterministic_checks_passed', str(rule_read))
            self.assertIn('not_evaluated', str(rule_read))
            case_read = read_product_example({'bundle_id': 'current', 'case_id': case['case_id'], 'row_count': 1}, context)
            self.assertIn('性别：男', str(case_read))
            self.assertIn('literal_context_only', str(case_read))
if __name__ == '__main__':
    unittest.main()
