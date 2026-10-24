"""Regression fixtures for reading order, spans, OCR geometry and quality gates."""
import copy
import io
import unittest
from unittest.mock import patch

import test_parsing as baseline
from reportlab.pdfgen.canvas import Canvas
from pypdf import PdfReader, PdfWriter
from pypdf.generic import RectangleObject

from insurance_rag.parsing.common import read_json
from insurance_rag.parsing.models import ParseConfig
from insurance_rag.parsing.native import extract_document
from insurance_rag.parsing.pipeline import parse_document
from insurance_rag.parsing.validation import validate_layout


class AdvancedParsingTests(unittest.TestCase):
    setUp = baseline.ParsingTests.setUp
    record = baseline.ParsingTests.record
    manifest = baseline.ParsingTests.manifest
    entry = baseline.ParsingTests.entry

    def parse(self, **config):
        result = parse_document(self.entry(), ParseConfig(**config), workspace_root=self.root)
        return result, read_json(self.root/result["output"]/"layout.json")

    def test_two_columns_read_left_column_before_right(self):
        canvas = Canvas(str(self.pdf), pagesize=(400, 500))
        canvas.setFont("Helvetica", 12)
        for y, left, right in ((450, "Left one", "Right one"),
                               (425, "Left two", "Right two"), (400, "Left three", "Right three")):
            canvas.drawString(25, y, left)
            canvas.drawString(230, y, right)
        canvas.save()
        _, layout = self.parse(layout_mode="xy_cut")
        texts = [b["raw_text"] for b in layout["blocks"]]
        self.assertEqual(texts, ["Left one", "Left two", "Left three",
                                "Right one", "Right two", "Right three"])
        self.assertEqual(validate_layout(layout), [])

    def test_merged_header_and_financial_values_remain_exact(self):
        canvas = Canvas(str(self.pdf), pagesize=(400, 500))
        canvas.setFont("Helvetica", 12)
        for y in (450, 420, 390):
            canvas.line(30, y, 330, y)
        for x in (30, 180, 330):
            canvas.line(x, 390, x, 450)
        canvas.line(105, 390, 105, 420)
        canvas.drawString(45, 430, "Merged header")
        canvas.drawString(195, 430, "Rate")
        canvas.drawString(40, 400, "1,000.00")
        canvas.drawString(115, 400, "30")
        canvas.drawString(200, 400, "100.25")
        canvas.save()
        _, layout = self.parse(layout_mode="xy_cut", extract_tables=True)
        table = layout["tables"][0]
        self.assertEqual((table["row_count"], table["column_count"]), (2, 3))
        merged = next(c for c in table["cells"] if c["raw_text"] == "Merged header")
        self.assertEqual(merged["column_span"], 2)
        text = [c["raw_text"] for c in table["cells"]]
        self.assertIn("1,000.00", text)
        self.assertIn("100.25", text)
        self.assertEqual(validate_layout(layout), [])
        broken = copy.deepcopy(layout)
        broken["tables"][0]["cells"][0]["column_span"] = 100
        with self.assertRaisesRegex(ValueError, "invalid_table_span"):
            validate_layout(broken)

    def test_ocr_confidence_and_rotation_crop_coordinates(self):
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(self.pdf))
        writer.pages[0].cropbox = RectangleObject([20, 30, 380, 480])
        writer.pages[0].rotate(90)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        prediction = [[[[20, 30], [100, 30], [100, 60], [20, 60]], "30 days 100.25", .7]]
        with patch("insurance_rag.parsing.ocr.get_engine", return_value=lambda image: (prediction, [0.1])):
            result, layout = self.parse(ocr_mode="force", ocr_dpi=72, layout_mode="xy_cut")
        block = next(b for b in layout["blocks"] if b["extraction_method"] == "rapidocr_onnx_cpu")
        self.assertEqual(block["bounding_box"], [30, 400, 60, 480])
        self.assertEqual(block["raw_text"], "30 days 100.25")
        self.assertEqual(block["extraction_confidence"], .7)
        self.assertIn("ocr_low_confidence", layout["pages"][0]["review_reasons"])
        self.assertTrue((self.root/result["output"]/"renders/page_0001.png").is_file())

    def test_ocr_failure_preserves_native_evidence(self):
        with patch("insurance_rag.parsing.native.recognize_page", side_effect=RuntimeError("test OCR unavailable")):
            _, layout = self.parse(ocr_mode="force")
        self.assertIn("ocr_failed", layout["pages"][0]["review_reasons"])
        self.assertTrue(any("1,000.00" in b["raw_text"] for b in layout["blocks"]))

    def test_blank_scan_is_reported_as_no_text(self):
        baseline.sample_pdf(self.pdf, scanned=True)
        with patch("insurance_rag.parsing.ocr.get_engine", return_value=lambda image: (None, [0.1])):
            _, layout = self.parse(ocr_mode="auto", layout_mode="xy_cut")
        self.assertIn("ocr_no_text_detected", layout["pages"][0]["review_reasons"])
        self.assertEqual(layout["pages"][1]["content_type"], "blank")

    def test_render_limit_and_config_validation(self):
        with patch("insurance_rag.parsing.ocr.get_engine") as engine:
            _, layout = self.parse(ocr_mode="force", ocr_max_pixels=10)
        engine.assert_not_called()
        self.assertIn("ocr_render_pixel_limit_exceeded", layout["pages"][0]["ocr"]["error"])
        for kwargs in (dict(ocr_mode="unknown"), dict(ocr_dpi=30), dict(extract_tables="yes")):
            with self.assertRaises(ValueError):
                ParseConfig(**kwargs)

    def test_borderless_matrix_preserves_values_and_keeps_axes_pending(self):
        from insurance_rag.parsing.tables import extract_native_tables
        import pdfplumber
        canvas = Canvas(str(self.pdf), pagesize=(400, 500))
        canvas.setFont("Helvetica", 12)
        expected = []
        for row in range(7):
            for col in range(4):
                value = f"{row*10+col}.25"
                expected.append(value)
                canvas.drawRightString(90+col*80, 450-row*24, value)
        canvas.save()
        with pdfplumber.open(self.pdf) as pdf:
            tables = extract_native_tables(pdf.pages[0], [1,0,0,1,0,0], "fixture", True)
        self.assertEqual(len(tables), 1)
        self.assertEqual((tables[0]["row_count"], tables[0]["column_count"]), (7, 4))
        self.assertEqual([c["raw_text"] for c in tables[0]["cells"]], expected)
        self.assertIn("row_column_axes_not_verified", tables[0]["review_reasons"])

    def test_mixed_ocr_does_not_duplicate_native_text(self):
        from PIL import Image
        canvas = Canvas(str(self.pdf), pagesize=(400, 500))
        canvas.setFont("Helvetica", 12)
        canvas.drawString(30, 450, "Native text")
        canvas.drawInlineImage(Image.new("RGB", (400, 300), "white"), 0, 0, 400, 300)
        canvas.save()
        import pdfplumber
        with pdfplumber.open(self.pdf) as pdf:
            b = pdf.pages[0].extract_text_lines()[0]
        box = [[b["x0"], b["top"]], [b["x1"], b["top"]],
               [b["x1"], b["bottom"]], [b["x0"], b["bottom"]]]
        predictions = [[box, "Native text", .99],
                       [[[30, 250], [120, 250], [120, 280], [30, 280]], "Raster text", .95]]
        with patch("insurance_rag.parsing.ocr.get_engine", return_value=lambda image: (predictions, [.1])):
            _, layout = self.parse(ocr_mode="auto", ocr_dpi=72, layout_mode="xy_cut")
        texts = [b["raw_text"] for b in layout["blocks"] if b["block_type"] != "image"]
        self.assertEqual(texts.count("Native text"), 1)
        self.assertIn("Raster text", texts)

    def test_cached_render_corruption_creates_new_attempt(self):
        predictions = [[[[20, 30], [100, 30], [100, 60], [20, 60]], "Evidence", .95]]
        with patch("insurance_rag.parsing.ocr.get_engine", return_value=lambda image: (predictions, [.1])):
            first, _ = self.parse(ocr_mode="force", ocr_dpi=72)
            (self.root/first["output"]/"renders/page_0001.png").write_bytes(b"corrupted")
            second, _ = self.parse(ocr_mode="force", ocr_dpi=72)
        self.assertFalse(second["cached"])
        self.assertNotEqual(first["output"], second["output"])

    def test_partial_grid_splits_numeric_rows_instead_of_giant_cells(self):
        from insurance_rag.parsing.tables import extract_native_tables
        import pdfplumber
        canvas = Canvas(str(self.pdf), pagesize=(400, 500))
        canvas.setFont("Helvetica", 12)
        for x in (30, 140, 250, 360):
            canvas.line(x, 220, x, 470)
        for y in (220, 450, 470):
            canvas.line(30, y, 360, y)
        for row in range(8):
            for col in range(3):
                canvas.drawString(50+col*110, 430-row*24, f"{row*10+col}.5")
        canvas.save()
        with pdfplumber.open(self.pdf) as pdf:
            tables = extract_native_tables(pdf.pages[0], [1,0,0,1,0,0], "fixture", True)
        self.assertEqual((tables[0]["row_count"], tables[0]["column_count"]), (8, 3))
        self.assertTrue(all("\n" not in c["raw_text"] for c in tables[0]["cells"]))

    def test_partial_grid_keeps_header_and_sparse_terminal_row(self):
        canvas=Canvas(str(self.pdf),pagesize=(400,500))
        canvas.setFont('Helvetica',10)
        for x in (30,140,250,360):
            canvas.line(x,220,x,470)
        for y in (220,450,470):
            canvas.line(30,y,360,y)
        for col,value in enumerate(('Age','Male','Female')):
            canvas.drawString(45+col*110,456,value)
        for row in range(8):
            canvas.drawString(45,432-row*25,str(row))
            canvas.drawString(155,432-row*25,'0' if row==7 else str(100+row))
            if row<7:
                canvas.drawString(265,432-row*25,str(200+row))
        canvas.save()
        import pdfplumber
        from insurance_rag.parsing.tables import extract_native_tables
        with pdfplumber.open(self.pdf) as pdf:
            table=extract_native_tables(pdf.pages[0],[1,0,0,1,0,0],'fixture',True)[0]
        self.assertEqual((table['row_count'],table['column_count']),(9,3))
        self.assertEqual([c['raw_text'] for c in table['cells'][:3]],['Age','Male','Female'])
        self.assertEqual([c['raw_text'] for c in table['cells'][-3:]],['7','0',''])
        self.assertTrue(all(c['row_span']==1 and c['column_span']==1 for c in table['cells']))

    def test_tiny_partial_grid_and_outside_unit_do_not_merge_rows(self):
        canvas=Canvas(str(self.pdf),pagesize=(400,500))
        canvas.setFont('Helvetica',3)
        canvas.drawString(30,475,'Unit: CNY')
        for x in (30,60,90,120):
            canvas.line(x,409,x,466)
        for y in (409,460,466):
            canvas.line(30,y,120,y)
        for col,value in enumerate(('Year','25','26')):
            canvas.drawString(34+col*30,462,value)
        for row in range(12):
            for col in range(3):
                canvas.drawString(34+col*30,455-row*4,str(row if col==0 else row*10+col))
        canvas.save()
        import pdfplumber
        from insurance_rag.parsing.tables import extract_native_tables
        from insurance_rag.parsing.layout import native_lines
        with pdfplumber.open(self.pdf) as pdf:
            tables=extract_native_tables(pdf.pages[0],[1,0,0,1,0,0],'fixture',True)
            table=tables[0]
            lines=native_lines(pdf.pages[0],tables)
        self.assertEqual((table['row_count'],table['column_count']),(13,3))
        self.assertEqual([c['raw_text'] for c in table['cells'][-3:]],['11','111','112'])
        self.assertTrue(any('Unit: CNY' in line['text'] for line in lines))
        self.assertNotIn('Unit:',table['raw_text'])

    def test_borderless_sparse_tail_keeps_zero_and_blank_cells(self):
        canvas=Canvas(str(self.pdf),pagesize=(400,500))
        canvas.setFont('Helvetica',10)
        for row in range(9):
            canvas.drawString(30,450-row*20,str(row+1))
            for col in range(1,4):
                if row<7 or col==1:
                    canvas.drawString(30+col*80,450-row*20,'0' if row>=7 else str(row*100+col))
        canvas.save()
        import pdfplumber
        from insurance_rag.parsing.tables import extract_native_tables
        with pdfplumber.open(self.pdf) as pdf:
            table=extract_native_tables(pdf.pages[0],[1,0,0,1,0,0],'fixture',True)[0]
        self.assertEqual((table['row_count'],table['column_count']),(9,4))
        self.assertEqual([c['raw_text'] for c in table['cells'][-4:]],['9','0','',''])

    def test_deep_output_directory_supports_atomic_artifacts(self):
        from insurance_rag.parsing.common import filesystem_path
        self.tmp.name = str(filesystem_path(self.root))
        output = self.root/("long_output_"+"x"*60)/("nested_"+"y"*60)
        result = parse_document(self.entry(), workspace_root=self.root, output=output)
        from insurance_rag.parsing.common import filesystem_path
        folder = filesystem_path(self.root/result["output"])
        self.assertTrue((folder/"complete.json").exists())
        self.assertEqual(read_json(folder/"layout.json")["page_count"], 2)
        cached = parse_document(self.entry(), workspace_root=self.root, output=output)
        self.assertTrue(cached["cached"])

    def test_tall_column_ocr_splits_rows_and_keeps_detection_evidence(self):
        from PIL import Image, ImageDraw
        from insurance_rag.parsing.ocr import split_tall_predictions
        image = Image.new("RGB", (80, 180), "white")
        draw = ImageDraw.Draw(image)
        for y in (10, 40, 70, 100, 130):
            draw.rectangle([15, y, 30, y+6], fill="black")
        class Recognizer:
            def text_rec(self, crops):
                return [("100.25", .95) for _ in crops], .1
        polygon = [[10, 5], [35, 5], [35, 145], [10, 145]]
        result, evidence, elapsed = split_tall_predictions(image, [[polygon, "bad vertical prediction", .3]], Recognizer())
        self.assertEqual(len(result), 5)
        self.assertEqual(evidence[0]["raw_text"], "bad vertical prediction")
        self.assertTrue(all(item[1] == "100.25" and item[0][2][1]-item[0][0][1] < 20 for item in result))

    def test_structural_failure_keeps_full_page_evidence(self):
        with patch("insurance_rag.parsing.pipeline.validate_layout", side_effect=ValueError("invalid_table_span")):
            result, layout = self.parse(layout_mode="xy_cut")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(layout["page_count"], 2)
        self.assertEqual(len(layout["pages"]), 2)
        self.assertTrue(layout["blocks"])

    def test_ocr_row_jitter_preserves_first_and_last_table_rows(self):
        from types import SimpleNamespace
        from insurance_rag.parsing.tables import raster_numeric_candidates
        blocks = []
        for row in range(25):
            for col in range(3):
                top = 100.8+row*12+(col%2)*.5
                blocks.append(dict(block_id=f"{row}-{col}", raw_text=str(row*10+col),
                                   native_bounding_box=[30+col*80, top, 55+col*80, top+7],
                                   extraction_confidence=.99))
        tables = raster_numeric_candidates(blocks, SimpleNamespace(page_number=1), [1,0,0,1,0,0], "test")
        self.assertEqual((tables[0]["row_count"], tables[0]["column_count"]), (25, 3))
        self.assertEqual([c["raw_text"] for c in tables[0]["cells"][-3:]], ["240", "241", "242"])
        self.assertTrue(all(len(c["source_block_ids"]) == 1 for c in tables[0]["cells"]))
