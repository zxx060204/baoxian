import copy, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from insurance_rag.parsing.generic_mapping import map_layout
from insurance_rag.parsing.common import write_json, file_hash
from insurance_rag.parsing.models import PARSER_VERSION
from insurance_rag.parsing.table_contract import POLICY as TABLE_POLICY

def cell(r, c, text, rs=1, cs=1):
    return {'cell_id': f'c{r}-{c}', 'row_index': r, 'column_index': c, 'row_span': rs, 'column_span': cs, 'raw_text': text, 'native_bounding_box': [c * 50, r * 10, (c + cs) * 50, (r + rs) * 10]}

def table(cells, rows, cols, page=1, id='t'):
    return {'table_id': id, 'page_number': page, 'row_count': rows, 'column_count': cols, 'cells': cells, 'native_bounding_box': [0, 0, cols * 50, rows * 10], 'header_rows_candidate': [0]}

def layout(tables, kind='cash_value'):
    return {'parser_version': PARSER_VERSION, 'table_preparation_policy': TABLE_POLICY, 'document_version_id': 'd', 'metadata': {'document_type': kind, 'product_name': '从未见过的产品'}, 'pages': [{'page_number': n, 'width': 400, 'raw_text': ''} for n in range(1, max((t['page_number'] for t in tables)) + 1)], 'blocks': [], 'tables': tables}

