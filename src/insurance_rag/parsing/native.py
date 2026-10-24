"""Full-page native baseline using the already installed PDF libraries."""
import importlib.metadata
import re

import pdfplumber
from pypdf import PdfReader

from .common import stable_hash,file_hash
from .page_cache import PageCache
from .preflight import inspect_page, transform_bbox
from .layout import native_lines, order_blocks
from .tables import extract_native_tables, extract_raster_tables
from .ocr import ocr_versions, recognize_page


def engine_versions(config=None):
    versions = {name: importlib.metadata.version(name) for name in ("pdfplumber", "pypdf", "pdfminer.six")}
    from .excel import excel_versions
    versions.update(excel_versions())
    if config and config.ocr_mode != "off":
        versions.update(ocr_versions())
    return versions


def extract_document(file, config, parse_key, artifact_dir=None, document_type=None):
    with open(file, "rb") as stream:
        if stream.read(5) != b"%PDF-":
            raise ValueError("invalid_pdf_signature")
    reader = PdfReader(file, strict=True)
    if reader.is_encrypted and not reader.decrypt(""):
        raise ValueError("encrypted_pdf_not_supported")
    count = len(reader.pages)
    if not count:
        raise ValueError("zero_pages")
    if any(n>count for n in config.page_numbers):raise ValueError('selected_page_out_of_range')
    if count > config.max_pages:
        return dict(page_count=count, pages=[
            dict(page_number=i+1, status="not_processed_limit", error="max_pages_exceeded",
                 review_reasons=["max_pages_exceeded"]) for i in range(count)
        ], blocks=[], sections=[], tables=[], errors=["max_pages_exceeded"])
    pages, blocks, tables = [], [], []
    cache=None
    # OCR render artifacts remain attempt-bound; native pages can be resumed
    # independently without moving or changing earlier evidence.
    if artifact_dir is not None and config.page_checkpoint and config.ocr_mode=='off':
        cache=PageCache(artifact_dir.parent/'page_checkpoints',
            stable_hash([file_hash(file),parse_key,config.to_dict(),engine_versions(config)]))
    with pdfplumber.open(file) as pdf:
        if len(pdf.pages) != count:
            raise ValueError("page_count_mismatch")
        for index, page in enumerate(pdf.pages):
            if ((config.page_selection == "first_last" and index not in (0, count - 1)) or
                (config.page_numbers and index+1 not in config.page_numbers)):
                pages.append(dict(page_number=index+1, status="not_processed_sample",
                                  review_reasons=["evaluation_page_sampling"]))
                page.close()
                continue
            cached=cache.load(index+1) if cache else None
            if cached:
                pages.append(cached['page']);blocks.extend(cached['blocks']);tables.extend(cached['tables'])
                page.close();continue
            try:
                info = inspect_page(page, reader.pages[index], config)
                matrix = info["native_to_standard"]
                rejected = []
                page_tables = extract_native_tables(page, matrix, parse_key, allow_borderless=document_type in ("terms", "brochure", "premium_rate", "cash_value"), rejected=rejected) if config.extract_tables else []
                if config.extract_tables:
                    from .table_evidence import capture
                    page_tables = capture(page, page_tables, matrix, parse_key, rejected)
                if rejected:
                    info["table_detection_rejections"] = rejected
                    info["review_reasons"].append("invalid_table_candidate_retained_as_text")
                lines = native_lines(page, page_tables) if config.layout_mode == "xy_cut" else page.extract_text_lines(strip=False, return_chars=True)
                info["raw_text"] = page.extract_text() or ""
                info["status"] = "needs_ocr" if info["content_type"] == "scanned" else (
                    "completed_with_warnings" if info["review_reasons"] else "completed")
                info["error"] = None
                page_blocks = []
                for ordinal, line in enumerate(lines):
                    raw = line["text"]
                    normalized = raw.replace("\r\n", "\n").replace("\r", "\n")
                    bbox = [line["x0"], line["top"], line["x1"], line["bottom"]]
                    page_blocks.append(dict(
                        block_id=stable_hash(["block-v1", parse_key, index+1, "text", ordinal]),
                        block_type="paragraph", raw_text=raw, normalized_text=normalized,
                        raw_character_text="".join(c.get("text", "") for c in line.get("chars", [])),
                        bounding_box=transform_bbox(bbox, matrix), native_bounding_box=list(bbox),
                        page_number=index+1, reading_order=ordinal, parent_section_id=None,
                        clause_id=None, extraction_method="pdfplumber_xy_cut_words" if config.layout_mode == "xy_cut" else "pdfplumber_native_text_line",
                        extraction_confidence=None,
                        original_reference=dict(page_number=index+1, native_bbox=list(bbox),
                                                extraction_scope="native_text_line",
                                                **({"numbered_column":line["numbered_column"]} if "numbered_column" in line else {})),
                        cleaning_changes=[] if raw == normalized else ["newline_normalization"],
                    ))
                for ordinal, image in enumerate(page.images):
                    bbox = [image["x0"], image["top"], image["x1"], image["bottom"]]
                    page_blocks.append(dict(
                        block_id=stable_hash(["block-v1", parse_key, index+1, "image", ordinal]),
                        block_type="image", raw_text="", normalized_text="",
                        bounding_box=transform_bbox(bbox, matrix), native_bounding_box=list(bbox),
                        page_number=index+1, reading_order=None, parent_section_id=None, clause_id=None,
                        extraction_method="pdfplumber_image_bounds", extraction_confidence=None,
                        original_reference=dict(page_number=index+1, native_bbox=list(bbox), image_index=ordinal),
                        cleaning_changes=[],
                    ))
                for ordinal in range(info["missing_image_bounds"]):
                    page_blocks.append(dict(
                        block_id=stable_hash(["block-v1", parse_key, index+1, "unlocated_image", ordinal]),
                        block_type="image", raw_text="", normalized_text="", bounding_box=None,
                        native_bounding_box=None, page_number=index+1, reading_order=None,
                        parent_section_id=None, clause_id=None, extraction_method="pypdf_image_resource_detection",
                        extraction_confidence=None, cleaning_changes=[],
                        original_reference=dict(page_number=index+1, image_resource_keys=info["image_resource_keys"],
                                                location_status="unknown"),
                    ))
                if config.layout_mode == "xy_cut":
                    info["review_reasons"] = [r for r in info["review_reasons"] if r != "layout_engine_not_implemented"]
                    info["layout_method"] = "whitespace_xy_cut"
                    if "multi_column_candidate" in info.get("layout_flags", []):
                        info["review_reasons"].append("heuristic_reading_order_needs_review")
                needs_ocr = config.ocr_mode == "force" or (config.ocr_mode == "auto" and (
                    info["content_type"] in ("scanned", "mixed") or
                    info.get("suspicious_char_ratio", 0) > config.suspicious_char_ratio))
                if needs_ocr:
                    try:
                        ocr_blocks, rendered, details = recognize_page(reader.pages[index], page, info,
                                                                       config, parse_key, artifact_dir)
                        info["ocr"] = details
                        if config.ocr_mode == "force" or info.get("suspicious_char_ratio", 0) > config.suspicious_char_ratio:
                            page_blocks = [b for b in page_blocks if b["block_type"] == "image"]
                        page_blocks.extend(ocr_blocks)
                        info["review_reasons"] = [r for r in info["review_reasons"] if r != "ocr_not_implemented"]
                        info["review_reasons"].append("ocr_text_not_business_verified")
                        if not ocr_blocks and info["content_type"] == "scanned":
                            info["review_reasons"].append("ocr_no_text_detected")
                        if any(b["extraction_confidence"] < config.ocr_min_confidence for b in ocr_blocks):
                            info["review_reasons"].append("ocr_low_confidence")
                        if config.extract_tables and not page_tables:
                            page_tables = extract_raster_tables(rendered, ocr_blocks, page, matrix, parse_key)
                    except Exception as exc:
                        info["ocr"] = dict(status="failed", error_type=type(exc).__name__, error=str(exc))
                        info["review_reasons"] += ["ocr_failed"]
                if config.extract_tables:
                    info["table_count"] = len(page_tables)
                    info["review_reasons"] = [r for r in info["review_reasons"] if r != "layout_engine_not_implemented"]
                    if "table_heavy_candidate" in info.get("layout_flags", []) and not page_tables:
                        info["review_reasons"].append("table_candidate_without_structure")
                    for table in page_tables:
                        info["review_reasons"].extend(table["review_reasons"])
                        if table["extraction_method"].startswith("pdfplumber_") and config.layout_mode == "xy_cut" and config.ocr_mode != "force":
                            page_blocks.append(dict(
                                block_id=stable_hash(["block-v1", parse_key, index+1, table["table_id"]]),
                                block_type="table", raw_text=table["raw_text"], normalized_text=table["raw_text"],
                                bounding_box=table["bounding_box"], native_bounding_box=table["native_bounding_box"],
                                page_number=index+1, reading_order=0, parent_section_id=None, clause_id=None,
                                extraction_method=table["extraction_method"], extraction_confidence=None,
                                table_id=table["table_id"], cleaning_changes=[],
                                original_reference=dict(page_number=index+1, native_bbox=table["native_bounding_box"])))
                if config.layout_mode == "xy_cut":
                    page_blocks = order_blocks(page_blocks, page.width)
                info["status"] = "needs_ocr" if info["content_type"] == "scanned" and (not needs_ocr or info.get("ocr", {}).get("status") == "failed" or info.get("ocr", {}).get("retained_count") == 0) else (
                    "completed_with_warnings" if info["review_reasons"] else "completed")
                info["route"] = "ocr" if needs_ocr else ("layout_native" if config.layout_mode == "xy_cut" else info.get("route", "native"))
                info["review_reasons"] = sorted(set(info["review_reasons"]))
                blocks.extend(page_blocks)
                tables.extend(page_tables)
                pages.append(info)
                if cache:cache.save(index+1,info,page_blocks,page_tables)
            except Exception as exc:
                pages.append(dict(page_number=index+1, status="failed", error=str(exc),
                                  error_type=type(exc).__name__, review_reasons=["page_extraction_failed"]))
            finally:
                page.close()
    from .numbered_columns import annotate_catalog_blocks
    annotate_catalog_blocks(blocks,parse_key)
    return dict(page_count=count, pages=pages, blocks=blocks,
                sections=detect_sections(blocks, parse_key), tables=tables,
                page_checkpoint_stats={'hits':cache.hits,'writes':cache.writes} if cache else {'enabled':False},
                errors=[p["error"] for p in pages if p["status"] == "failed"])


