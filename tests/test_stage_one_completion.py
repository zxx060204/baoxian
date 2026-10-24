import copy
import json
import os
import shutil
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from insurance_rag.parsing.chunking import build_chunks
from insurance_rag.parsing.common import file_hash,write_json,filesystem_path
from insurance_rag.parsing.excel import extract_workbook
from insurance_rag.parsing.models import ParseConfig
from insurance_rag.parsing.page_cache import PageCache
from test_native_workflows import fixture,AUDIT
from test_parsing import sample_pdf


def workbook_fixture(path):
    # Minimal OOXML fixture tests file parsing, not an insurance workbook artifact.
    parts={
        '[Content_Types].xml':'<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/><Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/></Types>',
        '_rels/.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/></Relationships>',
        'xl/workbook.xml':'<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="费率" sheetId="1" r:id="rId1"/></sheets></workbook>',
        'xl/_rels/workbook.xml.rels':'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/></Relationships>',
        'xl/worksheets/sheet1.xml':'<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><dimension ref="A1:B3"/><sheetData><row r="1"><c r="A1" t="inlineStr"><is><t>原始费率</t></is></c></row><row r="2"><c r="A2"><v>0</v></c><c r="B2"><f>A2+3</f><v>3</v></c></row><row r="3" hidden="1"><c r="A3"><v>30</v></c><c r="B3"><f>A3*2</f><v/></c></row></sheetData><mergeCells count="1"><mergeCell ref="A1:B1"/></mergeCells></worksheet>'}
    with zipfile.ZipFile(path,'w') as archive:
        for name,data in parts.items():archive.writestr(name,data)


def review_fixture(root):
    layout,mapping,_=fixture();source=root/'source.pdf';source.write_bytes(b'fixture original')
    lp=root/'layout.json';write_json(lp,layout);mapping['layout_sha256']=file_hash(lp)
    gold={'schema_version':1,'document_version_id':'doc-v1','file_sha256':file_hash(source),'source_file':'source.pdf',
        'audit':copy.deepcopy(AUDIT),'pages':[{'page_number':1,'tables':[{'source_bbox':[0,0,100,100],
        'expected_row_count':2,'expected_column_count':2,'cells':[{'row_index':1,'column_index':c['column_index'],
        'raw_text':c['raw_text']} for c in layout['tables'][0]['cells']]}]}]}
    gp=root/'gold.json';write_json(gp,gold)
    index={'cases':[{'case_id':'test','document_version_id':'doc-v1','layout':'layout.json','layout_sha256':file_hash(lp),
        'gold':'gold.json','gold_sha256':file_hash(gp)}]}
    identities={'entries':[{'document_version_id':'doc-v1','file_sha256':file_hash(source),'status':'verified',
        'product_id':'reviewed-product','product_version_id':'reviewed-version',**{k:AUDIT[k] for k in ('reviewer','reviewed_at','evidence')}}]}
    reviews={'entries':[{'mapping':mapping,'record_audit':copy.deepcopy(AUDIT),'value_nature':'unknown'}]}
    return index,identities,reviews,layout


