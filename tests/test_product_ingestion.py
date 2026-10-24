import copy, json, tempfile, unittest
from pathlib import Path
from contextlib import contextmanager
from types import SimpleNamespace
from unittest.mock import patch
from fastapi.testclient import TestClient
from insurance_rag.agent.store import AgentStore, Forbidden, Conflict
from insurance_rag.parsing.common import stable_hash
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.ingestion.store import register_asset, asset_folder, get_asset, create_job, bundle, owner_key
from insurance_rag.ingestion.service import run_job, validate_analysis
from insurance_rag.api.product_backend import create_app

class MultiProductTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agent = AgentStore.sqlite(self.root / 'db')
        self.store = IntakeStore(self.agent)
        self.app = SimpleNamespace(root=self.root, store=self.agent)
        self.asset = self.upload('alice')

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    def upload(self, owner, body=b'%PDF-1.4 test'):
        folder = asset_folder(self.root, owner)
        folder.mkdir(parents=True, exist_ok=True)
        p = folder / (stable_hash(body.hex()) + '.pdf')
        p.write_bytes(body)
        return register_asset(self.app, owner, p)

    def job(self, request='one', owner='alice', asset=None):
        return create_job(self.app, owner, request, [{'asset_id': (asset or self.asset)['id'], 'document_type': 'terms'}])

    def identity(self):
        return {'company_id': 'company', 'product_name': '产品', 'product_id': 'p', 'product_version_id': 'v'}

    def test_owner_isolation_even_for_identical_source_bytes(self):
        other = self.upload('bob')
        self.assertNotEqual(other['id'], self.asset['id'])
        with self.assertRaises(Forbidden):
            get_asset(self.app, 'bob', self.asset['id'])

    def test_cross_owner_job_cannot_reference_asset(self):
        with self.assertRaises(Forbidden):
            self.job(owner='bob')

    def test_dedup_same_request_and_conflicting_input(self):
        a = self.job()
        self.assertEqual(a['id'], self.job()['id'])
        with self.assertRaises(Conflict):
            create_job(self.app, 'alice', 'one', [{'asset_id': self.asset['id'], 'document_type': 'terms'}], product_name='changed')

    def test_reordered_uploads_reuse_queued_job_and_same_request(self):
        other = self.upload('alice', b'%PDF-1.4 other source')
        assets = [{'asset_id': self.asset['id'], 'document_type': 'terms'}, {'asset_id': other['id'], 'document_type': 'brochure'}]
        first = create_job(self.app, 'alice', 'ordered', assets)
        again = create_job(self.app, 'alice', 'ordered', list(reversed(assets)))
        new_request = create_job(self.app, 'alice', 'another-request', list(reversed(assets)))
        self.assertEqual(first['id'], again['id'])
        self.assertEqual(first['id'], new_request['id'])

    def test_no_duplicate_files_with_different_types(self):
        with self.assertRaisesRegex(ValueError, 'duplicate_asset'):
            create_job(self.app, 'alice', 'r', [{'asset_id': self.asset['id'], 'document_type': 'terms'}, {'asset_id': self.asset['id'], 'document_type': 'brochure'}])

    def test_source_replacement_detected(self):
        (self.root / self.asset['content']['path']).write_bytes(b'%PDF-1.4 changed')
        with self.assertRaisesRegex(Conflict, 'asset_changed'):
            get_asset(self.app, 'alice', self.asset['id'])

    def test_upload_path_must_belong_to_owner(self):
        with self.assertRaises(Forbidden):
            register_asset(self.app, 'bob', self.root / self.asset['content']['path'])

    def test_current_worker_does_not_resume_retired_workflow(self):
        job = self.job()
        self.store.update('preparation_job', job['id'], job['revision'], {**job['content'], 'workflow_policy': 'retired-workflow'}, 'queued', 'test', 'historical_workflow')
        with patch('insurance_rag.ingestion.service.parse_all', side_effect=AssertionError('old workflow resumed')):
            with self.assertRaisesRegex(ValueError, 'legacy_preparation_requires_new_job'):
                run_job(self.app, 'alice', job['id'])
        self.assertEqual(self.store.get('preparation_job', job['id'])['state'], 'queued')

    def test_partial_document_set_is_accepted(self):
        self.assertEqual(self.job()['state'], 'queued')

    def test_unknown_document_type_is_rejected(self):
        with self.assertRaises(ValueError):
            create_job(self.app, 'alice', 'x', [{'asset_id': self.asset['id'], 'document_type': 'made_up'}])

    def test_failure_persisted_and_completed_stage_resumed(self):
        job = self.job()
        identity = self.identity()
        with patch('insurance_rag.ingestion.service.identify', return_value=identity) as identify, patch('insurance_rag.ingestion.service.parse_all', side_effect=ValueError('test_parse_failed')):
            with self.assertRaisesRegex(ValueError, 'test_parse_failed'):
                run_job(self.app, 'alice', job['id'])
            with self.assertRaisesRegex(ValueError, 'test_parse_failed'):
                run_job(self.app, 'alice', job['id'])
            self.assertEqual(identify.call_count, 1)
        r = self.store.get('preparation_job', job['id'], 'alice')
        self.assertEqual(r['state'], 'failed')
        self.assertEqual(r['content']['failed_stage'], 'parsing')
        self.assertEqual(r['content']['attempts'], 2)

    def test_changed_checkpoint_is_rejected(self):
        job = self.job()
        with patch('insurance_rag.ingestion.service.identify', return_value=self.identity()), patch('insurance_rag.ingestion.service.parse_all', side_effect=ValueError('parse_failed')):
            with self.assertRaises(ValueError):
                run_job(self.app, 'alice', job['id'])
        r = self.store.get('preparation_job', job['id'])
        ref = r['content']['completed_steps']['identity']
        (self.root / ref['path']).write_text('{}', encoding='utf8')
        with self.assertRaisesRegex(ValueError, 'artifact_changed'):
            run_job(self.app, 'alice', job['id'])

    def test_private_bundle_authorization(self):
        self.store.create('product_bundle', 'product-bundle:test', 'alice', 'v', {'documents': []}, 'ready')
        with self.assertRaises(Forbidden):
            bundle(self.app, 'bob', 'product-bundle:test')

    def test_analysis_hash_bound_to_bundle(self):
        parent = self.store.create('product_bundle', 'product-bundle:test', 'alice', 'v', {'documents': []}, 'ready')
        a = self.store.create('bundle_analysis', 'a', 'alice', 'v', {'bundle_id': 'product-bundle:test', 'bundle_content_hash': parent['content_hash']}, 'verified_source_answer')
        self.assertEqual(validate_analysis(self.app, 'alice', {'analysis_id': 'a', 'content_hash': a['content_hash']})['id'], 'a')
        with self.assertRaises(Forbidden):
            validate_analysis(self.app, 'bob', {'analysis_id': 'a', 'content_hash': a['content_hash']})
        with self.assertRaises(Conflict):
            validate_analysis(self.app, 'alice', {'analysis_id': 'a', 'content_hash': 'wrong'})

    def test_http_requires_cookie_and_origin_and_persists_job(self):

        @contextmanager
        def factory():
            yield self.app
        with TestClient(create_app(factory, start_workers=False)) as c:
            self.assertEqual(c.get('/v1/product-ingestion/jobs').status_code, 401)
            self.assertEqual(c.get('/').status_code, 200)
            self.assertEqual(c.post('/v1/product-ingestion/uploads', content=b'%PDF-1.4 x').status_code, 403)
            headers = {'Origin': 'http://testserver'}
            u = c.post('/v1/product-ingestion/uploads', content=b'%PDF-1.4 x', headers=headers)
            self.assertEqual(u.status_code, 200)
            aid = u.json()['asset_id']
            r = c.post('/v1/product-ingestion/jobs', json={'request_id': 'http', 'assets': [{'asset_id': aid, 'document_type': 'terms'}]}, headers=headers)
            self.assertEqual(r.status_code, 202)
            self.assertEqual(r.json()['state'], 'queued')

    def test_unknown_product_identity_is_inferred_but_quotes_are_verified(self):
        from insurance_rag.ingestion.documents import identify
        text = '某某保险股份有限公司\n某某医疗保险\n保险期间为一年。'.replace(chr(92) + 'n', chr(10))
        entry = {'asset_id': self.asset['id'], 'document_type': 'terms', 'sha256': self.asset['content']['sha256']}
        proposal = {'company': '某某保险股份有限公司', 'product_name': '某某医疗保险', 'source_id': entry['sha256'], 'company_quote': '某某保险股份有限公司', 'product_quote': '某某医疗保险'}
        self.app.model = SimpleNamespace(structured=lambda *a: proposal)
        reader = SimpleNamespace(is_encrypted=False, pages=[SimpleNamespace(extract_text=lambda: text)])
        with patch('pypdf.PdfReader', return_value=reader):
            identity = identify(self.app, 'alice', [entry], {'company': '', 'product_name': '', 'version_label': ''})
        self.assertEqual(identity['product_name'], '某某医疗保险')
        self.assertEqual(identity['identity_method'], 'literal_header_model_verified')
        proposal['company_quote'] = '不存在的公司'
        with patch('pypdf.PdfReader', return_value=reader):
            with self.assertRaisesRegex(ValueError, 'identity_quote_not_literal'):
                identify(self.app, 'alice', [entry], {'company': '', 'product_name': '', 'version_label': ''})

    def test_table_header_facts_never_join_noncontiguous_lines(self):
        from insurance_rag.ingestion.documents import table_facts
        text = '单位：1000元' + chr(13) + chr(10) + '20 30 40' + chr(10) + '注：限制条件适用'
        facts = table_facts({'pages': [{'page_number': 1, 'raw_text': text}]}, {'document_version_id': 'd', 'document_type': 'premium_rate'})
        self.assertEqual(len(facts), 2)
        for f in facts:
            a, b = f['page_character_range']
            self.assertEqual(text[a:b], f['text'])
            self.assertNotIn('20 30 40', f['text'])

    def test_real_backend_lifespan_initializes_recovery(self):

        @contextmanager
        def factory():
            yield self.app
        with TestClient(create_app(factory, start_workers=True)) as client:
            self.assertEqual(client.get('/').status_code, 200)

    def test_agent_tool_rejects_asset_not_attached_to_run(self):
        from insurance_rag.tools.adapters.product_documents import product_documents
        c = {'app': SimpleNamespace(store=self.agent, root=self.root), 'owner': 'alice', 'run': {'run_id': 'run', 'input': {'attachments': []}}, 'call_id': 'call', 'state': {}}
        with self.assertRaises(Forbidden):
            product_documents({'action': 'prepare', 'assets': [{'asset_id': self.asset['id'], 'document_type': 'terms'}], 'product_name': '', 'company': '', 'version_label': ''}, c)

    def test_single_agent_business_tool_registered(self):
        from insurance_rag.tools.adapters.insurance import registry
        r = registry()
        self.assertIn('product_documents', r.specs)
        self.assertFalse(r.specs['product_documents'].read_only)

    def test_mapping_failure_does_not_create_qa_or_indexes(self):
        from insurance_rag.parsing.common import file_hash
        from insurance_rag.tools.adapters.capabilities import capability_view
        layout = self.root / 'layout.json'
        layout.write_text('{"pages":[]}', encoding='utf8')
        doc = {'document_version_id': 'doc', 'document_type': 'terms', 'source_file': self.asset['content']['path'], 'source_sha256': self.asset['content']['sha256'], 'layout_file': 'layout.json', 'layout_sha256': file_hash(layout), 'pages': 1}
        job = self.job('qa-first')
        with patch('insurance_rag.ingestion.service.identify', return_value=self.identity()), patch('insurance_rag.ingestion.service.parse_all', return_value=[doc]), patch('insurance_rag.ingestion.documents.ensure_numeric_contract', side_effect=lambda app, docs, folder: docs), patch('insurance_rag.ingestion.source_index.build_source_index', return_value={'chunk_count': 1}), patch('insurance_rag.ingestion.mapping.prepare_mapping', side_effect=ValueError('mapping_offline')):
            with self.assertRaisesRegex(ValueError, 'mapping_offline'):
                run_job(self.app, 'alice', job['id'])
        stored = self.store.get('preparation_job', job['id'], 'alice')
        self.assertNotIn('qa_result', stored['content'])
        self.assertEqual(stored['content']['failed_stage'], 'generic_mapping_v2')
        self.assertNotIn('qa_bundle', __import__('insurance_rag.onboarding.store',fromlist=['ENTITIES']).ENTITIES)
        self.assertEqual(self.store.list('product_bundle'), [])

