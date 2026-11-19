from hashlib import sha256
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

from baoxian_api.app import create_app
from baoxian_api.documents import DocumentService
from test_numeric_pdf_layout import pdf_with_content


class TextPdfLayoutTests(unittest.TestCase):
    def parse(self, kind, body):
        class Storage:
            async def get(self, key):
                return {'body': body}

        service = DocumentService('mysql://localhost/product', 'mysql://localhost/crawl', Storage())
        metadata = {'id': 51, 'type': kind, 'sha256': sha256(body).hexdigest(),
                    'mime_type': 'application/pdf', 'storage_key': 'documents/test.pdf'}
        with patch.object(service, '_query', return_value=[metadata]):
            with TestClient(create_app(internal_api_token='token', document_service=service)) as client:
                return client.get('/api/documents/51/parsed-content', headers={'Authorization': 'Bearer token'})

    def test_clause_sections_and_chunks_retain_page_and_source_spans(self):
        pdf = pdf_with_content(b"""
            BT /F1 12 Tf 40 165 Td (1.1 Coverage) Tj ET
            BT /F1 10 Tf 40 145 Td (Coverage starts after 30 days.) Tj ET
            BT /F1 12 Tf 40 110 Td (1.2 Exclusions) Tj ET
            BT /F1 10 Tf 40 90 Td (No payment for the excluded events.) Tj ET
        """)
        response = self.parse('clause', pdf)
        self.assertEqual(response.status_code, 200, response.text)
        content = response.json()
        self.assertEqual(content['document_type'], 'terms')
        self.assertEqual([s['clause_number'] for s in content['sections']], ['1.1', '1.2'])
        chunks = content['chunks']
        self.assertEqual(len(chunks), 2)
        self.assertIn('after 30 days', chunks[0]['raw_text'])
        self.assertTrue(chunks[0]['clause_id'])
        blocks = {b['block_id']: b for b in content['blocks']}
        for chunk in chunks:
            self.assertEqual(chunk['page_number'], 1)
            for span in chunk['source_spans']:
                a, b = span['source_character_range']
                x, y = span['chunk_character_range']
                self.assertEqual(blocks[span['block_id']]['raw_text'][a:b], chunk['raw_text'][x:y])

    def test_prospectus_table_is_a_grid_referenced_from_the_chunks(self):
        pdf = pdf_with_content(b"""
            BT /F1 12 Tf 35 160 Td (Benefit illustration) Tj ET
            BT /F1 10 Tf 35 143 Td (Example conditions are explained above the table.) Tj ET
            30 25 m 230 25 l 30 55 m 230 55 l 30 85 m 230 85 l 30 115 m 230 115 l
            30 25 m 30 115 l 130 25 m 130 115 l 230 25 m 230 115 l S
            BT /F1 10 Tf 45 98 Td (Year) Tj ET
            BT /F1 10 Tf 145 98 Td (Cash value) Tj ET
            BT /F1 10 Tf 45 67 Td (1) Tj ET
            BT /F1 10 Tf 145 67 Td (28.45) Tj ET
            BT /F1 10 Tf 45 38 Td (2) Tj ET
            BT /F1 10 Tf 145 38 Td (35.67) Tj ET
        """)
        response = self.parse('prospectus', pdf)
        self.assertEqual(response.status_code, 200, response.text)
        content = response.json()
        self.assertEqual(content['document_type'], 'brochure')
        table, = content['tables']
        self.assertEqual(table['grid'], [
            [['Year', 1, 1], ['Cash value', 1, 1]],
            [['1', 1, 1], ['28.45', 1, 1]],
            [['2', 1, 1], ['35.67', 1, 1]],
        ])
        self.assertNotIn('cells', table)
        reference, = [c for c in content['chunks'] if c['kind'] == 'table_reference']
        self.assertEqual(reference['table_id'], table['table_id'])
        prose = '\n'.join(c['raw_text'] for c in content['chunks'] if c['kind'] == 'text')
        self.assertIn('Benefit illustration', prose)
        self.assertNotIn('28.45', prose)
