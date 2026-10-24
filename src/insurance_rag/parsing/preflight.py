"""Page preflight and explicit rotated-to-unrotated coordinate transforms."""
import math
import re


def coordinate_transform(page, media):
    width, height = float(media.width), float(media.height)
    x0, top, _, _ = page.bbox
    rotation = int(page.rotation) % 360
    # Affine matrix: X=a*x+c*y+e, Y=b*x+d*y+f.
    matrices = {
        0: [1, 0, 0, 1, -x0, -top],
        90: [0, -1, 1, 0, -top, height + x0],
        180: [-1, 0, 0, -1, width + x0, height + top],
        270: [0, 1, -1, 0, width + top, -x0],
    }
    if rotation not in matrices or not all(math.isfinite(x) for x in (width, height, x0, top)) or min(width, height) <= 0:
        raise ValueError("invalid_page_geometry")
    return matrices[rotation]


def transform_bbox(bbox, matrix):
    a, b, c, d, e, f = matrix
    x0, y0, x1, y1 = map(float, bbox)
    points = [(a*x+c*y+e, b*x+d*y+f) for x, y in ((x0,y0), (x0,y1), (x1,y0), (x1,y1))]
    result = [min(x for x,y in points), min(y for x,y in points),
              max(x for x,y in points), max(y for x,y in points)]
    if not all(math.isfinite(x) for x in result):
        raise ValueError("invalid_block_coordinates")
    return [round(x, 4) for x in result]


def inspect_page(page, raw_page, config):
    chars = page.chars
    text = "".join(c.get("text", "") for c in chars)
    images = page.images
    resource_error = None
    try:
        resource_keys = list(raw_page.images.keys())
    except Exception as exc:
        # Some valid inline-image streams cannot be decoded by pypdf. Preserve
        # this uncertainty and inspect the original decompressed content only.
        resource_error = str(exc)
        contents = raw_page.get_contents()
        data = contents.get_data() if contents is not None else b""
        inline_candidate = bool(re.search(rb"(?<!\S)BI\s+/", data))
        resource_keys = ["inline_image_candidate"] if inline_candidate else []
    missing_image_bounds = max(0, len(resource_keys) - len(images))
    coverage = min(1.0, sum(max(0.0, float(i["x1"])-float(i["x0"])) *
                            max(0.0, float(i["bottom"])-float(i["top"])) for i in images) /
                   max(1.0, page.width*page.height))
    suspect = text.count("\ufffd") + text.count("(cid:")
    ratio = suspect / max(1, len(text))
    shapes = len(page.lines) + len(page.rects) + len(page.curves)
    if text.strip():
        content_type = "mixed" if coverage >= config.image_coverage_threshold or missing_image_bounds else "native"
    elif images or resource_keys:
        content_type = "scanned"
    elif shapes or resource_error:
        content_type = "unknown"
    else:
        content_type = "blank"
    flags = []
    if int(page.rotation) % 360:
        flags.append("rotated")
    if shapes >= config.table_edge_threshold:
        flags.append("table_heavy_candidate")
    # Conservative signal only: large gaps on at least three text rows.
    rows = {}
    for word in page.extract_words():
        rows.setdefault(round(float(word["top"]) / 3), []).append(word)
    gap_rows = 0
    for words in rows.values():
        words.sort(key=lambda w: w["x0"])
        if any(float(right["x0"])-float(left["x1"]) > page.width*0.18 for left, right in zip(words, words[1:])):
            gap_rows += 1
    if gap_rows >= 3:
        flags.append("multi_column_candidate")
    reasons = []
    if resource_error:
        reasons.append("image_resource_detection_incomplete")
    if missing_image_bounds:
        reasons.append("image_bounds_unavailable")
    if "rotated" in flags:
        reasons.append("rotation_reading_order_needs_review")
    if content_type in ("scanned", "mixed"):
        reasons.append("ocr_not_implemented")
    if content_type == "unknown":
        reasons.append("nontext_content_needs_review")
    if ratio > config.suspicious_char_ratio:
        reasons.append("suspicious_native_text")
    if any(f in flags for f in ("table_heavy_candidate", "multi_column_candidate")):
        reasons.append("layout_engine_not_implemented")
    matrix = coordinate_transform(page, raw_page.mediabox)
    return dict(
        page_number=page.page_number, width=float(raw_page.mediabox.width),
        height=float(raw_page.mediabox.height), rotation=int(page.rotation) % 360,
        media_box_pdf=[float(v) for v in raw_page.mediabox],
        crop_box_pdf=[float(v) for v in raw_page.cropbox],
        native_page_bbox=list(page.bbox), native_to_standard=matrix,
        coordinate_system="unrotated_mediabox_top_left_points",
        content_type=content_type, layout_flags=flags, character_count=len(text),
        image_resource_detection_error=resource_error,
        missing_image_bounds=missing_image_bounds, image_resource_keys=[str(k) for k in resource_keys],
        image_coverage_upper_bound=coverage, suspicious_char_ratio=ratio, review_reasons=reasons,
        route="needs_ocr" if content_type == "scanned" else
              ("native_with_review" if reasons else "native"),
    )
