"""Local CPU OCR with pinned model hashes and MediaBox render coordinates."""
from functools import lru_cache
from pathlib import Path
import importlib.metadata
import importlib.util
import io
import re
from dataclasses import replace
from time import perf_counter

from .common import file_hash, stable_hash
from .preflight import transform_bbox


def ocr_versions():
    versions = {}
    for name in ("rapidocr-onnxruntime", "onnxruntime", "opencv-python", "pypdfium2", "Pillow"):
        try:
            versions[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            versions[name] = "not_installed"
    spec = importlib.util.find_spec("rapidocr_onnxruntime")
    versions["ocr_models"] = ({p.name: file_hash(p) for p in sorted(
        Path(spec.origin).parent.glob("models/*.onnx"))} if spec else {})
    return versions


@lru_cache(maxsize=1)
def get_engine():
    try:
        from rapidocr_onnxruntime import RapidOCR
    except ImportError as exc:
        raise RuntimeError("ocr_dependency_missing: install insurance-rag[ocr]") from exc
    # Preserve weak predictions too; the quality gate marks their confidence.
    return RapidOCR(text_score=0.0, det_use_cuda=False, cls_use_cuda=False,
                    rec_use_cuda=False, intra_op_num_threads=2, inter_op_num_threads=1)


def render_page(raw_page, config):
    from pypdf import PdfWriter
    from pypdf.generic import RectangleObject
    import pypdfium2
    writer = PdfWriter()
    copied = writer.add_page(raw_page)
    copied.cropbox = RectangleObject(copied.mediabox)
    stream = io.BytesIO()
    writer.write(stream)
    scale = config.ocr_dpi/72
    size = float(copied.mediabox.width)*float(copied.mediabox.height)*scale*scale
    if size > config.ocr_max_pixels:
        raise ValueError("ocr_render_pixel_limit_exceeded")
    with pypdfium2.PdfDocument(stream.getvalue()) as pdf:
        page = pdf[0]
        try:
            bitmap = page.render(scale=scale)
            try:
                return bitmap.to_pil().convert("RGB").copy()
            finally:
                bitmap.close()
        finally:
            page.close()


def split_tall_predictions(image, predictions, engine):
    """Recover tiny horizontal numbers mistakenly detected as vertical columns."""
    import cv2
    import numpy as np
    array = np.asarray(image.convert("RGB"))
    retained, crops, positions, sources = [], [], [], []
    for polygon, text, score in predictions:
        x0, y0 = max(0, int(min(p[0] for p in polygon))), max(0, int(min(p[1] for p in polygon)))
        x1, y1 = min(image.width, int(max(p[0] for p in polygon))+1), min(image.height, int(max(p[1] for p in polygon))+1)
        if y1-y0 < 5*max(1, x1-x0) or x1-x0 < 4:
            retained.append([polygon, text, score])
            continue
        crop = array[y0:y1, x0:x1]
        mask = cv2.threshold(cv2.cvtColor(crop, cv2.COLOR_RGB2GRAY), 180, 255, cv2.THRESH_BINARY_INV)[1]
        lines = cv2.morphologyEx(mask, cv2.MORPH_OPEN,
                                cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, (y1-y0)//6))))
        ink = cv2.subtract(mask, lines)
        rows = np.flatnonzero((ink > 0).sum(axis=1) >= max(1, (x1-x0)*.06))
        bands = []
        for row in rows:
            if not bands or row > bands[-1][-1]+1:
                bands.append([int(row)])
            else:
                bands[-1].append(int(row))
        bands = [b for b in bands if 2 <= b[-1]-b[0]+1 <= max(25, int((x1-x0)*1.5))]
        if len(bands) < 4:
            retained.append([polygon, text, score])
            continue
        sources.append(dict(polygon=polygon, raw_text=text, confidence=float(score), split_count=len(bands)))
        for band in bands:
            top, bottom = max(0, band[0]-1), min(y1-y0, band[-1]+2)
            crops.append(crop[top:bottom].copy())
            positions.append([[x0, y0+top], [x1, y0+top], [x1, y0+bottom], [x0, y0+bottom]])
    if crops:
        recognized, elapsed = engine.text_rec(crops)
        for polygon, (text, score) in zip(positions, recognized):
            retained.append([polygon, text, float(score)])
    else:
        elapsed = 0.0
    return retained, sources, float(elapsed)


def short_text_retry(raw_page, image, predictions, config, engine):
    """One bounded high-DPI pass for weak short prose; numeric text is untouched."""
    candidates=[i for i,(_,text,score) in enumerate(predictions)
                if 0<len(text.strip())<=12 and float(score)<.92 and not re.search(r'\d',text)]
    if not candidates or config.ocr_dpi>=300:return predictions,[],{'status':'not_needed'}
    started=perf_counter()
    try:
        retry_image=render_page(raw_page,replace(config,ocr_dpi=300))
    except ValueError as exc:
        return predictions,[],{'status':'render_limit','reason':str(exc)}
    import numpy as np
    try:
        alternative,_=engine(np.asarray(retry_image))
    except Exception as exc:
        return predictions,[],{'status':'retry_failed','reason':type(exc).__name__,
                               'elapsed_seconds':perf_counter()-started}
    def box(poly,sx=1,sy=1):
        return [min(p[0] for p in poly)*sx,min(p[1] for p in poly)*sy,
                max(p[0] for p in poly)*sx,max(p[1] for p in poly)*sy]
    def overlap(a,b):
        area=max(0,min(a[2],b[2])-max(a[0],b[0]))*max(0,min(a[3],b[3])-max(a[1],b[1]))
        union=(a[2]-a[0])*(a[3]-a[1])+(b[2]-b[0])*(b[3]-b[1])-area
        return area/union if union else 0
    output=list(predictions);evidence=[]
    for i in candidates:
        poly,text,score=predictions[i];original_box=box(poly)
        matches=[(p,t,float(s)) for p,t,s in alternative or [] if 0<len(t.strip())<=12 and
                 not re.search(r'\d',t) and float(s)>=float(score)+.05 and
                 overlap(original_box,box(p,image.width/retry_image.width,image.height/retry_image.height))>=.6]
        if len(matches)!=1:continue
        p,t,s=matches[0]
        if t==text:continue
        output[i]=[poly,t,s]
        evidence.append({'original_text':text,'original_confidence':float(score),'retry_text':t,
            'retry_confidence':s,'primary_pixel_bbox':original_box,'retry_pixel_bbox':box(p),
            'primary_dpi':config.ocr_dpi,'retry_dpi':300,'review_status':'pending_review'})
    return output,evidence,{'status':'completed','elapsed_seconds':perf_counter()-started,'changed_count':len(evidence)}


def recognize_page(raw_page, page, info, config, parse_key, artifact_dir=None):
    import numpy as np
    image = render_page(raw_page, config)
    engine = get_engine()
    result, timing = engine(np.asarray(image))
    original_count = len(result or [])
    result,retry_evidence,retry_details=short_text_retry(raw_page,image,result or [],config,engine)
    if retry_evidence:
        info['review_reasons'].append('ocr_short_text_retry_changed_requires_review')
    split_sources, split_elapsed = [], 0.0
    if config.extract_tables and result and hasattr(engine, "text_rec"):
        result, split_sources, split_elapsed = split_tall_predictions(image, result, engine)
    blocks = []
    sx, sy = page.width/image.width, page.height/image.height
    xoff, yoff = page.bbox[:2]
    for ordinal, (polygon, text, score) in enumerate(result or []):
        pixel_bbox = [min(p[0] for p in polygon), min(p[1] for p in polygon),
                      max(p[0] for p in polygon), max(p[1] for p in polygon)]
        native = [xoff+pixel_bbox[0]*sx, yoff+pixel_bbox[1]*sy,
                  xoff+pixel_bbox[2]*sx, yoff+pixel_bbox[3]*sy]
        # On mixed pages, text overlapping an existing native text layer is
        # evidence only: native characters remain the authoritative extraction.
        if config.ocr_mode == "auto" and info["content_type"] != "scanned" and info["suspicious_char_ratio"] <= config.suspicious_char_ratio:
            area = max(1, (native[2]-native[0])*(native[3]-native[1]))
            overlap = sum(max(0, min(c["x1"], native[2])-max(c["x0"], native[0])) *
                          max(0, min(c["bottom"], native[3])-max(c["top"], native[1])) for c in page.chars)
            if overlap/area > .2:
                continue
        blocks.append(dict(block_id=stable_hash(["block-v1", parse_key, page.page_number, "ocr", ordinal]),
                           block_type="paragraph", raw_text=text, normalized_text=text,
                           raw_character_text=text,
                           bounding_box=transform_bbox(native, info["native_to_standard"]),
                           native_bounding_box=native, page_number=page.page_number, reading_order=ordinal,
                           parent_section_id=None, clause_id=None, extraction_method="rapidocr_onnx_cpu",
                           extraction_confidence=float(score), cleaning_changes=[],
                           original_reference=dict(page_number=page.page_number, native_bbox=native,
                                                   render_pixel_bbox=pixel_bbox, render_polygon=polygon,
                                                   render_size=[image.width, image.height],
                                                   dpi=config.ocr_dpi, extraction_scope="rendered_mediabox")))
    details = dict(engine="rapidocr_onnx_cpu", recognized_count=len(result or []), original_detected_count=original_count,
                   short_text_retry=retry_details,short_text_retry_sources=retry_evidence,
                   column_split_sources=split_sources, column_split_elapsed=split_elapsed,
                   retained_count=len(blocks), elapsed_seconds=[float(v) for v in (timing or [])],
                   render_size=[image.width, image.height], dpi=config.ocr_dpi)
    if artifact_dir:
        target = Path(artifact_dir)/"renders"/f"page_{page.page_number:04d}.png"
        target.parent.mkdir(parents=True, exist_ok=True)
        image.save(target)
        details.update(render_artifact=target.relative_to(artifact_dir).as_posix(), render_sha256=file_hash(target))
    return blocks, image, details
