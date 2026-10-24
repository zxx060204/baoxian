import copy
import json
from pathlib import Path
import tempfile
import unittest

from insurance_rag.parsing.common import file_hash
from insurance_rag.parsing.identity_evidence import identity_review_draft
from insurance_rag.parsing.reference import choose_numeric_reference,same_numeric_run,symbol_space_evidence
from test_native_workflows import fixture


def continuation_fixture():
    tables=[];blocks=[]
    for page,values in ((1,[20,21]),(2,[22,23])):
        cells=[{'row_index':0,'column_index':0,'raw_text':'投保年龄'},
               {'row_index':0,'column_index':1,'raw_text':'男性保费'}]
        cells += [{'row_index':r+1,'column_index':0,'raw_text':str(v)} for r,v in enumerate(values)]
        tables.append({'table_id':f't{page}','page_number':page,'column_count':2,'cells':cells,
                       'native_bounding_box':[10,50,100,150],'header_rows_candidate':[0]})
        blocks.append({'page_number':page,'block_type':'paragraph','native_bounding_box':[10,10,100,40],
                       'raw_text':'示例保险费率表 单位：人民币元 每1000元 男性 5年交'})
    return {'document_version_id':'d','tables':tables,'blocks':blocks}


class FollowupTests(unittest.TestCase):
    def test_geometry_never_overrules_agreeing_source_engines(self):
        self.assertEqual(choose_numeric_reference('12 34','1234','12 34'),{'12':1,'34':1})
        self.assertEqual(choose_numeric_reference('1 2','12','12'),{'12':1})
        self.assertEqual(choose_numeric_reference('1 2','12','1 2 3'),{'1':1,'2':1})

    def test_geometry_does_not_join_rows_or_separate_columns(self):
        self.assertTrue(same_numeric_run([0,0,1,2],[1.1,0,2,2],2))
        self.assertFalse(same_numeric_run([0,0,1,2],[4,0,5,2],2))
        self.assertFalse(same_numeric_run([0,0,1,2],[1.1,5,2,7],2))

    def test_only_proven_symbol_font_space_is_ignored(self):
        char={'text':'\uf020','fontname':'Wingdings2','x0':0,'top':0,'x1':1,'bottom':2}
        self.assertEqual(len(symbol_space_evidence([char])),1)
        self.assertFalse(symbol_space_evidence([char,{**char,'fontname':'UnknownFont'}]))
        self.assertFalse(symbol_space_evidence([{**char,'text':'\uf021'}]))



    def test_identity_evidence_never_creates_verified_ids(self):
        entry={'company_id':'c','source_product_key':'key','document_version_id':'d','document_type':'terms',
               'product_name':'示例','file_path':'original.pdf','file_sha256':'h'}
        ref={'file_sha256':'h','pages':[{'page_number':1,'reference_text':'示例保险[2026]终身寿险100号'}]}
        draft=identity_review_draft([entry],{'d':ref})
        self.assertTrue(draft['groups'][0]['documents'][0]['body_evidence'])
        self.assertIsNone(draft['mapping_template']['entries'][0]['product_version_id'])
        self.assertEqual(draft['mapping_template']['entries'][0]['status'],'pending_review')





if __name__=='__main__':
    unittest.main()