CHINESE_HEADING = re.compile(r"^\s*第\s*([零〇一二三四五六七八九十百千\d](?:\s*[零〇一二三四五六七八九十百千\d])*)\s*(章|节|条)\s*")
DECIMAL_HEADING = re.compile(r"^\s*(\d+(?:\.\d+)+)[、.\s]")


def detect_sections(blocks, parse_key):
    """Candidates only: preserve numbering and never invent missing clauses."""
    sections, stack = [], []
    current_clause = None
    previous_page = None
    toc_pages={b['page_number'] for b in blocks if re.sub(r'\s+','',b.get('raw_text','')) in ('目录','条款目录')}
    for block in blocks:
        if previous_page is not None and block["page_number"] > previous_page + 1:
            stack = []
            current_clause = None
        previous_page = block["page_number"]
        if block["block_type"] in ("image", "table"):
            continue
        text = block["normalized_text"]
        if block['page_number'] in toc_pages:
            block['semantic_role']='table_of_contents'
            block['parent_section_id']=None;block['clause_id']=None
            stack=[];current_clause=None
            continue
        if block.get('semantic_role')=='numbered_catalog':
            block['parent_section_id']=None;block['clause_id']=None
            stack=[];current_clause=None
            continue
        chinese = CHINESE_HEADING.match(text)
        decimal = DECIMAL_HEADING.match(text)
        level, clause_number = None, None
        if chinese:
            level = {"章": 1, "节": 2, "条": 3}[chinese[2]]
            if chinese[2] == "条":
                clause_number = chinese[0].strip()
        elif decimal:
            level = len(decimal[1].split("."))
            clause_number = decimal[1]
        if level is not None and len(text) <= 150:
            while stack and stack[-1]["level"] >= level:
                stack.pop()
            section_id = stable_hash(["section-v1", parse_key, block["block_id"]])
            current_clause = stable_hash(["clause-v1", section_id]) if clause_number else None
            section = dict(section_id=section_id, parent_id=stack[-1]["section_id"] if stack else None,
                           level=level, heading=text, clause_id=current_clause, clause_number=clause_number,
                           section_path=[s["heading"] for s in stack] + [text],
                           page_start=block["page_number"], page_end=block["page_number"],
                           block_ids=[], review_status="pending_review", detection_method="numbering_rule")
            sections.append(section)
            stack.append(section)
            block["block_type"] = "heading"
        block["parent_section_id"] = stack[-1]["section_id"] if stack else None
        block["clause_id"] = current_clause
        for section in stack:
            section["block_ids"].append(block["block_id"])
            section["page_end"] = block["page_number"]
    return sections
