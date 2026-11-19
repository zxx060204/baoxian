import hashlib
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from baoxian_api.app import create_app
from baoxian_api.documents import DocumentService
from test_numeric_pdf_layout import table_pdf


class MemoryStorage:
    def __init__(self):
        self.objects = {}
        self.reads = []
        self.reject_type = None

    async def exists(self, key):
        return key in self.objects

    async def get(self, key):
        self.reads.append(key)
        return {'body': self.objects[key]}

    async def put(self, key, body, content_type):
        if self.reject_type and f'/{self.reject_type}/' in key:
            raise OSError('storage write failed')
        assert content_type == 'application/json'
        self.objects[key] = body


class SavedLayoutsTests(unittest.TestCase):
    def setUp(self):
        self.storage = MemoryStorage()
        self.body = table_pdf()
        digest = hashlib.sha256(self.body).hexdigest()
        self.rows = [
            dict(id=index, type=kind, mime_type='application/pdf', sha256=digest,
                 storage_key=f'documents/{index}.pdf')
            for index, kind in enumerate(('clause', 'rate_table', 'cash_value_table', 'prospectus'), 1)
        ]
        for row in self.rows:
            self.storage.objects[row['storage_key']] = self.body
        self.service = DocumentService('mysql://localhost/product', 'mysql://localhost/crawl', self.storage)
        self.auth = {'Authorization': 'Bearer test-token'}
        self.batch = '/api/products/12/parsed-layouts'

        def query(_, sql, args):
            if 'LEFT JOIN document' in sql:
                self.assertIn("d.link_status='current' AND d.mime_type='application/pdf'", sql)
                if args[-1] == 12:
                    return self.rows
                return [{'id': None}] if args[-1] == 13 else []
            return [row for row in self.rows if row['id'] == args[0]]

        query_patch = patch.object(self.service, '_query', side_effect=query)
        self.addCleanup(query_patch.stop)
        query_patch.start()
        self.client = self.enterContext(TestClient(create_app(internal_api_token='test-token', document_service=self.service)))

    def test_batch_persists_four_layouts_and_reuses_them(self):
        self.assertEqual(self.client.post(self.batch).status_code, 401)
        self.assertEqual(self.client.get(self.batch, headers=self.auth).json(), {'items': []})
        self.assertEqual(self.client.get('/api/documents/2/saved-layout', headers=self.auth).status_code, 404)
        first = self.client.post(self.batch, headers=self.auth)
        self.assertEqual(first.status_code, 200, first.text)
        self.assertEqual([item['state'] for item in first.json()['items']], ['saved'] * 4)
        self.assertEqual(len(self.client.get(self.batch, headers=self.auth).json()['items']), 4)
        self.assertEqual(len(self.storage.reads), 4)

        for row in self.rows:
            path = f'/api/documents/{row["id"]}/saved-layout'
            stored = self.client.get(path, headers=self.auth)
            key = f'layouts/v1/{row["id"]}/{row["type"]}/{row["sha256"]}.json'
            self.assertEqual(stored.status_code, 200)
            self.assertEqual(stored.content, self.storage.objects[key])
            self.assertEqual(stored.json()['source_sha256'], row['sha256'])
            is_numeric = row['type'] in ('rate_table', 'cash_value_table')
            self.assertEqual(stored.json()['schema'], 'mapping-input-v1' if is_numeric else 'document-content-v1')
            if is_numeric:
                self.assertEqual(stored.json()['tables'][0]['grid'][0], [['Amount', 1, 2], -1])
        replay = self.client.post(self.batch, headers=self.auth)
        self.assertEqual([item['state'] for item in replay.json()['items']], ['reused'] * 4)
        self.assertEqual(sum(key.startswith('documents/') for key in self.storage.reads), 4)

    def test_batch_continues_after_parse_and_object_write_failures(self):
        self.rows[0]['sha256'] = '0' * 64
        self.storage.reject_type = 'cash_value_table'
        with self.assertLogs('baoxian_api.documents', level='ERROR'):
            response = self.client.post(self.batch, headers=self.auth)
        items = response.json()['items']
        self.assertEqual([item['state'] for item in items], ['failed', 'saved', 'failed', 'saved'])
        self.assertEqual(items[0]['reason'], 'file_hash_mismatch')
        self.assertEqual([item['documentId'] for item in self.client.get(self.batch, headers=self.auth).json()['items']], [2, 4])

        self.storage.reject_type = None
        saved = self.client.post('/api/documents/3/saved-layout', headers=self.auth)
        self.assertEqual(saved.status_code, 200, saved.text)
        self.storage.objects[self.rows[2]['storage_key']] = b'invalid'
        self.assertEqual(self.client.post('/api/documents/3/saved-layout?force=true', headers=self.auth).status_code, 422)
        self.assertEqual(self.client.get('/api/documents/3/saved-layout', headers=self.auth).content, saved.content)

    def test_empty_product_and_missing_product_have_distinct_results(self):
        self.assertEqual(self.client.post('/api/products/13/parsed-layouts', headers=self.auth).json(), {'items': []})
        self.assertEqual(self.client.post('/api/products/14/parsed-layouts', headers=self.auth).status_code, 404)