class AgentPreparationTests(unittest.TestCase):

    def test_prepare_wait_restart_resume_reports_source_only_readiness(self):
        from test_native_loop import Scripted, tool, answer
        from insurance_rag.agent.engine import AgentEngine
        from insurance_rag.services.config import AgentSettings
        from support_fixture_tools import FixtureTools
        from insurance_rag.agent.loop_config import LoopSettings
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            store = AgentStore.sqlite(root / 'db')
            tools = FixtureTools()
            owner = 'alice'
            folder = asset_folder(root, owner)
            folder.mkdir(parents=True)
            source = folder / 'test.pdf'
            source.write_bytes(b'%PDF-1.4 fixture')
            app = SimpleNamespace(root=root, store=store)
            asset = register_asset(app, owner, source)
            intake = IntakeStore(store)
            attachments = [{'asset_id': asset['id'], 'document_type': 'terms'}]
            model = Scripted([tool('product_documents', {'action': 'prepare', 'assets': attachments})])
            engine = AgentEngine(store, AgentSettings(), model, lambda: tools.release, lambda r: tools, root=root, limits=LoopSettings())
            tid = engine.new_thread(owner)
            run = engine.submit(tid, owner, '分析刚上传产品的保险期间', attachments=attachments)
            with patch('insurance_rag.tools.adapters.product_documents.launch'):
                waiting = engine.drive(run['run_id'], owner)
            self.assertEqual(waiting['state'], 'waiting', waiting)
            self.assertEqual(waiting['clarification']['kind'], 'document_processing')
            self.assertEqual(waiting['budget']['llm_calls'], 1)
            job = intake.get('preparation_job', waiting['clarification']['job_id'], owner)
            intake.update('preparation_job', job['id'], job['revision'], {**job['content'], 'result': {'scope': 'parser_and_generic_mapper', 'identity': {'product_name': '新产品'}, 'mapping': {'structure_status': 'mapped', 'package_id': 'source-package:fixture'}}, 'stage': 'completed'}, 'ready', 'fixture', 'prepared')
            model.steps = [tool('product_documents', {'action': 'status', 'job_id': job['id']}, 'c2'), answer]
            engine = AgentEngine(store, AgentSettings(), model, lambda: tools.release, lambda r: tools, root=root, limits=LoopSettings())
            end = engine.drive(run['run_id'], owner)
            self.assertEqual(end['state'], 'completed', end)
            summary = end['result']['product_preparation']
            self.assertFalse(summary['source_ready'])
            self.assertFalse(summary['qa_ready'])
            self.assertIsNone(summary['bundle_id'])
            self.assertFalse(summary['personal_calculation_ready'])
            self.assertNotIn('document_analysis', end['result'])
            self.assertEqual(model.usage['requests'], 3)
            store.close()
if __name__ == '__main__':
    unittest.main()
