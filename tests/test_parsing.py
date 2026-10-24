"""Offline integration tests: provenance, failure isolation, all pages, and caches."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen.canvas import Canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont

from insurance_rag.parsing.common import file_hash, read_json, stable_hash, write_json, write_jsonl
from insurance_rag.parsing.manifest import build_manifest, export_manifest
from insurance_rag.parsing.models import ManifestEntry, ParseConfig
from insurance_rag.parsing.native import detect_sections
from insurance_rag.parsing.pipeline import parse_document, parse_manifest
from insurance_rag.parsing.preflight import transform_bbox
from insurance_rag.parsing.validation import validate_layout


def sample_pdf(file, *, blank=False, scanned=False):
    canvas = Canvas(str(file), pagesize=(400, 500))
    if scanned:
        image = Image.new("RGB", (400, 500), "white")
        canvas.drawInlineImage(image, 0, 0, width=400, height=500)
    elif not blank:
        pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
        canvas.setFont("STSong-Light", 14)
        canvas.drawString(30, 450, "第一章 保险责任")
        canvas.drawString(30, 410, "第一条 保险金")
        canvas.drawString(30, 380, "不承担责任；等待期30天；金额1,000.00元。")
        canvas.setFont("Helvetica", 12)
        canvas.drawString(30, 340, "Rate 100.25 CNY")
    canvas.showPage()
    canvas.showPage()
    canvas.save()


class ParsingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.run = self.root / "data" / "acquisition" / "sample"
        self.company = self.run / "测试公司"
        self.raw = self.company / "raw"
        self.raw.mkdir(parents=True)
        self.pdf = self.raw / "sample.pdf"
        sample_pdf(self.pdf)

    def record(self, *, company="测试公司", identifier="candidate1", file=None, member="direct", category="条款"):
        file = file or self.pdf
        digest = file_hash(file)
        return dict(id=identifier, company=company, product_identity="同名产品@1", product_name="测试终身保险",
                    category=category, scope="product", status="validated", url="https://example.com/file",
                    source_catalog="https://example.com/catalog", source_row={"versionNo": "1"},
                    updated_at="2026-10-10T01:00:00+00:00", raw_file=str(file), sha256=digest,
                    validation_level="partial_after_parser_timeout",
                    files=[dict(file=str(file), file_type=file.suffix[1:], sha256=digest, member=member,
                                pdf_validation={"text_scope": "first_and_last_pages_only"})])

    def manifest(self, rows=None):
        write_jsonl(self.company / "checkpoint.jsonl", rows or [self.record()])
        return build_manifest(self.run, self.root)

    def entry(self):
        return ManifestEntry.from_dict(self.manifest()["documents"][0])

    def test_manifest_preserves_metadata_and_quarantines_unknown_version(self):
        entry = self.entry()
        self.assertEqual(entry.source_url, "https://example.com/file")
        self.assertEqual(entry.validation_level, "partial_after_parser_timeout")
        self.assertEqual(entry.source_row["versionNo"], "1")
        self.assertIsNone(entry.download_time)
        self.assertIsNone(entry.product_version_id)
        self.assertEqual(entry.linkage_status, "pending_review")

    def test_latest_failure_supersedes_success_and_summary(self):
        success = self.record()
        failure = dict(success, status="failed", failure_kind="invalid_file")
        write_json(self.company / "summary.json", {"records": [success]})
        result = self.manifest([success, failure])
        self.assertEqual(result["documents"], [])
        self.assertEqual(result["excluded"][0]["acquisition_status"], "failed")
        self.assertTrue(result["warnings"])

    def test_summary_fallback(self):
        write_json(self.company / "summary.json", {"records": [self.record()]})
        result = build_manifest(self.run, self.root)
        self.assertEqual(result["counts"]["documents"], 1)

    def test_checkpoint_tail_recovery_is_explicit_and_bounded(self):
        checkpoint = self.company / "checkpoint.jsonl"
        checkpoint.write_text(json.dumps(self.record(), ensure_ascii=False) + '\n{"id":', encoding="utf8")
        with self.assertRaisesRegex(ValueError, "invalid_checkpoint_json"):
            build_manifest(self.run, self.root)
        result = build_manifest(self.run, self.root, recover_tail=True)
        self.assertEqual(result["counts"]["documents"], 1)
        checkpoint.write_text('{"id":\n' + json.dumps(self.record()), encoding="utf8")
        with self.assertRaisesRegex(ValueError, "invalid_checkpoint_json"):
            build_manifest(self.run, self.root, recover_tail=True)

    def test_active_acquisition_is_rejected(self):
        self.manifest()
        (self.run / "active.lock").write_text("123")
        with self.assertRaisesRegex(ValueError, "acquisition_run_active"):
            build_manifest(self.run, self.root)

    def test_missing_files_and_changed_hash_are_excluded(self):
        row = self.record()
        row["files"][0]["sha256"] = "a" * 64
        result = self.manifest([row])
        self.assertIn("file_hash_mismatch", result["excluded"][0]["reason"])
        row["files"] = []
        result = self.manifest([row])
        self.assertEqual(result["excluded"][0]["reason"], "validated_record_has_no_files")

    def test_outside_workspace_file_is_excluded(self):
        with tempfile.TemporaryDirectory() as folder:
            outside = Path(folder) / "outside.pdf"
            sample_pdf(outside)
            result = self.manifest([self.record(file=outside)])
        self.assertTrue(result["excluded"])
        self.assertIn("path_outside_workspace", result["excluded"][0]["reason"])

    def test_shared_file_keeps_company_associations(self):
        result = self.manifest([self.record(company="公司A", identifier="a"),
                                self.record(company="公司B", identifier="b")])
        a, b = result["documents"]
        self.assertEqual(a["file_sha256"], b["file_sha256"])
        self.assertNotEqual(a["source_product_key"], b["source_product_key"])
        self.assertNotEqual(a["document_version_id"], b["document_version_id"])

    def test_archive_members_remain_distinct(self):
        row = self.record(member="first.pdf")
        row["files"].append(dict(row["files"][0], member="second.pdf"))
        result = self.manifest([row])
        self.assertEqual(result["counts"]["documents"], 2)
        self.assertNotEqual(result["documents"][0]["document_id"], result["documents"][1]["document_id"])
        self.assertEqual(result["documents"][1]["archive_member"], "second.pdf")

    def test_mapping_is_bound_to_document_version_and_requires_audit(self):
        entry = self.entry()
        mapping = self.root / "mapping.json"
        verified = dict(document_version_id=entry.document_version_id, status="verified", product_id="P1",
                        product_version_id="PV1", reviewer="tester", reviewed_at="2026-10-10", evidence=["条款编号核验"])
        write_json(mapping, dict(schema_version=1, entries=[verified]))
        result = build_manifest(self.run, self.root, mapping)
        self.assertEqual(result["documents"][0]["product_version_id"], "PV1")
        verified.pop("reviewer")
        write_json(mapping, dict(schema_version=1, entries=[verified]))
        with self.assertRaisesRegex(ValueError, "missing_mapping_reviewer"):
            build_manifest(self.run, self.root, mapping)

    def test_company_scope_never_inherits_product(self):
        row = self.record()
        row["scope"] = "company"
        result = self.manifest([row])
        self.assertEqual(result["documents"][0]["linkage_status"], "not_applicable")
        self.assertIsNone(result["documents"][0]["product_id"])

    def test_native_parses_all_pages_and_protects_chinese_and_numbers(self):
        entry = self.entry()
        result = parse_document(entry, workspace_root=self.root)
        folder = self.root / result["output"]
        layout = read_json(folder / "layout.json")
        text = "\n".join(b["raw_text"] for b in layout["blocks"])
        self.assertIn("不承担责任", text)
        self.assertIn("30天", text)
        self.assertIn("1,000.00元", text)
        self.assertEqual([p["page_number"] for p in layout["pages"]], [1, 2])
        self.assertEqual(layout["pages"][1]["content_type"], "blank")
        self.assertTrue(layout["sections"])
        self.assertEqual(result["publication_status"], "quarantined")
        self.assertEqual(read_json(folder / "tables.json")["extraction_status"], "not_implemented")
        self.assertEqual(validate_layout(layout), [])
        for block in layout["blocks"]:
            self.assertEqual(block["raw_text"], block["normalized_text"])
            self.assertEqual(block["original_reference"]["document_version_id"], entry.document_version_id)

    def test_rotated_coordinates_return_to_unrotated_page(self):
        source = self.pdf.read_bytes()
        writer = PdfWriter()
        writer.clone_document_from_reader(PdfReader(self.pdf))
        writer.pages[0].rotate(90)
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        result = parse_document(self.entry(), workspace_root=self.root)
        layout = read_json(self.root / result["output"] / "layout.json")
        first = layout["pages"][0]
        self.assertEqual((first["width"], first["height"], first["rotation"]), (400, 500, 90))
        for block in layout["blocks"]:
            if block["page_number"] == 1:
                self.assertLessEqual(block["bounding_box"][2], 400)
                self.assertLessEqual(block["bounding_box"][3], 500)
        self.assertTrue(source)
        # Independent affine check, including the full rotated page rectangle.
        self.assertEqual(transform_bbox([0, 0, 500, 400], first["native_to_standard"]), [0, 0, 400, 500])

    def test_scanned_page_is_not_empty_success(self):
        sample_pdf(self.pdf, scanned=True)
        result = parse_document(self.entry(), workspace_root=self.root)
        layout = read_json(self.root / result["output"] / "layout.json")
        self.assertEqual(layout["pages"][0]["status"], "needs_ocr")
        self.assertEqual(result["status"], "completed_with_warnings")
        self.assertTrue(any(b["block_type"] == "image" for b in layout["blocks"]))

    def test_encrypted_pdf_fails_explicitly(self):
        writer = PdfWriter()
        writer.add_blank_page(width=400, height=500)
        writer.encrypt("secret")
        with self.pdf.open("wb") as stream:
            writer.write(stream)
        result = parse_document(self.entry(), workspace_root=self.root)
        report = read_json(self.root / result["output"] / "parse_report.json")
        self.assertEqual(result["status"], "failed")
        self.assertIn("encrypted_pdf_not_supported", str(report["errors"]))
        self.assertFalse((self.root / result["output"] / "complete.json").exists())

    def test_page_limit_keeps_every_page_status(self):
        result = parse_document(self.entry(), ParseConfig(max_pages=1), workspace_root=self.root)
        report = read_json(self.root / result["output"] / "parse_report.json")
        self.assertEqual(result["status"], "failed")
        self.assertEqual(report["page_count"], report["page_status_count"])
        self.assertEqual(report["processed_page_count"], 0)
        self.assertEqual(report["page_statuses"], {"not_processed_limit": 2})

    def test_cache_integrity_and_force_preserve_attempts(self):
        entry = self.entry()
        first = parse_document(entry, workspace_root=self.root)
        cached = parse_document(entry, workspace_root=self.root)
        self.assertTrue(cached["cached"])
        (self.root / first["output"] / "content.md").write_text("corrupted", encoding="utf8")
        second = parse_document(entry, workspace_root=self.root)
        self.assertFalse(second["cached"])
        third = parse_document(entry, workspace_root=self.root, force=True)
        self.assertNotEqual(second["output"], third["output"])
        self.assertTrue((self.root / second["output"] / "complete.json").exists())

    def test_input_hash_is_checked_before_cache(self):
        entry = self.entry()
        parse_document(entry, workspace_root=self.root)
        self.pdf.write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "file_hash_mismatch"):
            parse_document(entry, workspace_root=self.root)

    def test_manifest_metadata_change_does_not_reuse_stale_provenance(self):
        entry = self.entry()
        first = parse_document(entry, workspace_root=self.root)
        changed = entry.to_dict()
        changed["source_updated_at"] = "2026-10-11"
        second = parse_document(changed, workspace_root=self.root)
        self.assertFalse(second["cached"])
        self.assertNotEqual(first["output"], second["output"])

    def test_failure_isolation_and_retry_failed(self):
        good = self.record()
        bad_file = self.raw / "bad.pdf"
        bad_file.write_bytes(b"%PDF-not-valid")
        result = self.manifest([self.record(file=bad_file, identifier="bad"), good])
        export_manifest(result, self.root / "manifests", self.root)
        report = parse_manifest(self.root / "manifests/documents.jsonl", self.root)
        self.assertEqual(report["counts"], {"failed": 1, "completed": 1})
        retry = parse_manifest(self.root / "manifests/documents.jsonl", self.root, retry_failed=True)
        self.assertEqual(len(retry["results"]), 1)
        self.assertEqual(retry["results"][0]["status"], "failed")

    def test_unsupported_format_is_retained(self):
        file = self.raw / "rate.html"
        file.write_bytes(b"retained unsupported material")
        entry = ManifestEntry.from_dict(self.manifest([self.record(file=file, category="费率表")])["documents"][0])
        result = parse_document(entry, workspace_root=self.root)
        self.assertEqual(result["status"], "unsupported_format")
        self.assertEqual(result["publication_status"], "quarantined")

    def test_page_exception_retains_status_and_continues(self):
        with patch("insurance_rag.parsing.native.inspect_page", side_effect=[RuntimeError("first-page-failed"), dict(
            page_number=2, width=400, height=500, native_to_standard=[1,0,0,1,0,0], content_type="blank",
            review_reasons=[], rotation=0, missing_image_bounds=0)]):
            result = parse_document(self.entry(), workspace_root=self.root)
        layout = read_json(self.root / result["output"] / "layout.json")
        self.assertEqual([p["status"] for p in layout["pages"]], ["failed", "completed"])
        self.assertEqual(result["status"], "failed")

    def test_model_rejects_forged_identity_and_config(self):
        entry = self.entry().to_dict()
        entry["document_version_id"] = "b" * 64
        with self.assertRaisesRegex(ValueError, "document_version_id_mismatch"):
            ManifestEntry.from_dict(entry)
        with self.assertRaises(ValueError):
            ParseConfig(image_coverage_threshold=2)

    def test_corrupt_manifest_row_does_not_stop_next_document(self):
        data = self.manifest()
        manifest = self.root / "manifest.jsonl"
        manifest.write_text("{invalid}\n" + json.dumps(data["documents"][0], ensure_ascii=False), encoding="utf8")
        result = parse_manifest(manifest, self.root)
        self.assertEqual(result["counts"], {"completed": 1, "failed_input": 1})

    def test_parse_lock_is_respected(self):
        entry = self.entry()
        result = parse_document(entry, workspace_root=self.root)
        base = (self.root / result["output"]).parent
        (base / "active.lock").write_text("running")
        with self.assertRaisesRegex(ValueError, "parse_task_locked"):
            parse_document(entry, workspace_root=self.root)

    def test_invalid_metadata_is_rejected_without_crashing_batch(self):
        entry = self.entry().to_dict()
        entry.update(linkage_status="verified", product_id="P", product_version_id="PV", identity_audit=[])
        manifest = self.root / "invalid.jsonl"
        write_jsonl(manifest, [entry])
        report = parse_manifest(manifest, self.root)
        self.assertEqual(report["counts"], {"failed_input": 1})

    def test_duplicate_manifest_versions_are_both_rejected(self):
        entry = self.entry().to_dict()
        manifest = self.root / "duplicates.jsonl"
        write_jsonl(manifest, [entry, dict(entry, product_name="Conflicting label")])
        report = parse_manifest(manifest, self.root)
        self.assertEqual(report["results"], [])
        self.assertEqual(report["counts"], {"failed_input": 2})

    def test_all_rotations_and_crop_preserve_media_coordinates(self):
        import io
        from pypdf.generic import RectangleObject
        source = self.pdf.read_bytes()
        base = parse_document(self.entry(), workspace_root=self.root)
        base_layout = read_json(self.root / base["output"] / "layout.json")
        original = [b["bounding_box"] for b in base_layout["blocks"] if b["page_number"] == 1]
        expected = [min(b[0] for b in original), min(b[1] for b in original),
                    max(b[2] for b in original), max(b[3] for b in original)]
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation):
                writer = PdfWriter()
                writer.clone_document_from_reader(PdfReader(io.BytesIO(source)))
                writer.pages[0].cropbox = RectangleObject([20, 30, 380, 480])
                writer.pages[0].rotate(rotation)
                with self.pdf.open("wb") as stream:
                    writer.write(stream)
                parsed = parse_document(self.entry(), workspace_root=self.root)
                layout = read_json(self.root / parsed["output"] / "layout.json")
                boxes = [b["bounding_box"] for b in layout["blocks"] if b["page_number"] == 1]
                actual = [min(b[0] for b in boxes), min(b[1] for b in boxes),
                          max(b[2] for b in boxes), max(b[3] for b in boxes)]
                for a, e in zip(actual, expected):
                    self.assertAlmostEqual(a, e, places=2)
                self.assertEqual(layout["pages"][0]["crop_box_pdf"], [20, 30, 380, 480])

    def test_cli_pipeline_from_source_checkout(self):
        import subprocess
        import sys
        self.manifest()
        project = Path(__file__).resolve().parents[1]
        manifest_folder = self.root / "cli_manifests"
        commands = [
            ["manifest", "--input", str(self.run), "--output", str(manifest_folder)],
            ["parse", "--manifest", str(manifest_folder / "documents.jsonl"), "--output", "cli_parsed"],
        ]
        for arguments in commands:
            with self.subTest(command=arguments[0]):
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", str(project / "scripts/parse.py"),
                     *arguments, "--workspace-root", str(self.root)],
                    cwd=project, capture_output=True, text=True, encoding="utf8", timeout=30,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr + completed.stdout)
                report = json.loads(completed.stdout)
                if arguments[0] == "parse":
                    self.assertEqual(report["counts"], {"completed": 1})
