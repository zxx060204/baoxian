import copy
from pathlib import Path
import tempfile
import unittest
from reportlab.pdfgen.canvas import Canvas

from insurance_rag.parsing.jobs import JobLedger
from insurance_rag.parsing.reference import build_reference,evaluate_reference
from test_native_workflows import fixture


class StageOneTests(unittest.TestCase):
    def test_prose_inside_table_rectangle_outside_cells_is_preserved(self):
        import pdfplumber
        from insurance_rag.parsing.layout import native_lines
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'original.pdf';c=Canvas(str(path))
            c.drawString(30,500,'LEFT');c.drawString(100,500,'RIGHT');c.save()
            with pdfplumber.open(path) as pdf:
                tables=[{'native_bounding_box':[0,0,200,800],
                         'cells':[{'native_bounding_box':[0,0,70,800]}]}]
                lines=native_lines(pdf.pages[0],tables)
                text=' '.join(line['text'] for line in lines)
                self.assertIn('RIGHT',text)
                self.assertNotIn('LEFT',text)

    def test_glyph_index_matches_exact_original_order_and_bounds(self):
        from insurance_rag.parsing.tables import GlyphIndex
        chars=[{'x0':x,'x1':x+2,'top':y,'bottom':y+2,'text':str(i)}
               for i,(x,y) in enumerate([(3,5),(1,1),(9,2),(3,0),(0,5)])]
        index=GlyphIndex(chars)
        for box in ([0,0,10,10],[2,0,10,5],[0,0,2,2],[4,6,5,7]):
            expected=[c for c in chars if box[0]<=(c['x0']+c['x1'])/2<box[2]
                      and box[1]<=(c['top']+c['bottom'])/2<box[3]]
            self.assertEqual(index.within(box),expected)

    def test_decorative_overlapping_boxes_are_not_a_valid_table(self):
        from insurance_rag.parsing.tables import grid_issue
        self.assertEqual(grid_issue([(0,0,20,20),(10,10,30,30)]),'overlapping_table_cell_candidate')
        self.assertIsNone(grid_issue([(0,0,10,10),(10,0,20,10),(0,10,20,20)]))

    def test_public_pdf_with_empty_user_password_is_readable(self):
        from pypdf import PdfReader, PdfWriter
        from insurance_rag.parsing.models import ParseConfig
        from insurance_rag.parsing.native import extract_document
        with tempfile.TemporaryDirectory() as temp:
            plain=Path(temp)/'plain.pdf';encrypted=Path(temp)/'encrypted.pdf'
            c=Canvas(str(plain));c.drawString(30,500,'Original amount 123');c.save()
            writer=PdfWriter();writer.append(PdfReader(plain));writer.encrypt('',owner_password='test-owner')
            with encrypted.open('wb') as output: writer.write(output)
            result=extract_document(encrypted,ParseConfig(),'encrypted-sample')
            self.assertEqual(result['pages'][0]['status'],'completed')
            self.assertIn('123',result['blocks'][0]['raw_text'])





    def test_independent_reference_exposes_changed_value(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'original.pdf';c=Canvas(str(path))
            c.drawString(30,500,'Age 30 Amount 1,234.50');c.save()
            ref=build_reference(path,{'document_version_id':'d','file_sha256':'h','company_id':'c','document_type':'premium_rate','file_path':str(path)})
            layout={'document_version_id':'d','blocks':[{'page_number':1,'block_type':'paragraph','raw_text':'Age 30 Amount 1,234.00'}],'tables':[]}
            result=evaluate_reference(ref,layout)
            self.assertEqual(result['pages'][0]['status'],'coverage_difference')
            self.assertIn('1,234.50',result['pages'][0]['numeric_coverage']['missing'])
            self.assertFalse(result['human_gold'])

    def test_sample_pages_preserve_original_numbers_and_skip_status(self):
        from insurance_rag.parsing.models import ParseConfig
        from insurance_rag.parsing.native import extract_document
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/'original.pdf';c=Canvas(str(path))
            for n in range(4):
                c.drawString(30,500,f'Original page {n+1}');c.showPage()
            c.save()
            result=extract_document(path,ParseConfig(page_selection='first_last'),'sample')
            self.assertEqual(result['page_count'],4)
            self.assertEqual([p['page_number'] for p in result['pages']],[1,2,3,4])
            self.assertEqual([p['status'] for p in result['pages']],['completed','not_processed_sample','not_processed_sample','completed'])
            self.assertEqual({b['page_number'] for b in result['blocks']},{1,4})

    def test_empty_native_text_reference_is_not_a_pass(self):
        ref={'document_version_id':'d','pages':[{'page_number':1,'reference_text':'','numeric_tokens':{},'reference_status':'no_native_text'}]}
        result=evaluate_reference(ref,{'document_version_id':'d','blocks':[],'tables':[]})
        self.assertEqual(result['pages'][0]['status'],'needs_source_review')

    def ledger(self,temp):
        root=Path(temp);ledger=JobLedger(root/'jobs.sqlite3')
        jid=ledger.enqueue({'workspace_root':str(root),'output':str(root/'parsed'),'entry':{'document_version_id':'doc'}})
        job=ledger.claim();ledger.heartbeat(job,99999)
        ledger.db.execute("UPDATE jobs SET updated_at='2000-01-01T00:00:00+00:00'");ledger.db.commit()
        return ledger,jid

    def test_recovery_requires_dead_worker_and_old_heartbeat(self):
        with tempfile.TemporaryDirectory() as temp:
            ledger,jid=self.ledger(temp)
            try:
                results=ledger.recover_stale(lambda pid:False)
                self.assertEqual(results[0]['status'],'pending')
                self.assertEqual(ledger.claim()['job_id'],jid)
            finally:
                ledger.close()

    def test_recovery_never_reclaims_live_or_unknown_worker(self):
        for state in (True,None):
            with self.subTest(state=state),tempfile.TemporaryDirectory() as temp:
                ledger,_=self.ledger(temp)
                try:
                    self.assertEqual(ledger.recover_stale(lambda pid:state)[0]['status'],'skipped_worker_alive_or_unknown')
                    self.assertEqual(ledger.list_jobs()[0]['state'],'running')
                finally:
                    ledger.close()


if __name__=='__main__':
    unittest.main()
