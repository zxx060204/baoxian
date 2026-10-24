"""Deterministic whitespace partitioning; results remain review candidates."""
from .text_geometry import text_tolerances


def _gaps(items, start, end):
    intervals = sorted((float(i[start]), float(i[end])) for i in items)
    gaps, right = [], intervals[0][1]
    for left, edge in intervals[1:]:
        if left > right:
            gaps.append((left-right, (right+left)/2))
        right = max(right, edge)
    return gaps


def xy_order(items, width, depth=0):
    """Vertical cuts first, then separated horizontal bands, then row order."""
    if len(items) < 2 or depth > 32:
        return list(items)
    heights = sorted(max(1, i["bottom"]-i["top"]) for i in items)
    typical = heights[len(heights)//2]
    for start, end, threshold in (("x0", "x1", max(18, width*.035)),
                                   ("top", "bottom", typical*.9)):
        gaps = [g for g in _gaps(items, start, end) if g[0] >= threshold]
        if gaps:
            _, cut = max(gaps)
            left = [i for i in items if i[end] <= cut]
            right = [i for i in items if i[start] >= cut]
            if left and right and len(left)+len(right) == len(items):
                return xy_order(left, width, depth+1)+xy_order(right, width, depth+1)
    return sorted(items, key=lambda i: (round(i["top"]/3), i["x0"]))


def native_lines(page, tables):
    x_tolerance,y_tolerance=text_tolerances(page)
    if tables:
        from .tables import GlyphIndex
        glyphs = GlyphIndex(page.chars)
        captured = {id(c) for t in tables for cell in t['cells']
                    for c in glyphs.within(cell['native_bounding_box'])}
        # A decorative/irregular table's bounding rectangle may also contain
        # unrelated prose. Exclude only glyphs represented by actual cells.
        # Filtering before word extraction also avoids duplicating partial words
        # whose centres fall outside a cell while some glyphs fall inside it.
        prose = page.filter(lambda obj: obj.get('object_type') != 'char' or id(obj) not in captured)
    else:
        prose = page
    words = prose.extract_words(x_tolerance=x_tolerance, y_tolerance=y_tolerance, keep_blank_chars=False)
    from .numbered_columns import ordered_numbered_words
    detected=ordered_numbered_words(words,page.width)
    ordered=(xy_order(detected[0],page.width)+[w for col in detected[1] for w in sorted(col,key=lambda w:(round(w['top']/max(y_tolerance,1)),w['x0']))]+xy_order(detected[2],page.width)) if detected else xy_order(words,page.width)
    lines, current = [], []
    for word in ordered:
        if current and (word.get('numbered_column')!=current[-1].get('numbered_column') or abs(word["top"]-current[-1]["top"]) > y_tolerance or
                        word["x0"]-current[-1]["x1"] > max(18, page.width*.035)
                        or word["x0"] < current[-1]["x0"]):
            lines.append(_line(current))
            current = []
        current.append(word)
    if current:
        lines.append(_line(current))
    return lines


def _line(words):
    line=dict(text=" ".join(w["text"] for w in words),
                x0=min(w["x0"] for w in words), x1=max(w["x1"] for w in words),
                top=min(w["top"] for w in words), bottom=max(w["bottom"] for w in words),
                chars=[])
    if 'numbered_column' in words[0]:line['numbered_column']=words[0]['numbered_column']
    return line


def order_blocks(blocks, width):
    text = [b for b in blocks if b["block_type"] != "image"]
    proxies = [dict(x0=b["native_bounding_box"][0], top=b["native_bounding_box"][1],
                    x1=b["native_bounding_box"][2], bottom=b["native_bounding_box"][3], block=b)
               for b in text]
    # OCR forms contain short labels and values on the same physical rows.
    # Column-first XY cuts otherwise read the label column before the title.
    ocr=[b for b in text if b.get('extraction_method')=='rapidocr_onnx_cpu']
    rows=[]
    for item in sorted(proxies,key=lambda i:(i['top']+i['bottom'])/2):
        center=(item['top']+item['bottom'])/2
        if not rows or abs(center-rows[-1][0])>max(2,(item['bottom']-item['top'])*.5):rows.append((center,[]))
        rows[-1][1].append(item)
    form=(len(ocr)>=6 and len(ocr)==len(text) and
          sum(len(b['raw_text'])<=12 for b in ocr)>=len(ocr)*.5 and
          sum(len(row)>=2 for _,row in rows)>=3)
    numbered=any('numbered_column' in b.get('original_reference',{}) for b in text)
    ordered=sorted(proxies,key=lambda i:i['block'].get('reading_order',0)) if numbered else ([i for _,row in rows for i in sorted(row,key=lambda i:i['x0'])] if form else xy_order(proxies,width))
    for ordinal, item in enumerate(ordered):
        item["block"]["reading_order"] = ordinal
        if form:item['block']['reading_order_method']='ocr_form_visual_rows'
    return sorted(text, key=lambda b: b["reading_order"]) + [
        b for b in blocks if b["block_type"] == "image"]
