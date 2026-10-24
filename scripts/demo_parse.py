"""Offline synthetic demonstration; never downloads insurance materials."""
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from PIL import Image
from reportlab.lib.utils import ImageReader
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.pdfgen.canvas import Canvas

from insurance_rag.parsing.common import file_hash, write_jsonl
from insurance_rag.parsing.cli import main

ROOT = Path(__file__).resolve().parents[1]
DEMO = ROOT / "data" / "evaluation" / "synthetic_baseline"


def create_sample(file, scanned=False):
    canvas = Canvas(str(file), pagesize=(400, 500))
    pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))
    canvas.setFont("STSong-Light", 12)
    if scanned:
        image = Image.new("RGB", (400, 500), "white")
        canvas.drawImage(ImageReader(image), 0, 0, width=400, height=500)
    else:
        canvas.drawString(30, 465, "合成测试样本：不代表真实保险条款")
        canvas.setFont("STSong-Light", 16)
        canvas.drawString(30, 420, "第一章 保险责任")
        canvas.setFont("STSong-Light", 12)
        canvas.drawString(30, 380, "第一条 保险金")
        canvas.drawString(30, 350, "不承担责任；等待期30天；金额1,000.00元。")
    canvas.showPage()
    canvas.showPage()
    canvas.save()


def run():
    folder = DEMO / "acquisition" / "合成样本公司"
    raw = folder / "raw"
    raw.mkdir(parents=True, exist_ok=True)
    records = []
    for kind in ("native", "scanned"):
        file = raw / (kind + ".pdf")
        create_sample(file, scanned=kind == "scanned")
        records.append(dict(
            id="synthetic-" + kind, company="合成样本公司", product_name="合成验证产品",
            product_identity="synthetic-" + kind, category="条款", scope="product", status="validated",
            raw_file=str(file), sha256=file_hash(file), source_row={"fixture": True},
            validation_level="synthetic_fixture_not_acquisition_verified",
            files=[dict(file=str(file), file_type="pdf", sha256=file_hash(file), member="direct")],
        ))
    write_jsonl(folder / "checkpoint.jsonl", records)
    manifest_folder = DEMO / "manifests"
    code = main(["manifest", "--input", str(DEMO / "acquisition"), "--output", str(manifest_folder)])
    if code:
        return code
    return main(["parse", "--manifest", str(manifest_folder / "documents.jsonl"),
                 "--output", str(DEMO / "parsed"), "--config", "configs/parsing.json"])


if __name__ == "__main__":
    raise SystemExit(run())
