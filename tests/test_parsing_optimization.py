import copy
from pathlib import Path
import tempfile
import unittest
from reportlab.pdfgen.canvas import Canvas
import pypdfium2

from insurance_rag.parsing.layout import order_blocks
from insurance_rag.parsing.native import detect_sections
from insurance_rag.parsing.tables import extract_native_tables,extract_borderless_tables
from insurance_rag.parsing.ocr import short_text_retry
from insurance_rag.parsing.models import ParseConfig
from unittest.mock import patch,Mock
from PIL import Image
import pdfplumber
from types import SimpleNamespace
from test_stage_one_completion import review_fixture


class OptimizationTests(unittest.TestCase):

    def test_short_ocr_retry_preserves_numeric_text_and_records_change(self):
        poly=[[0,0],[40,0],[40,10],[0,10]];large=[[0,0],[80,0],[80,20],[0,20]]
        original=[[poly,'填装说明',.85],[poly,'123.45',.2]]
        engine=Mock(return_value=([[large,'填表说明',.98],[large,'999',.99]],[]))
        with patch('insurance_rag.parsing.ocr.render_page',return_value=Image.new('RGB',(200,200))):
            output,evidence,details=short_text_retry(None,Image.new('RGB',(100,100)),original,ParseConfig(),engine)
        self.assertEqual(output[0][1],'填表说明');self.assertEqual(output[1][1],'123.45')
        self.assertEqual(evidence[0]['original_text'],'填装说明');self.assertEqual(evidence[0]['review_status'],'pending_review')
        self.assertEqual(details['changed_count'],1)

    def test_failed_optional_ocr_retry_keeps_primary_prediction(self):
        original=[[[[0,0],[40,0],[40,10],[0,10]],'原始文字',.8]]
        with patch('insurance_rag.parsing.ocr.render_page',return_value=Image.new('RGB',(200,200))):
            output,evidence,details=short_text_retry(None,Image.new('RGB',(100,100)),original,ParseConfig(),Mock(side_effect=RuntimeError('retry failed')))
        self.assertEqual(output,original);self.assertEqual(evidence,[]);self.assertEqual(details['status'],'retry_failed')



    def test_discarded_decorative_frame_does_not_hide_numeric_table(self):
        words=[dict(text=str(r*10+c)+'.25',x0=20+c*30,x1=30+c*30,top=50+r*12,bottom=58+r*12)
               for r in range(6) for c in range(3)]
        page=SimpleNamespace(page_number=1,chars=[],extract_words=lambda **kwargs:words,
            find_tables=lambda **kwargs:[SimpleNamespace(rows=[None,None],cells=[None,None])])
        tables=extract_native_tables(page,[1,0,0,1,0,0],'fixture',allow_borderless=True)
        self.assertEqual([(t['row_count'],t['column_count']) for t in tables],[(6,3)])




    def test_ocr_form_title_then_each_label_value_row(self):
        blocks=[]
        def add(text,box):blocks.append({'raw_text':text,'block_type':'paragraph','native_bounding_box':box,'extraction_method':'rapidocr_onnx_cpu'})
        add('Form title',[150,20,300,30])
        for row in range(4):
            add('Label'+str(row),[30,45+row*20,80,55+row*20]);add('Value'+str(row),[150,47+row*20,240,57+row*20])
        ordered=order_blocks(blocks,400)
        self.assertEqual([b['raw_text'] for b in ordered],['Form title','Label0','Value0','Label1','Value1','Label2','Value2','Label3','Value3'])

    def test_toc_not_promoted_into_real_sections(self):
        blocks=[{'block_id':str(i),'page_number':p,'block_type':'paragraph','raw_text':text,'normalized_text':text}
                for i,(p,text) in enumerate([(1,'目 录'),(1,'第一条 合同组成'),(2,'第 一 条 合同组成')])]
        sections=detect_sections(blocks,'test')
        self.assertEqual(len(sections),1);self.assertEqual(sections[0]['page_start'],2)
        self.assertEqual(blocks[1]['semantic_role'],'table_of_contents')




if __name__=='__main__':unittest.main()
