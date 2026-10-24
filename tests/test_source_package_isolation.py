import copy, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from insurance_rag.parsing.common import write_json, file_hash
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.ingestion.source_package import load_package, reference, PREPARATION_POLICY
from test_generic_source_mapping import layout, table, cell

class SourcePackageIsolationTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.app = SimpleNamespace(root=self.root)
        (self.root / 'source.pdf').write_bytes(b'%PDF original')
        self.data = layout([table([cell(0, 0, '条件'), cell(0, 1, '值'), cell(1, 0, 'A'), cell(1, 1, '20')], 2, 2)])
        self.doc = self.save_layout(self.data, 'layout.json')
        self.content = {'bundle_id': 'legacy-looking-id', 'identity': {'product_version_id': 'v'}, 'documents': [self.doc], 'mapping': {'bindings': ['legacy']}}

    def tearDown(self):
        self.tmp.cleanup()

    def save_layout(self, data, path):
        write_json(self.root / path, data)
        return {'document_version_id': 'd', 'document_type': 'cash_value', 'source_file': 'source.pdf', 'source_sha256': file_hash(self.root / 'source.pdf'), 'layout_file': path, 'layout_sha256': file_hash(self.root / path)}

    def prepare(self):
        return prepare_mapping(self.app, None, self.content, self.root)

    def test_old_layout_cannot_be_upgraded_by_renaming_its_report(self):
        old = copy.deepcopy(self.data)
        old.pop('parser_version')
        self.content['documents'] = [self.save_layout(old, 'old.json')]
        with self.assertRaisesRegex(ValueError, 'current_source_parse_required'):
            self.prepare()

    def test_different_source_revision_cannot_overwrite_previous_mapping(self):
        first = self.prepare()
        before = (self.root / first['documents'][0]['model_ref']['path']).read_bytes()
        changed = copy.deepcopy(self.data)
        changed['tables'][0]['cells'][-1]['raw_text'] = '99'
        self.content['documents'] = [self.save_layout(changed, 'changed.json')]
        second = self.prepare()
        self.assertNotEqual(first['package_id'], second['package_id'])
        self.assertNotEqual(first['package_ref']['path'], second['package_ref']['path'])
        self.assertEqual((self.root / first['documents'][0]['model_ref']['path']).read_bytes(), before)
        load_package(self.root, first['package_ref'])

    def test_existing_package_is_verified_instead_of_overwritten(self):
        report = self.prepare()
        p = self.root / report['documents'][0]['model_ref']['path']
        p.write_text('{}')
        with self.assertRaisesRegex(ValueError, 'source_artifact_changed'):
            self.prepare()
        self.assertEqual(p.read_text(), '{}')

    def test_swapping_records_or_text_from_another_revision_is_rejected(self):
        report = self.prepare()
        model = report['documents'][0]['model_ref']
        m = json.loads((self.root / model['path']).read_text(encoding='utf8'))
        (self.root / m['records_ref']['path']).write_text('{}\n')
        with self.assertRaisesRegex(ValueError, 'source_artifact_changed'):
            load_package(self.root, report['package_ref'])

    def test_legacy_report_is_rejected_even_with_valid_hash(self):
        write_json(self.root / 'oldreport.json', {'status': 'mapped', 'bindings': []})
        with self.assertRaisesRegex(ValueError, 'current_source_package_required'):
            load_package(self.root, reference(self.root, self.root / 'oldreport.json'))

    def test_old_logical_header_fields_cannot_enter_physical_source(self):
        old = copy.deepcopy(self.data)
        old['tables'][0]['inherited_headers'] = [{'raw_text': 'stale'}]
        self.content['documents'] = [self.save_layout(old, 'old.json')]
        with self.assertRaisesRegex(ValueError, 'physical_layout_contains_logical_mapping'):
            self.prepare()

    def test_identity_changes_create_separate_package(self):
        first = self.prepare()
        self.content['identity']['product_version_id'] = 'other'
        second = self.prepare()
        self.assertNotEqual(first['package_id'], second['package_id'])

class SourcePreparationCacheIsolationTests(unittest.TestCase):

    def test_source_parser_does_not_read_historical_layout_cache(self):
        from reportlab.pdfgen.canvas import Canvas
        from insurance_rag.agent.store import AgentStore
        from insurance_rag.onboarding.store import IntakeStore
        from insurance_rag.ingestion.store import asset_folder, register_asset
        from insurance_rag.ingestion.documents import parse_all
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = AgentStore.sqlite(root / 'db')
            app = SimpleNamespace(root=root, store=agent)
            try:
                folder = asset_folder(root, 'alice')
                folder.mkdir(parents=True)
                pdf = folder / 'source.pdf'
                canvas = Canvas(str(pdf))
                canvas.drawString(40, 700, 'Current original source')
                canvas.save()
                asset = register_asset(app, 'alice', pdf)
                agent.execute('CREATE TABLE insurance_onboarding_document_content (content TEXT)')
                agent.execute('INSERT INTO insurance_onboarding_document_content VALUES (?)', (json.dumps({'layout_file':'poisoned-old.json'}),))
                target = root / 'fresh'
                target.mkdir()
                docs = parse_all(app, 'alice', [{'asset_id': asset['id'], 'document_type': 'terms'}], {'company_id': 'c', 'product_name': 'Current product', 'product_version_id': 'v'}, target)
                data = json.loads((root / docs[0]['layout_file']).read_text(encoding='utf8'))
                self.assertIn('Current original source', data['pages'][0]['raw_text'])
                self.assertFalse(docs[0]['cache_reused'])
                report = prepare_mapping(app, 'alice', {'identity': {}, 'documents': docs}, target)
                load_package(root, report['package_ref'])
            finally:
                agent.close()

    def test_old_completed_job_cannot_be_resumed_by_current_worker(self):
        from insurance_rag.agent.store import AgentStore
        from insurance_rag.onboarding.store import IntakeStore
        from insurance_rag.ingestion.service import run_job
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            agent = AgentStore.sqlite(root / 'db')
            app = SimpleNamespace(root=root, store=agent)
            try:
                IntakeStore(agent).create('preparation_job', 'legacy', 'alice', 'v', {'preparation_policy': 'illustration-model-v17', 'result': {'bindings': ['old']}}, 'ready')
                with self.assertRaisesRegex(ValueError, 'legacy_preparation_requires_new_job'):
                    run_job(app, 'alice', 'legacy')
            finally:
                agent.close()
if __name__ == '__main__':
    unittest.main()