class CompletionTests(unittest.TestCase):
    def test_native_interruption_keeps_completed_pages(self):
        from insurance_rag.parsing.native import extract_document,inspect_page
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);source=root/'source.pdf';sample_pdf(source)
            first=root/'parsed/attempt_0001';first.mkdir(parents=True)
            def interrupt_second(page,reader,config):
                if page.page_number==2:raise KeyboardInterrupt('simulated worker interruption')
                return inspect_page(page,reader,config)
            with patch('insurance_rag.parsing.native.inspect_page',side_effect=interrupt_second):
                with self.assertRaises(KeyboardInterrupt):extract_document(source,ParseConfig(),'key',first)
            second=root/'parsed/attempt_0002';second.mkdir()
            result=extract_document(source,ParseConfig(),'key',second)
            self.assertEqual(result['page_checkpoint_stats'],{'hits':1,'writes':1})
            self.assertEqual(len(result['pages']),2)

    @unittest.skipUnless(os.name=='nt','Windows long-path regression')
    def test_completed_artifacts_are_cached_on_long_windows_paths(self):
        from insurance_rag.parsing.pipeline import _cached,ARTIFACTS
        with tempfile.TemporaryDirectory() as temp:
            base=Path(temp)/('document_'+'a'*80)/('parse_'+'b'*80)/('output_'+'c'*80)
            folder=filesystem_path(base/'attempt_0001');folder.mkdir(parents=True)
            for name in ARTIFACTS:write_json(folder/name,{'status':'completed'})
            write_json(folder/'complete.json',{'manifest_hash':'m','artifact_hashes':{n:file_hash(folder/n) for n in ARTIFACTS}})
            write_json(filesystem_path(base)/'latest.json',{'manifest_hash':'m','status':'completed','attempt':'attempt_0001'})
            try:
                self.assertIsNotNone(_cached(base,'m'))
            finally:
                target=base.parents[1]
                if not target.is_relative_to(Path(temp)):raise ValueError('Fixture cleanup outside temporary root')
                shutil.rmtree(filesystem_path(target))


    def test_workbook_merges_formula_cache_hidden_and_zero(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.xlsx';workbook_fixture(path)
            layout=extract_workbook(path,ParseConfig(extract_tables=True),'hash')
            table=layout['tables'][0];cells=table['cells']
            self.assertEqual((table['row_count'],table['column_count']),(3,2))
            self.assertEqual(cells[0]['column_span'],2)
            self.assertEqual(next(c for c in cells if c['row_index']==1 and c['column_index']==0)['raw_text'],'0')
            self.assertIn('formula_cache_missing',layout['pages'][0]['review_reasons'])
            self.assertIn('hidden_cells',layout['pages'][0]['review_reasons'])
            self.assertEqual(next(c for c in cells if c.get('formula')=='=A2+3')['cached_value'],'3')
            from insurance_rag.parsing.validation import validate_layout
            self.assertEqual(validate_layout(layout),[])


    def test_workbook_resource_limit_is_explicit(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'fixture.xlsx';workbook_fixture(path)
            with self.assertRaisesRegex(ValueError,'limit'):extract_workbook(path,ParseConfig(),'hash',max_cells=2)






    def test_chunks_reconstruct_protected_text_and_obey_budget(self):
        blocks=[{'block_id':f'b{i}','block_type':'paragraph','raw_text':text,'page_number':1,'reading_order':i,
            'parent_section_id':'s','clause_id':'c','bounding_box':[0,0,10,10]} for i,text in enumerate(
            ['不承担责任，金额为123.45元。'*80,'注：仅适用于年度末。'])]
        layout={'document_version_id':'d','blocks':blocks,'metadata':{}}
        result=build_chunks(layout,'hash',500);recovered={b['block_id']:[] for b in blocks}
        for chunk in result['chunks']:
            self.assertLessEqual(len(chunk['raw_text'].encode('utf-8')),500)
            for span in chunk['source_spans']:
                lo,hi=span['chunk_character_range'];recovered[span['block_id']].append(chunk['raw_text'][lo:hi])
        for block in blocks:self.assertEqual(''.join(recovered[block['block_id']]),block['raw_text'])
        self.assertTrue(all(c['publication_status']=='quarantined' for c in result['chunks']))

    def test_page_checkpoint_rejects_corruption_and_configuration_change(self):
        with tempfile.TemporaryDirectory() as temp:
            cache=PageCache(temp,'original');cache.save(1,{'page_number':1,'status':'completed'},[],[])
            self.assertIsNotNone(cache.load(1));self.assertIsNone(PageCache(temp,'changed').load(1))
            p=Path(temp)/'page_000001.json';data=json.loads(p.read_text(encoding='utf-8'));data['payload']['page']['status']='failed'
            write_json(p,data);self.assertIsNone(cache.load(1))

    def test_page_selection_rejects_invalid_inputs(self):
        for numbers in ([1,1],[True],[-1]):
            with self.assertRaises(ValueError):ParseConfig(page_numbers=numbers)
        self.assertEqual(ParseConfig(page_numbers=[1,3]).page_numbers,(1,3))












if __name__=='__main__':unittest.main()
