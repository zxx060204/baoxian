"""Regressions for package migration and the isolated validation worker."""
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from pypdf import PdfWriter
from insurance_rag.acquisition import crawler
from insurance_rag.paths import get_project_root

ROOT = Path(__file__).resolve().parents[1]


class ProjectTests(unittest.TestCase):
    def test_project_root_is_workspace(self):
        with patch.dict("os.environ", {"INSURANCE_PROJECT_ROOT": str(ROOT)}):
            self.assertEqual(get_project_root(), ROOT)

    def test_root_discovery_without_local_docs(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            package = root / "src" / "insurance_rag"
            package.mkdir(parents=True)
            (root / "pyproject.toml").write_text("[project]", encoding="utf8")
            (package / "__init__.py").touch()
            nested = root / "scripts"
            nested.mkdir()
            with patch.dict("os.environ", {}, clear=True), patch("pathlib.Path.cwd", return_value=nested):
                self.assertEqual(get_project_root(), root.resolve())
            self.assertFalse((root / "docs").exists())

    def test_unrelated_pyproject_is_not_project_root(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            (root / "pyproject.toml").touch()
            with patch.dict("os.environ", {}, clear=True), patch("pathlib.Path.cwd", return_value=root), patch("insurance_rag.paths.__file__", str(root / "unrelated.py")):
                with self.assertRaises(RuntimeError):
                    get_project_root()

    def test_acquisition_cli_entries(self):
        env = os.environ.copy()
        env["PYTHONPATH"] = os.pathsep.join(filter(None, [str(ROOT / "src"), env.get("PYTHONPATH")]))
        commands = [
            [str(ROOT / "scripts" / "crawl.py"), "--help"],
            ["-m", "insurance_rag.acquisition", "--help"],
        ]
        for command in commands:
            with self.subTest(command=command):
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", *command],
                    cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf8", timeout=20,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)
                self.assertIn("--sample-products", completed.stdout)

    def test_isolated_pdf_validation_after_package_move(self):
        with tempfile.TemporaryDirectory() as folder:
            pdf = Path(folder) / "sample.pdf"
            writer = PdfWriter()
            writer.add_blank_page(width=300, height=400)
            with pdf.open("wb") as stream:
                writer.write(stream)
            result = crawler.validate(pdf, Path(folder) / "validation.json")
            self.assertEqual(result["status"], "validated")
            self.assertEqual(result["files"][0]["pdf_validation"]["pages"], 1)
            self.assertEqual(result["files"][0]["file_type"], "pdf")