class GenericSourceMappingTests(unittest.TestCase):

    def test_multiple_row_dimensions_and_horizontal_values(self):
        heads = ['期间', '缴费', '年龄', '性别', '年度一', '年度二']
        values = ['终身', '5', '35', '男', '101', '102']
        data = layout([table([cell(0, i, x) for i, x in enumerate(heads)] + [cell(1, i, x) for i, x in enumerate(values)], 2, 6)])
        model, records = map_layout(data, 'h')
        self.assertEqual(len(records), 2)
        self.assertEqual([d['raw'] for d in records[0]['row_dimensions']], values[:4])
        self.assertEqual(records[1]['column_path'], ['年度二'])
        renamed = copy.deepcopy(data)
        renamed['metadata']['product_name'] = '其他名字'
        self.assertEqual(map_layout(renamed, 'h'), (model, records))

    def test_multilevel_headers_and_merged_row_header(self):
        t = table([cell(0, 0, '方案', 2), cell(0, 1, '利益', 1, 2), cell(1, 1, '甲'), cell(1, 2, '乙'), cell(2, 0, '选项A', 2), cell(2, 1, '10'), cell(2, 2, '20'), cell(3, 1, '11'), cell(3, 2, '21')], 4, 3)
        m, r = map_layout(layout([t]), 'h')
        self.assertEqual(len(r), 4)
        self.assertEqual(r[0]['column_path'], ['利益', '甲'])
        self.assertEqual(r[2]['row_header_ids'], ['c2-0'])
        self.assertTrue(m['tables'][0]['header_parents'])

    def test_fragment_inherits_physical_grid_only_in_mapper(self):
        t = table([cell(0, 0, '年度'), cell(0, 1, '甲'), cell(0, 2, '乙'), cell(1, 0, '1'), cell(1, 1, '100'), cell(1, 2, '200'), cell(2, 0, '2'), cell(2, 1, '110'), cell(2, 2, '210')], 3, 3)
        f = {'table_id': 'f', 'page_number': 2, 'row_count': 1, 'column_count': 3, 'cells': [], 'native_bounding_box': [5, 5, 140, 15], 'structure_status': 'fragment', 'tokens': [{'row_index': 0, 'text': v, 'native_bounding_box': [i * 50 + 5, 5, i * 50 + 25, 15]} for i, v in enumerate(['3', '120', '220'])]}
        t['context_candidates'] = [{'context_id': 'unit-note', 'raw_text': '原始单位说明', 'page_number': 1, 'role': 'unclassified'}]
        data = layout([t, f])
        original = copy.deepcopy(data)
        m, r = map_layout(data, 'h')
        self.assertEqual(data, original)
        self.assertEqual(m['tables'][1]['continuation_of'], 't')
        self.assertEqual(r[-1]['column_path'], ['乙'])
        self.assertEqual(r[-1]['value']['raw'], '220')
        self.assertIn('unit-note', r[-1]['context_refs'])
        self.assertEqual(m['tables'][1]['context_candidates'][0]['page_number'], 1)

    def test_unmatched_fragment_is_retained_as_explicit_gap(self):
        f = {'table_id': 'f', 'page_number': 1, 'row_count': 1, 'column_count': 3, 'cells': [], 'native_bounding_box': [0, 0, 150, 10], 'structure_status': 'fragment', 'tokens': [{'text': '10', 'row_index': 0, 'native_bounding_box': [5, 0, 10, 10]}]}
        m, r = map_layout(layout([f]), 'h')
        self.assertEqual(m['status'], 'partial')
        self.assertEqual(m['issues'][0]['reason'], 'fragment_grid_unresolved')
        self.assertEqual(m['tables'][0]['token_count'], 1)

    def test_text_and_percent_are_not_forced_to_money(self):
        t = table([cell(0, 0, '选项'), cell(0, 1, '原值'), cell(1, 0, 'A'), cell(1, 1, '172%'), cell(2, 0, 'B'), cell(2, 1, '由承保确定')], 3, 2)
        m, r = map_layout(layout([t], 'brochure'), 'h')
        self.assertEqual(r[0]['value']['kind'], 'percent')
        self.assertEqual(r[1]['value']['kind'], 'text')
        self.assertEqual(m['document_branch'], 'brochure')

    def test_mapping_stages_local_artifacts_without_store_or_network(self):
        from insurance_rag.ingestion.mapping import prepare_mapping
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            pdf = root / 'source.pdf'
            pdf.write_bytes(b'%PDF source')
            lp = root / 'layout.json'
            write_json(lp, layout([table([cell(0, 0, '条件'), cell(0, 1, '值'), cell(1, 0, 'A'), cell(1, 1, '20')], 2, 2)]))
            app = SimpleNamespace(root=root, store=Mock(side_effect=AssertionError('database accessed')))
            doc = {'document_type': 'cash_value', 'document_version_id': 'd', 'source_file': 'source.pdf', 'source_sha256': file_hash(pdf), 'layout_file': 'layout.json', 'layout_sha256': file_hash(lp)}
            report = prepare_mapping(app, None, {'bundle_id': 'b', 'documents': [doc]}, root)
            self.assertNotIn('numeric_batch_id', report)
            self.assertNotIn('bindings', report)
            self.assertEqual(report['scope'], 'source_relations')
            self.assertTrue(list((root / 'data/source_packages').rglob('*.model.json')))

    def test_detected_headerless_grid_does_not_drop_first_data_row(self):
        p = table([cell(0, 0, '年度'), cell(0, 1, '数值'), cell(1, 0, '1'), cell(1, 1, '10'), cell(2, 0, '2'), cell(2, 1, '20')], 3, 2)
        q = table([cell(0, 0, '3'), cell(0, 1, '30'), cell(1, 0, '4'), cell(1, 1, '40')], 2, 2, page=2, id='q')
        m, r = map_layout(layout([p, q]), 'h')
        self.assertEqual(len(r), 4)
        self.assertEqual(m['tables'][1]['continuation_of'], 't')
        self.assertEqual(r[2]['value']['raw'], '30')
        self.assertEqual(r[2]['column_path'], ['数值'])

    def test_source_only_task_summary_does_not_claim_business_readiness(self):
        from insurance_rag.tools.adapters.product_documents import task_summary
        row = {'id': 'job', 'state': 'ready', 'content': {'stage': 'completed', 'result': {'scope': 'parser_and_generic_mapper', 'identity': {'product_name': '未知产品'}, 'mapping': {'structure_status': 'mapped'}, 'downstream': {'search_index': 'deferred'}, 'published': False}}}
        summary = task_summary(row)
        self.assertIsNone(summary['bundle_id'])
        self.assertFalse(summary['qa_ready'])
        self.assertFalse(summary['business_ready'])
        self.assertFalse(summary['source_ready'])
        self.assertEqual(summary['downstream']['search_index'], 'deferred')
