import copy
from pathlib import Path
import tempfile
import time
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from insurance_rag.parsing.jobs import JobLedger
from insurance_rag.parsing.runner import run_pending

AUDIT = {"status": "confirmed", "reviewer": "tester", "reviewed_at": "2026-10-11T10:00:00+08:00", "evidence": ["原件第 1 页已逐项核对"]}


def slow_worker(payload, results, memory_mb):
    time.sleep(5)


def successful_worker(payload, results, memory_mb):
    results.put({"status": "completed", "elapsed_seconds": 0.01})


def fixture():
    layout = {"document_version_id": "doc-v1", "metadata": {"document_type": "premium_rate", "product_id": "p1", "product_version_id": "p1-v1"},
        "pages": [{"page_number": 1}], "blocks": [{"page_number": 1, "reading_order": 0, "raw_text": "年龄 男性 年交"}],
        "tables": [{"table_id": "t1", "page_number": 1, "bbox": [0, 0, 100, 100], "row_count": 2, "column_count": 2,
            "cells": [{"cell_id": "age", "row_index": 1, "column_index": 0, "raw_text": "30", "bbox": [0, 50, 50, 100]},
                      {"cell_id": "value", "row_index": 1, "column_index": 1, "raw_text": "1,234.50", "bbox": [50, 50, 100, 100]}]}]}
    mapping = {"schema_version": 1, "document_version_id": "doc-v1", "layout_sha256": "layout-hash", "kind": "premium_rate", "table_id": "t1",
        "row_axis": {"role": "age", "column_index": 0}, "data_rows": {"start": 1, "stop": 2},
        "constants": {"sex": "male", "payment_period": {"kind": "years", "value": 10},
                      "coverage_period": {"kind": "lifetime", "value": None}, "payment_frequency": "annual"},
        "value_columns": [{"column_index": 1, "axes": {}}],
        "unit": {"currency": "CNY", "value_unit": "premium", "basis": {"kind": "sum_assured", "amount": "1000"}},
        "conditions": [], "audit": copy.deepcopy(AUDIT)}
    entry = SimpleNamespace(document_version_id="doc-v1", file_sha256="original-hash", file_path="data/original.pdf")
    return layout, mapping, entry






class JobLedgerTests(unittest.TestCase):
    def test_resume_does_not_rerun_finished_jobs(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "jobs.sqlite3"
            ledger = JobLedger(path)
            job_id = ledger.enqueue({"document": "one"})
            job = ledger.claim()
            ledger.finish(job, "needs_review", {"status": "completed"})
            ledger.close()
            ledger = JobLedger(path)
            try:
                self.assertEqual(ledger.enqueue({"document": "one"}), job_id)
                self.assertIsNone(ledger.claim())
                self.assertFalse(ledger.retry(job_id))
            finally:
                ledger.close()

    def test_failed_job_has_bounded_explicit_retry(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = JobLedger(Path(directory) / "jobs.sqlite3")
            try:
                job_id = ledger.enqueue({"document": "one"})
                job = ledger.claim()
                ledger.finish(job, "failed", {"error": "timeout"})
                self.assertTrue(ledger.retry(job_id, max_attempts=2))
                job = ledger.claim()
                ledger.finish(job, "failed", {"error": "timeout"})
                self.assertFalse(ledger.retry(job_id, max_attempts=2))
            finally:
                ledger.close()

    def test_other_owner_cannot_finish_running_job(self):
        with tempfile.TemporaryDirectory() as directory:
            ledger = JobLedger(Path(directory) / "jobs.sqlite3")
            try:
                ledger.enqueue({"document": "one"})
                job = ledger.claim()
                job["owner"] = "not-the-owner"
                with self.assertRaises(RuntimeError):
                    ledger.finish(job, "failed", {})
            finally:
                ledger.close()


class ProcessRunnerTests(unittest.TestCase):
    def payload(self, root):
        return {"workspace_root": str(root), "output": str(root / "parsed"), "entry": {"document_version_id": "doc-v1"}}

    def test_timeout_stops_worker_and_records_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = JobLedger(root / "jobs.sqlite3")
            try:
                ledger.enqueue(self.payload(root))
                with patch("insurance_rag.parsing.runner._worker", slow_worker):
                    summaries = run_pending(ledger, timeout_seconds=0.1)
                self.assertEqual(summaries[0]["error"], "document_timeout")
                self.assertTrue(summaries[0]["worker_stopped"])
                self.assertEqual(ledger.list_jobs()[0]["state"], "failed")
            finally:
                ledger.close()

    def test_success_still_requires_business_review(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ledger = JobLedger(root / "jobs.sqlite3")
            try:
                ledger.enqueue(self.payload(root))
                with patch("insurance_rag.parsing.runner._worker", successful_worker):
                    summaries = run_pending(ledger, timeout_seconds=30)
                self.assertEqual(summaries[0]["state"], "needs_review")
                self.assertEqual(summaries[0]["publication_status"], "quarantined")
            finally:
                ledger.close()
