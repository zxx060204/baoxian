import hashlib
from io import BytesIO
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from baoxian_api.app import create_app
from baoxian_api.documents import DocumentService


def pdf_with_content(content, width=300, height=200):
    writer = PdfWriter()
    page = writer.add_blank_page(width=width, height=height)
    font = DictionaryObject({
        NameObject('/Type'): NameObject('/Font'),
        NameObject('/Subtype'): NameObject('/Type1'),
        NameObject('/BaseFont'): NameObject('/Helvetica'),
    })
    page[NameObject('/Resources')] = DictionaryObject({
        NameObject('/Font'): DictionaryObject({NameObject('/F1'): font}),
    })
    stream = DecodedStreamObject()
    stream.set_data(content)
    page.replace_contents(stream)
    result = BytesIO()
    writer.write(result)
    return result.getvalue()


def table_pdf():
    return pdf_with_content(b"""
        30 10 m 230 10 l 30 40 m 230 40 l 30 70 m 230 70 l 30 100 m 230 100 l
        30 10 m 30 100 l 130 10 m 130 70 l 230 10 m 230 100 l S
        BT /F1 12 Tf 40 115 Td (Numeric table) Tj ET
        BT /F1 12 Tf 45 80 Td (Amount) Tj ET
        BT /F1 12 Tf 45 50 Td (25) Tj ET
        BT /F1 12 Tf 145 50 Td (12.50) Tj ET
        BT /F1 12 Tf 45 20 Td (26) Tj ET
        BT /F1 12 Tf 145 20 Td (16.25) Tj ET
    """)


def text_at(x, y, text):
    return f'BT /F1 8 Tf {x} {y} Td ({text}) Tj ET'


class NumericPdfLayoutTests(unittest.TestCase):
    def setUp(self):
        self.body = table_pdf()
        self.auth = {'Authorization': 'Bearer test-token'}

        class Storage:
            async def get(_, key):
                if key != 'documents/test.pdf':
                    raise AssertionError(key)
                return {'body': self.body}

        self.service = DocumentService('mysql://localhost/product', 'mysql://localhost/crawl', Storage())

    def get_layout(self, kind, digest=None, mime_type='application/pdf'):
        row = dict(
            id=51, type=kind, mime_type=mime_type, sha256=digest or hashlib.sha256(self.body).hexdigest(),
            storage_key='documents/test.pdf',
        )
        with patch.object(self.service, '_query', return_value=[row]):
            with TestClient(create_app(internal_api_token='test-token', document_service=self.service)) as client:
                return client.get('/api/documents/51/layout', headers=self.auth)

    def test_existing_numeric_pdf_returns_compact_grid_with_merges(self):
        for kind, layout_type in [('rate_table', 'premium_rate'), ('cash_value_table', 'cash_value')]:
            with self.subTest(kind=kind):
                response = self.get_layout(kind)
                self.assertEqual(response.status_code, 200, response.text)
                layout = response.json()
                self.assertEqual(layout['schema'], 'mapping-input-v1')
                self.assertEqual(layout['document_type'], layout_type)
                self.assertEqual(layout['source_sha256'], hashlib.sha256(self.body).hexdigest())
                self.assertEqual(layout['tables'][0]['grid'], [
                    [['Amount', 1, 2], -1], [['25', 1, 1], ['12.50', 1, 1]],
                    [['26', 1, 1], ['16.25', 1, 1]],
                ])
                self.assertNotIn('cells', layout['tables'][0])
                self.assertIn('Numeric table', layout['contexts'][0]['text'])

    def test_clauses_and_prospectuses_are_outside_layout_route(self):
        for kind in ('clause', 'prospectus'):
            with self.subTest(kind=kind):
                with patch.object(self.service.storage, 'get', side_effect=AssertionError('unexpected download')):
                    response = self.get_layout(kind)
                self.assertEqual(response.status_code, 415)

    def test_invalid_pdf_and_source_hash_mismatch_are_reported(self):
        response = self.get_layout('rate_table', digest='0' * 64)
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['reason'], 'file_hash_mismatch')
        self.body = b'%PDF-1.4\ninvalid'
        response = self.get_layout('cash_value_table')
        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()['reason'], 'invalid_pdf')

    def test_rate_tail_keeps_aligned_dashes_and_excludes_footer(self):
        commands = []
        for age in range(9):
            for x, label in zip((40, 85, 130, 175, 220), (
                age, 100 + age, 200 + age if age < 6 else '-', 300 + age if age < 6 else '-', 400 + age,
            )):
                commands.append(text_at(x, 155 - 12 * age, label))
        # Sequential age text on the next line is not enough if the values
        # have shifted off the established columns.
        commands += [text_at(40, 47, 9), text_at(110, 47, '-'), text_at(200, 47, 999)]
        self.body = pdf_with_content('\n'.join(commands).encode())
        response = self.get_layout('rate_table')
        self.assertEqual(response.status_code, 200, response.text)
        tables = response.json()['tables']
        full = next(table for table in tables if table['grid'])
        by_age = {row[0][0]: [cell[0] for cell in row] for row in full['grid']}
        self.assertEqual(by_age['8'], ['8', '108', '-', '-', '408'])
        self.assertNotIn('9', by_age)

    def test_cash_parallel_tables_recover_columns_without_merging_contexts(self):
        # The left table's unit and the right table's title sit close enough
        # that a page-wide text line can otherwise join the two contexts.
        commands = [text_at(219, 175, 'CNY')]
        for left, title, base in [(25, 'Male', 100), (247, 'Female', 200)]:
            commands.append(text_at(left, 175, title))
            for x in (left, left + 55, left + 210):
                commands.append(f'{x} 46 m {x} 156 l S')
            for y in range(46, 157, 11):
                commands.append(f'{left} {y} m {left + 210} {y} l S')
            commands.append(text_at(left + 8, 148, 'Policy year'))
            for age in range(5):
                commands.append(text_at(left + 65 + 28 * age, 148, age))
            for year in range(1, 10):
                commands.append(text_at(left + 8, 148 - 11 * year, year))
                for age in range(5):
                    commands.append(text_at(left + 65 + 28 * age, 148 - 11 * year, base + year + age))
        self.body = pdf_with_content('\n'.join(commands).encode(), width=525)
        response = self.get_layout('cash_value_table')
        self.assertEqual(response.status_code, 200, response.text)
        layout = response.json()
        tables = sorted(layout['tables'], key=lambda table: table['bbox'][0])
        self.assertEqual([t['column_count'] for t in tables], [6, 6])
        for table, title, base in zip(tables, ('Male', 'Female'), (100, 200)):
            contents = [[cell[0] for cell in row] for row in table['grid']]
            self.assertIn(['1', *(str(base + age) for age in range(1, 6))], contents)
            self.assertIn(['9', *(str(base + age) for age in range(9, 14))], contents)
            context = next((c for c in layout['contexts'] if c['text'] == title), None)
            self.assertIsNotNone(context, layout['contexts'])
            self.assertEqual([item['table_id'] for item in context['relations']], [table['table_id']])
