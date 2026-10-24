import tempfile,unittest
from pathlib import Path
from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
import pdfplumber
from insurance_rag.parsing.table_evidence import capture,partial_report
from insurance_rag.parsing.tables import make_table
from insurance_rag.parsing.preflight import inspect_page
from insurance_rag.parsing.models import ParseConfig
from pypdf import PdfReader

class PhysicalEvidenceTests(unittest.TestCase):
 def test_grid_gaps_recover_source_words_and_keep_nonkeyword_context(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'t.pdf';pdfmetrics.registerFont(UnicodeCIDFont('STSong-Light'));c=canvas.Canvas(str(p),pagesize=(300,300));c.setFont('STSong-Light',10)
   c.drawString(30,260,'特别约定内容');c.drawString(30,230,'年龄');c.drawString(130,230,'比例');c.drawString(30,210,'成年');c.drawString(130,210,'120%');c.save()
   with pdfplumber.open(p) as pdf:
    page=pdf.pages[0];t=make_table([20,60,220,100],[{'bbox':[20,60,120,80],'text':'年龄'},{'bbox':[120,60,220,80],'text':'比例'},{'bbox':[20,80,120,100],'text':'成年'}],1,[1,0,0,1,0,0],'p',0,'test')
    found=capture(page,[t],[1,0,0,1,0,0],'p');self.assertEqual(len(found[0]['cells']),4);self.assertIn('120%',found[0]['raw_text']);self.assertTrue(any('特别约定内容' in x['raw_text'] for x in found[0]['context_candidates']))
 def test_short_numeric_region_is_retained_without_claiming_continuation(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'t.pdf';c=canvas.Canvas(str(p),pagesize=(300,300));c.setFont('Helvetica',10)
   for row in range(2):
    for col in range(4):c.drawString(20+col*60,270-row*20,str(100+row+col))
   c.save()
   with pdfplumber.open(p) as pdf:found=capture(pdf.pages[0],[],[1,0,0,1,0,0],'p')
   self.assertEqual(len(found),1);self.assertEqual(found[0]['structure_status'],'fragment');self.assertEqual(found[0]['cells'],[]);self.assertIsNone(found[0]['continuation_of']);self.assertEqual(len(found[0]['tokens']),8)
   report=partial_report({'tables':found});self.assertEqual(report[0]['table_id'],found[0]['table_id'])
 def test_one_row_text_grid_is_preserved_as_reported_fragment(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'t.pdf';c=canvas.Canvas(str(p),pagesize=(300,300));c.setFont('Helvetica',10);c.drawString(20,270,'Alpha');c.drawString(120,270,'Beta');c.save()
   with pdfplumber.open(p) as pdf:found=capture(pdf.pages[0],[],[1,0,0,1,0,0],'p',[{'reason':'undersized_table_candidate','native_bounding_box':[10,10,200,40]}])
   self.assertEqual(found[0]['fragment_reason'],'undersized_ruled_table');self.assertEqual([t['text'] for t in found[0]['tokens']],['Alpha','Beta'])
