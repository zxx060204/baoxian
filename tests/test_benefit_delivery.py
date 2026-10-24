import copy, json, tempfile, unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import patch
from fastapi.testclient import TestClient
from insurance_rag.agent.store import AgentStore, Forbidden
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import file_hash, write_json, stable_hash
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.illustration.schema import standard_metrics
from insurance_rag.parsing.models import PARSER_VERSION
from insurance_rag.parsing.table_contract import POLICY as TABLE_POLICY
from insurance_rag.api.product_backend import create_app

class BenefitDeliveryTests(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.agent = AgentStore.sqlite(self.root / 'db')
        self.store = IntakeStore(self.agent)
        self.app = SimpleNamespace(root=self.root, store=self.agent)
        self.identity = {'company_id': 'c', 'product_id': 'p', 'product_version_id': 'v', 'product_name': '产品'}
        self.pdf = self.root / 'original.pdf'
        self.pdf.write_bytes(b'%PDF-1.4 test')

    def tearDown(self):
        self.agent.close()
        self.tmp.cleanup()

    def content(self, text, *, tables=None, pages=None, kind='terms'):
        layout = {'parser_version': PARSER_VERSION, 'table_preparation_policy': TABLE_POLICY, 'metadata': {**self.identity, 'document_type': kind}, 'document_version_id': 'd', 'pages': pages or [{'page_number': 1, 'status': 'completed', 'raw_text': text}], 'tables': tables or []}
        p = self.root / 'layout.json'
        write_json(p, layout)
        doc = {'document_version_id': 'd', 'document_id': 'doc', 'document_type': kind, 'source_file': 'original.pdf', 'source_sha256': file_hash(self.pdf), 'layout_file': 'layout.json', 'layout_sha256': file_hash(p), 'pages': len(layout['pages'])}
        return {'bundle_id': 'b', 'identity': self.identity, 'documents': [doc], 'capabilities': {}}

    def test_absent_numeric_material_is_not_mapping_failure(self):
        r = prepare_mapping(self.app, 'alice', self.content('条款'), self.root)
        self.assertEqual(r['structure_status'], 'mapped')
        self.assertEqual(r['scope'], 'source_relations')

    def test_old_agent_catalog_action_is_removed(self):
        from insurance_rag.tools.adapters.product_documents import ProductDocuments
        with self.assertRaises(ValueError):
            ProductDocuments(action='benefits', bundle_id='b')

    def test_pdf_inline_range_and_owner_scope(self):
        from insurance_rag.ingestion.store import asset_folder, register_asset
        owner = 'local-product-administrator'
        c = self.content('满期金')
        folder = asset_folder(self.root, owner)
        folder.mkdir(parents=True)
        p = folder / 'x.pdf'
        p.write_bytes(self.pdf.read_bytes())
        asset = register_asset(self.app, owner, p)
        c['documents'][0]['asset_id'] = asset['id']
        self.store.create('product_bundle', 'product-bundle:b', owner, 'v', c, 'ready')
        self.store.create('product_bundle', 'product-bundle:foreign', 'another-owner', 'v', c, 'ready')

        @contextmanager
        def factory():
            yield self.app
        with TestClient(create_app(factory, start_workers=False)) as client:
            url = '/v1/product-ingestion/bundles/product-bundle:b/documents/d/pdf'
            self.assertEqual(client.get(url).status_code, 401)
            client.get('/')
            r = client.get(url)
            self.assertEqual(r.status_code, 200)
            self.assertEqual(r.content, self.pdf.read_bytes())
            self.assertTrue(r.headers['content-disposition'].startswith('inline;'))
            self.assertEqual(r.headers['content-type'], 'application/pdf')
            self.assertEqual(r.headers['content-security-policy'], "frame-ancestors 'self'")
            r = client.get(url, headers={'Range': 'bytes=0-4'})
            self.assertEqual(r.status_code, 206)
            self.assertEqual(r.content, b'%PDF-')
            self.assertEqual(client.get(url.replace('/product-bundle:b/', '/product-bundle:foreign/')).status_code, 404)
            self.assertEqual(client.get(url.replace('/d/', '/missing/')).status_code, 404)

    def test_local_sessions_do_not_collide_across_ports(self):

        @contextmanager
        def factory():
            yield self.app
        with TestClient(create_app(factory, start_workers=False), base_url='http://127.0.0.1:8784') as first, TestClient(create_app(factory, start_workers=False), base_url='http://127.0.0.1:8783') as second:
            first.get('/')
            second.get('/')
            first.cookies.update(second.cookies)
            self.assertEqual(first.get('/v1/product-ingestion/jobs').status_code, 200)
            second.cookies.update(first.cookies)
            self.assertEqual(second.get('/v1/product-ingestion/jobs').status_code, 200)

    def test_ingestion_runs_new_workflow_without_old_profiles(self):
        from insurance_rag.ingestion.service import run_job
        from insurance_rag.ingestion.store import register_asset, asset_folder, create_job
        c = self.content('满期金')
        folder = asset_folder(self.root, 'alice')
        folder.mkdir(parents=True)
        p = folder / 'x.pdf'
        p.write_bytes(self.pdf.read_bytes())
        a = register_asset(self.app, 'alice', p)
        job = create_job(self.app, 'alice', 'r', [{'asset_id': a['id'], 'document_type': 'terms'}])
        doc = {**c['documents'][0], 'cache_reused': True}
        prepared = {'bundle_id': 'new-source', 'capabilities': {'document_qa': {'status': 'available'}}, 'downstream': {}, 'errors': {}, 'published': False, 'execution_state': 'not_applicable'}
        with patch('insurance_rag.ingestion.service.identify', return_value=self.identity), patch('insurance_rag.ingestion.service.parse_all', return_value=[doc]), patch('insurance_rag.ingestion.source_workflow.complete_source', return_value=prepared) as workflow:
            output = run_job(self.app, 'alice', job['id'])
        workflow.assert_called_once()
        self.assertEqual(output['state'], 'ready')
        self.assertEqual(output['content']['result']['bundle_id'], 'new-source')
        self.assertFalse(output['content']['result']['published'])
        self.assertNotIn('illustration_profile', __import__('insurance_rag.onboarding.store',fromlist=['ENTITIES']).ENTITIES)
        self.assertIn('generic_mapping_v2', output['content']['completed_steps'])

    def test_refresh_creates_new_snapshot_without_discarding_prior_job(self):
        from insurance_rag.ingestion.store import asset_folder, register_asset, create_job
        folder = asset_folder(self.root, 'alice')
        folder.mkdir(parents=True)
        p = folder / 'x.pdf'
        p.write_bytes(self.pdf.read_bytes())
        a = register_asset(self.app, 'alice', p)
        assets = [{'asset_id': a['id'], 'document_type': 'terms'}]
        old = create_job(self.app, 'alice', 'old', assets)
        new = create_job(self.app, 'alice', 'new', assets, refresh=True)
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual(new['content']['preparation_revision'], 'new')

    def test_tiny_font_numeric_text_uses_independent_pdfium_order(self):
        from insurance_rag.ingestion.documents import repair_numeric_text
        from reportlab.pdfgen import canvas
        p = self.root / 'tiny.pdf'
        c = canvas.Canvas(str(p), pagesize=(300, 300))
        c.setFont('Helvetica', 0.8)
        for i in range(50):
            c.drawString(10, 250 - i * 1.2, '10000 ' + str(i) + ' 300 400 500')
        c.save()
        layout = {'pages': [{'page_number': 1, 'raw_text': 'broken order'}]}
        repair_numeric_text(layout, p)
        text = layout['pages'][0]['raw_text']
        self.assertIn('10000 49', text)
        self.assertNotIn('broken order', text)
        self.assertEqual(layout['numeric_text_policy'], 'pdfium-native-v1')

