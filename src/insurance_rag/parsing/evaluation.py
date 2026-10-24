"""Reproducible real-file checks and explicitly labelled raster derivatives."""
import argparse
from collections import Counter
from dataclasses import replace
from difflib import SequenceMatcher
import json
from pathlib import Path
import re

from PIL import ImageDraw
from pypdf import PdfReader
from reportlab.pdfgen.canvas import Canvas
from reportlab.lib.utils import ImageReader

from ..paths import get_project_root
from .common import file_hash, read_json, relative_path, stable_hash, timestamp, workspace_path, write_json, write_jsonl
from .models import ManifestEntry, ParseConfig
from .ocr import render_page
from .pipeline import parse_document
from .tables import NUMERIC


def _numeric_metrics(reference, predicted):
    ref, pred = Counter(NUMERIC.findall(reference)), Counter(NUMERIC.findall(predicted))
    matches = sum((ref & pred).values())
    return dict(reference_count=sum(ref.values()), predicted_count=sum(pred.values()), matched_count=matches,
                recall=matches/sum(ref.values()) if ref else None,
                precision=matches/sum(pred.values()) if pred else None)


def spatial_numeric_comparison(source_file, page_number, blocks):
    """Compare values at the same locations; repeated numbers cannot hide errors."""
    import math
    import pdfplumber
    from collections import defaultdict
    with pdfplumber.open(source_file) as pdf:
        words = pdf.pages[page_number-1].extract_words(x_tolerance=2, y_tolerance=3)
    words = [w for w in words if NUMERIC.fullmatch(w["text"]) and w["bottom"]-w["top"] < 40]
    buckets = defaultdict(list)
    for block in blocks:
        box = block["native_bounding_box"]
        x, y = (box[0]+box[2])/2, (box[1]+box[3])/2
        buckets[(int(x//10), int(y//10))].append((block, x, y))
    detected = exact = 0
    used, examples = set(), []
    for word in words:
        x, y = (word["x0"]+word["x1"])/2, (word["top"]+word["bottom"])/2
        tolerance = max(3, (word["bottom"]-word["top"])*.8)
        radius = math.ceil(tolerance/10)
        nearby = [item for dx in range(-radius, radius+1) for dy in range(-radius, radius+1)
                  for item in buckets[(int(x//10)+dx, int(y//10)+dy)] if item[0]["block_id"] not in used]
        closest = min(nearby, key=lambda item: (x-item[1])**2+(y-item[2])**2, default=None)
        prediction = None
        if closest and math.hypot(x-closest[1], y-closest[2]) <= tolerance:
            block = closest[0]
            prediction = block["raw_text"]
            detected += 1
            used.add(block["block_id"])
            exact += prediction == word["text"]
        if prediction != word["text"] and len(examples) < 20:
            examples.append(dict(reference=word["text"], prediction=prediction,
                                 native_bbox=[word["x0"], word["top"], word["x1"], word["bottom"]]))
    return dict(reference_numeric_words=len(words), detected_at_reference_location=detected,
                exact_value_matches=exact, exact_match_rate=exact/len(words) if words else None,
                detection_coverage=detected/len(words) if words else None,
                discrepancy_examples=examples, reference_status="native_words_proxy_not_human_ground_truth")


def _scan_entry(entry, target, root, source_page, image_sha):
    data = entry.to_dict()
    record = entry.source_record_id + ":raster-evaluation:" + str(source_page)
    identifier = stable_hash(["document-v1", entry.company_id, record, ["direct", 0]])
    digest = file_hash(target)
    data.update(source_record_id=record, document_id=identifier,
                document_version_id=stable_hash(["document-version-v1", identifier, digest]),
                file_path=relative_path(target, root), file_sha256=digest, file_type="pdf",
                parent_raw_file=entry.file_path, parent_sha256=entry.file_sha256,
                archive_member=None, member_index=0, product_id=None, product_version_id=None,
                linkage_status="pending_review", identity_audit={},
                review_reasons=["controlled_raster_derivative_not_downloaded_scan"],
                source_reference=dict(derivation="pdfium_render_then_image_only_pdf",
                    original_document_version_id=entry.document_version_id,
                    original_file_path=entry.file_path, original_sha256=entry.file_sha256,
                    original_page_number=source_page, rendered_image_sha256=image_sha),
                pdf_validation=dict(evaluation_derivative=True))
    return ManifestEntry.from_dict(data)


def _overlay(image, layout, page_number, target):
    image = image.copy()
    draw = ImageDraw.Draw(image)
    page = next(p for p in layout["pages"] if p["page_number"] == page_number)
    sx, sy = image.width/page["width"], image.height/page["height"]
    for table in layout["tables"]:
        if table["page_number"] == page_number:
            for cell in table["cells"]:
                box = cell["bounding_box"]
                draw.rectangle([box[0]*sx, box[1]*sy, box[2]*sx, box[3]*sy], outline="blue", width=1)
    for block in layout["blocks"]:
        if block["page_number"] == page_number and block["block_type"] not in ("image", "table"):
            box = block["bounding_box"]
            draw.rectangle([box[0]*sx, box[1]*sy, box[2]*sx, box[3]*sy], outline="red", width=1)
            draw.text((box[0]*sx, box[1]*sy), str(block["reading_order"]), fill="red")
    image.save(target)


def evaluate(manifest, output, root, scan_pages=3):
    root = Path(root).resolve()
    manifest = workspace_path(manifest, root, must_exist=True)
    output = workspace_path(output, root)
    output.mkdir(parents=True, exist_ok=True)
    entries = [ManifestEntry.from_dict(json.loads(line)) for line in manifest.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    config = ParseConfig(ocr_mode="auto", layout_mode="xy_cut", extract_tables=True)
    results, layouts = [], {}
    for entry in entries:
        result = parse_document(entry, config, workspace_root=root, output=output/"parsed_originals")
        results.append(result)
        if "output" in result:
            layouts[entry.document_version_id] = read_json(root/result["output"]/"layout.json")
        print(json.dumps(dict(phase="original", company=entry.company_id, document_type=entry.document_type,
                              status=result["status"]), ensure_ascii=False), flush=True)
    # One sample per company/category. Huge engineering-size PDF matrices are
    # validated natively; bounded rasterization avoids allocating gigapixel images.
    priority = {"cash_value": 0, "premium_rate": 1, "terms": 2, "brochure": 3}
    candidates = sorted([e for e in entries if e.file_type == "pdf"],
                        key=lambda e: (priority.get(e.document_type, 4), e.company_id))
    scanned, derivatives, used = [], [], set()
    for entry in candidates:
        if len(scanned) >= scan_pages:
            break
        pair = (entry.company_id, entry.document_type)
        if pair in used:
            continue
        layout = layouts.get(entry.document_version_id, {})
        pages = layout.get("pages", [])
        good = [p for p in pages if p.get("rotation", 0) == 0 and p.get("character_count", 0) > 30
                and p["width"]*p["height"]*(config.ocr_dpi/72)**2 <= config.ocr_max_pixels]
        if not good:
            continue
        number = good[0]["page_number"]
        directory = output/"scan_cases"/(entry.document_version_id[:16]+f"_page_{number}")
        directory.mkdir(parents=True, exist_ok=True)
        image = render_page(PdfReader(root/entry.file_path).pages[number-1], config)
        png = directory/"source_render.png"
        image.save(png)
        scan_pdf = directory/"controlled_scan.pdf"
        page = good[0]
        canvas = Canvas(str(scan_pdf), pagesize=(page["width"], page["height"]), invariant=1)
        canvas.drawImage(ImageReader(image), 0, 0, width=page["width"], height=page["height"])
        canvas.save()
        derivative = _scan_entry(entry, scan_pdf, root, number, file_hash(png))
        derivatives.append(derivative.to_dict())
        result = parse_document(derivative, config, workspace_root=root, output=output/"parsed_scans")
        scan_layout = read_json(root/result["output"]/"layout.json")
        predicted = "\n".join(b["raw_text"] for b in scan_layout["blocks"] if b["block_type"] != "image")
        reference = page.get("raw_text", "")
        clean = lambda text: re.sub(r"\s+", "", text)
        ref_numbers = _numeric_metrics(reference, predicted)
        ocr_blocks = [b for b in scan_layout["blocks"] if b["extraction_method"] == "rapidocr_onnx_cpu"]
        _overlay(image, scan_layout, 1, directory/"ocr_overlay.png")
        _overlay(image, layout, number, directory/"native_overlay.png")
        case = dict(company=entry.company_id, document_type=entry.document_type,
                    original_document_version_id=entry.document_version_id, original_page_number=number,
                    sample_kind="controlled_raster_derivative", source_url=entry.source_url,
                    source_sha256=entry.file_sha256, derivative_sha256=derivative.file_sha256,
                    result=result, source_render=relative_path(png, root),
                    ocr_overlay=relative_path(directory/"ocr_overlay.png", root),
                    native_overlay=relative_path(directory/"native_overlay.png", root),
                    ocr_block_count=len(ocr_blocks), ocr_table_count=len(scan_layout["tables"]),
                    low_confidence_count=sum(b["extraction_confidence"] < config.ocr_min_confidence for b in ocr_blocks),
                    native_reference_numeric_token_metrics=ref_numbers,
                    spatial_numeric_comparison=spatial_numeric_comparison(root/entry.file_path, number, ocr_blocks),
                    native_reference_sequence_similarity=SequenceMatcher(None, clean(reference), clean(predicted), autojunk=False).ratio(),
                    metrics_status="native_text_proxy_not_human_ground_truth",
                    review_reasons=scan_layout["pages"][0].get("review_reasons", []))
        scanned.append(case)
        used.add(pair)
        print(json.dumps(dict(phase="controlled_scan", company=entry.company_id,
                              document_type=entry.document_type, blocks=len(ocr_blocks),
                              numeric_recall=ref_numbers["recall"]), ensure_ascii=False), flush=True)
    write_jsonl(output/"scan_manifest.jsonl", derivatives)
    all_pages = [p for l in layouts.values() for p in l["pages"]]
    tables = [t for l in layouts.values() for t in l["tables"]]
    report = dict(schema_version=1, generated_at=timestamp(), input_manifest=relative_path(manifest, root),
                  manifest_sha256=file_hash(manifest), config=config.to_dict(),
                  original_counts=dict(documents=len(entries), pdf_documents=sum(e.file_type == "pdf" for e in entries),
                                       pages=len(all_pages), tables=len(tables), cells=sum(len(t["cells"]) for t in tables)),
                  original_statuses=dict(Counter(r["status"] for r in results)),
                  page_content_types=dict(Counter(p.get("content_type", "failed") for p in all_pages)),
                  table_methods=dict(Counter(t["extraction_method"] for t in tables)),
                  layout_flags=dict(Counter(flag for p in all_pages for flag in p.get("layout_flags", []))),
                  original_results=results, controlled_scan_cases=scanned,
                  scan_request_count=scan_pages, scan_completed_count=len(scanned),
                  limitations=["no_downloaded_scan_in_this_sample", "native_text_reference_is_not_manual_ground_truth",
                               "cross_page_tables_and_business_axes_need_review", "ocr_numbers_not_approved_for_rating"])
    write_json(output/"evaluation_report.json", report)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description="Validate real source PDFs plus explicitly labelled scan derivatives")
    parser.add_argument("--manifest", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--workspace-root")
    parser.add_argument("--scan-pages", type=int, default=3)
    args = parser.parse_args(argv)
    if not 1 <= args.scan_pages <= 20:
        parser.error("--scan-pages must be between 1 and 20")
    report = evaluate(args.manifest, args.output, args.workspace_root or get_project_root(), args.scan_pages)
    print(json.dumps({k: report[k] for k in ("original_counts", "original_statuses", "scan_completed_count")}, ensure_ascii=False))
    failed = any(r["status"] == "failed" for r in report["original_results"])
    failed |= any(not c["ocr_block_count"] or "ocr_failed" in c["review_reasons"] for c in report["controlled_scan_cases"])
    return int(failed or report["scan_completed_count"] < args.scan_pages)
