"""Structural invariants and lossless-text checks for native artifacts."""
import math
import re

PROTECTED = re.compile(r"不予|不得|不承担|除外|[0-9]+(?:[.,][0-9]+)*(?:%|％)?|元|万元|岁|天|日|年")


def validate_layout(layout):
    count = layout["page_count"]
    if count is None:
        return []  # A document-level error is reported by the pipeline.
    pages = layout["pages"]
    if [p["page_number"] for p in pages] != list(range(1, count+1)):
        raise ValueError("page_status_coverage_mismatch")
    page_map = {p["page_number"]: p for p in pages}
    identifiers = set()
    section_ids = {s["section_id"] for s in layout["sections"]}
    reasons = []
    for block in layout["blocks"]:
        if block["block_id"] in identifiers:
            raise ValueError("duplicate_block_id")
        identifiers.add(block["block_id"])
        page = page_map.get(block["page_number"])
        if page is None:
            raise ValueError("block_page_missing")
        bbox = block["bounding_box"]
        if bbox is None and block["block_type"] == "image":
            if block["original_reference"].get("location_status") != "unknown":
                raise ValueError("missing_image_location_status")
            continue
        if len(bbox) != 4 or not all(isinstance(x, (int, float)) and math.isfinite(x) for x in bbox):
            raise ValueError("invalid_block_bbox")
        if bbox[0] > bbox[2] or bbox[1] > bbox[3]:
            raise ValueError("inverted_block_bbox")
        if page.get("width") and (bbox[0] < -1 or bbox[1] < -1 or
                                 bbox[2] > page["width"]+1 or bbox[3] > page["height"]+1):
            reasons.append("block_outside_page_bounds")
        if block.get("parent_section_id") is not None and block["parent_section_id"] not in section_ids:
            raise ValueError("section_reference_missing")
        if PROTECTED.findall(block["raw_text"]) != PROTECTED.findall(block["normalized_text"]):
            raise ValueError("protected_text_changed")
    for section in layout["sections"]:
        if section["parent_id"] is not None and section["parent_id"] not in section_ids:
            raise ValueError("parent_section_missing")
        if not set(section["block_ids"]).issubset(identifiers):
            raise ValueError("section_block_reference_missing")
    table_ids = set()
    for table in layout.get("tables", []):
        if table["table_id"] in table_ids or table["page_number"] not in page_map:
            raise ValueError("invalid_table_identity")
        table_ids.add(table["table_id"])
        occupied, cell_ids = set(), set()
        for cell in table["cells"]:
            if cell["cell_id"] in cell_ids:
                raise ValueError("duplicate_cell_id")
            cell_ids.add(cell["cell_id"])
            row, col = cell["row_index"], cell["column_index"]
            rs, cs = cell["row_span"], cell["column_span"]
            if min(row, col) < 0 or min(rs, cs) < 1 or row+rs > table["row_count"] or col+cs > table["column_count"]:
                raise ValueError("invalid_table_span")
            slots = {(r, c) for r in range(row, row+rs) for c in range(col, col+cs)}
            if occupied & slots:
                raise ValueError("overlapping_table_cells")
            occupied.update(slots)
            bbox = cell["bounding_box"]
            if len(bbox) != 4 or not all(isinstance(v, (float, int)) and math.isfinite(v) for v in bbox) or bbox[0] > bbox[2] or bbox[1] > bbox[3]:
                raise ValueError("invalid_cell_bbox")
            if cell["raw_text"] != cell["normalized_text"]:
                raise ValueError("table_cell_text_changed")
            if not set(cell.get("source_block_ids", [])).issubset(identifiers):
                raise ValueError("table_cell_block_missing")
        if len(occupied) != table["row_count"]*table["column_count"]:
            reasons.append("table_grid_incomplete")
    return sorted(set(reasons))
