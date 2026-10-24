"""Native ruled tables with cell geometry and conservative span candidates."""
import re
from bisect import bisect_left, bisect_right
from collections import defaultdict, Counter
from statistics import median
from pdfplumber.utils import extract_text

from .common import stable_hash
from .preflight import transform_bbox
from .text_geometry import text_tolerances

NUMERIC = re.compile(r"[-+]?\d+(?:[,.]\d+)*(?:[%％])?")


class GlyphIndex:
    """Query exact half-open glyph-centre bounds without rescanning the page."""
    def __init__(self, chars):
        self.chars = chars
        self.centres = [((c['x0']+c['x1'])/2, (c['top']+c['bottom'])/2) for c in chars]
        self.axes = [sorted((point[axis], i) for i, point in enumerate(self.centres)) for axis in (0, 1)]
        self.values = [[v for v, _ in axis] for axis in self.axes]

    def within(self, bbox):
        ranges = [(bisect_left(self.values[a], bbox[a]), bisect_left(self.values[a], bbox[a+2])) for a in (0, 1)]
        axis = min((0, 1), key=lambda a: ranges[a][1]-ranges[a][0])
        lo, hi = ranges[axis]
        ids = [i for _, i in self.axes[axis][lo:hi] if
               bbox[0] <= self.centres[i][0] < bbox[2] and bbox[1] <= self.centres[i][1] < bbox[3]]
        return [self.chars[i] for i in sorted(ids)]


def grid_issue(boxes):
    xs = _axis([v for b in boxes for v in (b[0], b[2])])
    ys = _axis([v for b in boxes for v in (b[1], b[3])])
    occupied = set()
    for b in boxes:
        c0, c1 = [min(range(len(xs)), key=lambda i: abs(xs[i]-v)) for v in (b[0], b[2])]
        r0, r1 = [min(range(len(ys)), key=lambda i: abs(ys[i]-v)) for v in (b[1], b[3])]
        if r1 <= r0 or c1 <= c0:
            return 'degenerate_table_cell_candidate'
        for r in range(r0, r1):
            for c in range(c0, c1):
                if (r, c) in occupied:
                    return 'overlapping_table_cell_candidate'
                occupied.add((r, c))
    return None


def _axis(values):
    result = []
    for value in sorted(values):
        if not result or abs(value-result[-1]) > 1:
            result.append(float(value))
    return result


def make_table(bbox, cells, page_number, matrix, parse_key, ordinal, method):
    xs = _axis([v for c in cells for v in (c["bbox"][0], c["bbox"][2])])
    ys = _axis([v for c in cells for v in (c["bbox"][1], c["bbox"][3])])
    def nearest(axis,value):
        i=bisect_left(axis,value)
        return min((j for j in (i-1,i) if 0<=j<len(axis)),key=lambda j:abs(axis[j]-value))
    table_id = stable_hash(["table-v1", parse_key, page_number, method, ordinal])
    output = []
    for index, cell in enumerate(cells):
        box, text = list(cell["bbox"]), cell["text"]
        col, endcol = nearest(xs, box[0]), nearest(xs, box[2])
        row, endrow = nearest(ys, box[1]), nearest(ys, box[3])
        output.append(dict(cell_id=stable_hash([table_id, index]), row_index=row, column_index=col,
                           row_span=endrow-row, column_span=endcol-col,
                           raw_text=text, normalized_text=text,
                           numeric_tokens=NUMERIC.findall(text),
                           bounding_box=transform_bbox(box, matrix), native_bounding_box=box,
                           extraction_confidence=cell.get("confidence"),
                           source_block_ids=cell.get("block_ids", []),
                           header_candidate=row == 0,
                           original_reference=dict(page_number=page_number, native_bbox=box)))
    return dict(table_id=table_id, page_number=page_number,
                bounding_box=transform_bbox(bbox, matrix), native_bounding_box=list(bbox),
                row_count=max(0, len(ys)-1), column_count=max(0, len(xs)-1),
                cells=output, extraction_method=method, review_status="pending_review",
                review_reasons=["header_semantics_not_verified", "cross_page_linkage_not_verified"],
                header_rows_candidate=[0], continuation_of=None,
                raw_text="\n".join(c["raw_text"] for c in sorted(output, key=lambda c: (c["row_index"], c["column_index"]))))


def sequential_year_header(rows, group, typical_height):
    """Recover a consecutive year axis and wrapped leading dimension labels.

    A header establishes columns even if no body row fills the whole matrix.
    No insurer names, gold labels or business identities participate.
    """
    for candidate in rows:
        candidate=sorted(candidate,key=lambda w:w['x0'])
        for start,w in enumerate(candidate):
            tail=candidate[start:]
            if w['text']!='1' or len(tail)<8 or any(t['text']!=str(i+1) for i,t in enumerate(tail)):continue
            top=min(w['top'] for w in candidate);bottom=max(w['bottom'] for w in candidate)
            if abs(top-min(w['top'] for w in group[0]))>typical_height*6:continue
            nearby=[w for r in rows for w in r if top-typical_height*3<=w['top']<=bottom]
            if not any(re.search('年度|期间|年龄|性别|交费|year|age',w['text'],re.I) for w in nearby):continue
            years=[(t['x0']+t['x1'])/2 for t in tail]
            step=median(b-a for a,b in zip(years,years[1:]))
            if step<=2 or any(abs(b-a-step)>step*.2 for a,b in zip(years,years[1:])):continue
            year_left=years[0]-step*.5
            group_end=max(w['bottom'] for w in group[-1])
            body=[sorted(r,key=lambda w:w['x0']) for r in rows if bottom<min(w['top'] for w in r)<=group_end+typical_height*2]
            prefixes=[[w for w in r if (w['x0']+w['x1'])/2<year_left] for r in body]
            frequencies=Counter(len(r) for r in prefixes if r)
            sizes=[n for n,f in frequencies.items() if f>=3]
            if not sizes:continue
            n=max(sizes);complete=[r for r in prefixes if len(r)==n]
            centers=[median((r[i]['x0']+r[i]['x1'])/2 for r in complete) for i in range(n)]
            # Complete one-line headers already have the original alignment
            # path. Do not replace verified geometry with this wrapped-header
            # recovery, which also has a different top boundary.
            if len(candidate)==n+len(years) and sum(len(r)==len(candidate) for r in body)>=3:continue
            if any(b-a<2 for a,b in zip(centers+years,centers[1:]+years)):continue
            valid=[]
            for r,prefix in zip(body,prefixes):
                values=[w for w in r if (w['x0']+w['x1'])/2>=year_left]
                if len(prefix)==n and values and all(NUMERIC.fullmatch(w['text']) for w in values) and all(
                    abs((w['x0']+w['x1'])/2-centers[i])<step*.4 for i,w in enumerate(prefix)):
                    valid.append(r)
            if len(valid)<5:continue
            data_top=min(w['top'] for w in valid[0])
            header=[w for r in rows for w in r if top-typical_height*2<=w['top']<data_top and
                    ((w['x0']+w['x1'])/2<year_left or w in candidate)]
            if not header:continue
            return centers+years,valid,header
    return None


def extract_borderless_tables(page, matrix, parse_key):
    """Dense numeric matrices; preserve cells without guessing business axes."""
    from collections import Counter
    from statistics import median
    xt,yt=text_tolerances(page)
    words = page.extract_words(x_tolerance=xt, y_tolerance=yt)
    if not words:
        return []
    tolerance = max(.8, min(3, median(w["bottom"]-w["top"] for w in words)*.4))
    rows = []
    for word in sorted(words, key=lambda w: (w["top"], w["x0"])):
        if not rows or word["top"]-rows[-1][0]["top"] > tolerance:
            rows.append([word])
        else:
            rows[-1].append(word)
    dense = []
    for row in rows:
        row.sort(key=lambda w: w["x0"])
        if len(row) >= 3 and sum(bool(NUMERIC.fullmatch(w["text"])) for w in row)/len(row) >= .8:
            dense.append(row)
    if len(dense) < 5:
        return []
    typical_height = median(w["bottom"]-w["top"] for row in dense for w in row)
    groups = []
    for row in dense:
        if not groups or min(w["top"] for w in row)-max(w["bottom"] for w in groups[-1][-1]) > typical_height*2:
            groups.append([row])
        else:
            groups[-1].append(row)
    tables = []
    glyphs = GlyphIndex(page.chars) if hasattr(page, 'chars') else None
    for group in groups:
        if len(group) < 5:
            continue
        year_header=sequential_year_header(rows,group,typical_height)
        frequencies=Counter(len(row) for row in group)
        # Triangular matrices have real blank tails; their most frequent row
        # length is not the number of columns. Several full source rows must
        # establish the largest stable axis before retaining sparse rows.
        count=max((n for n,f in frequencies.items() if f>=3),default=0)
        complete = [row for row in group if len(row) == count]
        if not year_header and (count < 3 or len(complete) < 3 or any(len(row) > count*1.2 for row in group)):
            continue
        if year_header:
            centers,group,wrapped_header=year_header;count=len(centers)
        else:
            centers = [median((r[i]["x0"]+r[i]["x1"])/2 for r in complete) for i in range(count)]
        if any(b-a < 2 for a,b in zip(centers, centers[1:])):
            continue
        # Dense rows establish the axes. Sparse terminal rows remain real rows
        # if their first value stays on the row axis and spacing stays regular.
        step=median(min(w['top'] for w in b)-min(w['top'] for w in a) for a,b in zip(group,group[1:]))
        trailing=[r for r in rows if min(w['top'] for w in r)>min(w['top'] for w in group[-1])]
        for row in trailing:
            if min(w['top'] for w in row)-min(w['top'] for w in group[-1])>step*1.5:
                break
            if not all(NUMERIC.fullmatch(w['text']) for w in row):
                break
            if abs((row[0]['x0']+row[0]['x1'])/2-centers[0])>max(typical_height,min(b-a for a,b in zip(centers,centers[1:]))*.2):
                break
            if any(min(abs((w['x0']+w['x1'])/2-c) for c in centers)>typical_height*1.5 for w in row):
                break
            group.append(row)
        bounds = [centers[0]-(centers[1]-centers[0])/2] + [
            (a+b)/2 for a,b in zip(centers, centers[1:])] + [centers[-1]+(centers[-1]-centers[-2])/2]
        # Repeated horizontal-year headers are outside the numeric body. Require
        # one source word per established column and tight geometric agreement.
        preceding=[r for r in rows if max(w['bottom'] for w in r)<=min(w['top'] for w in group[0])]
        header_count=0
        if preceding and not year_header:
            candidate=preceding[-1]
            aligned=(len(candidate)==count and
                min(w['top'] for w in group[0])-max(w['bottom'] for w in candidate)<=max(step,typical_height)*2 and
                all(abs((w['x0']+w['x1'])/2-c)<=max(typical_height*2,(bounds[i+1]-bounds[i])*.5)
                    for i,(w,c) in enumerate(zip(candidate,centers))))
            if aligned and any(re.search('年龄|性别|保单年度|保险期间',w['text']) for w in candidate):
                group.insert(0,candidate);header_count=1
        if year_header:
            group.insert(0,wrapped_header);header_count=1
        tops = [min(w["top"] for w in r) for r in group]
        bottoms = [max(w["bottom"] for w in r) for r in group]
        ys = [tops[0]] + [(a+b+typical_height)/2 for a,b in zip(tops, tops[1:])] + [tops[-1]+typical_height]
        cells = []
        for ri, row in enumerate(group):
            buckets = [[] for _ in range(count)]
            for word in row:
                ci = min(range(count), key=lambda i: abs(centers[i]-(word["x0"]+word["x1"])/2))
                buckets[ci].append(word["text"])
            for ci, texts in enumerate(buckets):
                box = [bounds[ci], ys[ri], bounds[ci+1], ys[ri+1]]
                # Alignment establishes geometry, not a second copy of the
                # words. Re-read physical glyphs so inferred rows cannot omit
                # headers or duplicate terminal values already in the page.
                text = (" ".join(texts) if ri<header_count else
                    extract_text(glyphs.within(box), x_tolerance=xt, y_tolerance=yt) if glyphs else " ".join(texts))
                cells.append(dict(bbox=box, text=text or ''))
        if header_count==0 and glyphs and getattr(page,'edges',None):
            # Retain a ruled two-level header above an aligned numeric body.
            # Its physical column boundaries also correct a wide age column.
            width=bounds[-1]-bounds[0]
            he=[e for e in page.edges if e.get('orientation')=='h' and
                e['x1']-e['x0']>=width*.65 and
                tops[0]-typical_height*5<=e['top']<=tops[0]+1 and
                e['x0']<=bounds[0]+width*.35 and e['x1']>=bounds[-1]-typical_height*4]
            def physical_axis(values):
                groups_axis=[]
                for v in sorted(values):
                    if not groups_axis or v-groups_axis[-1][-1]>max(1.5,typical_height*.2):groups_axis.append([v])
                    else:groups_axis[-1].append(v)
                return [median(g) for g in groups_axis]
            hy=physical_axis([e['top'] for e in he])
            if len(hy)>=3:
                full=[e for e in he if e['x1']-e['x0']>=width*.95]
                left=min((e['x0'] for e in full),default=bounds[0]);right=max((e['x1'] for e in full),default=bounds[-1])
                ve=[e for e in page.edges if e.get('orientation')=='v' and
                    e['top']<=hy[-1]+1 and e['bottom']>=hy[-1]-1 and left-1<=e['x0']<=right+1]
                hx=physical_axis([left,right]+[e['x0'] for e in ve])
                if len(hx)==count+1 and all(hx[i]<c<hx[i+1] for i,c in enumerate(centers)):
                    bounds=hx;ys[0]=hy[-1]
                    for ri in range(len(group)):
                        for ci in range(count):
                            box=[bounds[ci],ys[ri],bounds[ci+1],ys[ri+1]]
                            cells[ri*count+ci]={'bbox':box,'text':extract_text(glyphs.within(box),x_tolerance=xt,y_tolerance=yt) or ''}
                    nr=len(hy)-1;parents=list(range(nr*count))
                    def find_header(i):
                        while parents[i]!=i:parents[i]=parents[parents[i]];i=parents[i]
                        return i
                    for r in range(nr):
                        for c in range(count):
                            i=r*count+c
                            if c+1<count and not any(abs(e['x0']-hx[c+1])<=1 and e['top']<=hy[r]+1 and e['bottom']>=hy[r+1]-1 for e in ve):
                                parents[find_header(i)]=find_header(i+1)
                            if r+1<nr and not any(abs(e['top']-hy[r+1])<=1 and e['x0']<=hx[c]+1 and e['x1']>=hx[c+1]-1 for e in he):
                                parents[find_header(i)]=find_header(i+count)
                    groups_header=defaultdict(list)
                    for r in range(nr):
                        for c in range(count):groups_header[find_header(r*count+c)].append((r,c))
                    header_cells=[]
                    for items in groups_header.values():
                        r0=min(r for r,c in items);r1=max(r for r,c in items)+1;c0=min(c for r,c in items);c1=max(c for r,c in items)+1
                        if len(items)!=(r1-r0)*(c1-c0):header_cells=[];break
                        box=[hx[c0],hy[r0],hx[c1],hy[r1]]
                        header_cells.append({'bbox':box,'text':extract_text(glyphs.within(box),x_tolerance=xt,y_tolerance=yt) or ''})
                    if header_cells:cells=header_cells+cells;ys=hy+ys[1:];header_count=nr
        # Compound filled paths may expose the frame's horizontal boundaries
        # while the table finder cannot recover its cells. Keep physically
        # ruled multiline headers instead of only the last aligned text line.
        if header_count==1 and glyphs and getattr(page,'edges',None):
            upper=_axis([e['top'] for e in page.edges if e.get('orientation')=='h' and
                e['x1']-e['x0']>=(bounds[-1]-bounds[0])*.9 and
                abs(e['x0']-bounds[0])<typical_height*2 and
                tops[0]-typical_height*5<=e['top']<=tops[1]])
            if len(upper)>=3:
                first_text=extract_text(glyphs.within([bounds[0],upper[0],bounds[-1],upper[1]]),x_tolerance=xt,y_tolerance=yt) or ''
                if re.search('金额|保费|费率|现金价值',first_text):
                    header_cells=[]
                    for a,b in zip(upper,upper[1:]):
                        band_text=extract_text(glyphs.within([bounds[0],a,bounds[-1],b]),x_tolerance=xt,y_tolerance=yt) or ''
                        aligned_columns=bool(re.search('交费|缴费',band_text) and re.search('男性|女性',band_text))
                        start=0
                        for end in range(1,count+1):
                            boundary=aligned_columns or end==count or any(e.get('orientation')=='v' and abs(e['x0']-bounds[end])<=max(3,typical_height)
                                and e['top']<=a+1 and e['bottom']>=b-1 for e in page.edges)
                            if boundary:
                                box=[bounds[start],a,bounds[end],b]
                                header_cells.append({'bbox':box,'text':extract_text(glyphs.within(box),x_tolerance=xt,y_tolerance=yt) or ''})
                                start=end
                    cells=header_cells+cells[count:]
                    for c in cells[len(header_cells):len(header_cells)+count]:c['bbox'][1]=upper[-1]
                    ys=upper+ys[2:];header_count=len(upper)-1
        table = make_table([bounds[0], ys[0], bounds[-1], ys[-1]], cells,
                           page.page_number, matrix, parse_key, len(tables), "pdfplumber_numeric_alignment_candidate")
        table["review_reasons"] += ["borderless_table_structure_not_verified", "row_column_axes_not_verified"]
        table["header_rows_candidate"] = list(range(header_count))
        if year_header:table['review_reasons'].append('consecutive_year_header_axis_requires_review')
        for cell in table["cells"]:
            cell["header_candidate"] = cell['row_index']<header_count
        if header_count:
            table['header_evidence']=[{'text':c['raw_text'],'native_bbox':c['native_bounding_box']}
                                      for c in table['cells'] if c['header_candidate']]
        if header_count>1:table['review_reasons'].append('inferred_header_column_boundaries_need_review')
        tables.append(table)
    return tables


def _recover_partial_grid(page, table, matrix, parse_key, ordinal):
    """Split a ruled numeric body using its row-axis anchors, retaining headers.

    Existing cell geometry supplies columns and header bounds. Only a body with
    several regularly spaced numeric anchors can be recovered; no product names,
    ages, payment periods or gold annotations participate in this decision.
    """
    xt,yt=text_tolerances(page)
    words=page.extract_words(x_tolerance=xt,y_tolerance=yt)
    for ri,physical_row in enumerate(table.rows):
        boxes=[b for b in physical_row.cells if b]
        if len(boxes)<3:
            continue
        boxes.sort(key=lambda b:b[0])
        xs=_axis([v for b in boxes for v in (b[0],b[2])])
        if len(xs)!=len(boxes)+1 or any(b[0]!=boxes[0][0] and b[1]!=boxes[0][1] for b in boxes):
            continue
        top,bottom=boxes[0][1],boxes[0][3]
        anchors=sorted([w for w in words if NUMERIC.fullmatch(w['text'])
                        and xs[0]<(w['x0']+w['x1'])/2<xs[1]
                        and top<(w['top']+w['bottom'])/2<bottom],key=lambda w:w['top'])
        if len(anchors)<5:
            continue
        centers=[(w['top']+w['bottom'])/2 for w in anchors]
        spacings=[b-a for a,b in zip(centers,centers[1:])]
        step=median(spacings)
        if step<=yt or any(abs(s-step)>step*.3 for s in spacings):
            continue
        ys=[top]+[(a+b)/2 for a,b in zip(centers,centers[1:])]+[bottom]
        # Preceding complete physical rows are actual column headers. Small
        # partial cells protruding above the frame are not part of the matrix.
        for previous in reversed(table.rows[:ri]):
            hb=[b for b in previous.cells if b]
            if len(hb)!=len(boxes) or abs(max(b[3] for b in hb)-ys[0])>1:
                break
            header_top=min(b[1] for b in hb)
            chars=[c for c in page.chars if xs[0]<(c['x0']+c['x1'])/2<xs[-1]
                   and header_top<(c['top']+c['bottom'])/2<ys[0]]
            if not chars:
                break
            ys.insert(0,header_top)
        groups=defaultdict(list)
        for char in page.chars:
            x,y=(char['x0']+char['x1'])/2,(char['top']+char['bottom'])/2
            if xs[0]<=x<xs[-1] and ys[0]<=y<ys[-1]:
                groups[(bisect_right(ys,y)-1,bisect_right(xs,x)-1)].append(char)
        cells=[]
        for row in range(len(ys)-1):
            for col in range(len(xs)-1):
                text=extract_text(groups[(row,col)],x_tolerance=xt,y_tolerance=yt) or ''
                cells.append(dict(bbox=[xs[col],ys[row],xs[col+1],ys[row+1]],text=text))
        # Reject accidental paragraph recovery: at least two numeric columns
        # must be present in most of the anchored data rows.
        header_count=len(ys)-1-len(anchors)
        cols=len(xs)-1
        numeric_rows=sum(sum(bool(NUMERIC.fullmatch(c['text'])) for c in cells[r*cols:(r+1)*cols])>=2
                         for r in range(header_count,len(ys)-1))
        if numeric_rows<len(anchors)*.6:
            continue
        result=make_table([xs[0],ys[0],xs[-1],ys[-1]],cells,page.page_number,matrix,parse_key,ordinal,
                          'pdfplumber_partial_grid_row_alignment')
        result['review_reasons'].append('inferred_body_row_boundaries_need_review')
        return result
    return None


def _clean_header_frames(page, detected):
    """Remove inset, same-colour text backgrounds, retaining visible grid lines."""
    rects=[r for r in getattr(page,'rects',[]) if not r.get('stroke') and r.get('fill') and r['x1']-r['x0']>2 and r['bottom']-r['top']>2]
    bad=set()
    for inner in rects:
        width=inner['x1']-inner['x0'];height=inner['bottom']-inner['top']
        for outer in rects:
            if inner is outer or inner.get('non_stroking_color')!=outer.get('non_stroking_color'):continue
            if not width+2<outer['x1']-outer['x0']<width*2 or outer['bottom']-outer['top']>height*8:continue
            if outer['x0']+1<inner['x0'] and inner['x1']<outer['x1']-1 and outer['top']-.1<=inner['top'] and inner['bottom']<=outer['bottom']+.1:
                bad.add((inner['x0'],inner['x1'],inner['top'],inner['bottom']));break
    if not bad:return None
    return page.filter(lambda o:o.get('object_type')!='rect' or (o['x0'],o['x1'],o['top'],o['bottom']) not in bad)


def _open_outer_edges(page, detected):
    """An open-sided table still has repeated, source-drawn row boundaries."""
    bounds=[]
    for table in detected:
        if len(table.rows)<4:continue
        horizontal=[e for e in page.edges if e['orientation']=='h' and table.bbox[1]-1<=e['top']<=table.bbox[3]+1]
        for side,key,inside in [('left','x0',table.bbox[0]),('right','x1',table.bbox[2])]:
            candidates=defaultdict(set)
            for e in horizontal:
                x=e[key]
                if (inside-x>10 if side=='left' else x-inside>10):candidates[round(x)].add(round(e['top']))
            supported=[x for x,ys in candidates.items() if len(ys)>=4]
            if supported:bounds.append(min(supported) if side=='left' else max(supported))
    return sorted(set(bounds))


def _open_top_edges(page,detected):
    tops=[]
    for table in detected:
        if len(table.rows)<3:continue
        candidates=defaultdict(list)
        for edge in page.edges:
            if edge['orientation']=='v' and table.bbox[0]-1<=edge['x0']<=table.bbox[2]+1 and edge['top']<table.bbox[1]-3 and edge['bottom']>=table.bbox[1]-2:
                candidates[round(edge['top'],1)].append(edge['x0'])
        for top,xs in candidates.items():
            if len(_axis(xs))<3:continue
            above=[w for w in page.extract_words() if table.bbox[0]<(w['x0']+w['x1'])/2<table.bbox[2] and top<(w['top']+w['bottom'])/2<table.bbox[1]]
            if len(above)>=3 and sum(bool(NUMERIC.fullmatch(w['text'])) for w in above)>=len(above)*.8:tops.append(top)
    return sorted(set(tops))


def extract_native_tables(page, matrix, parse_key, allow_borderless=False, rejected=None, _repair_geometry=True):
    result = []
    xt,yt=text_tolerances(page)
    line_tolerance=max(1,yt)
    settings={
        "vertical_strategy": "lines", "horizontal_strategy": "lines",
        "snap_tolerance": line_tolerance, "join_tolerance": line_tolerance, "intersection_tolerance": line_tolerance,
    }
    detected = page.find_tables(table_settings=settings)
    if allow_borderless and _repair_geometry:
        cleaned=_clean_header_frames(page,detected)
        if cleaned is not None:return extract_native_tables(cleaned,matrix,parse_key,allow_borderless=True,rejected=rejected,_repair_geometry=False)
        outer=_open_outer_edges(page,detected)
        tops=_open_top_edges(page,detected)
        if outer or tops:detected=page.find_tables(table_settings={**settings,**({'explicit_vertical_lines':outer} if outer else {}),**({'explicit_horizontal_lines':tops} if tops else {})})
    method = "pdfplumber_ruled_cells"
    if not detected:
        return extract_borderless_tables(page, matrix, parse_key) if allow_borderless else []
    glyphs = GlyphIndex(page.chars)
    for index, table in enumerate(detected):
        if len(table.rows) < 2 or len(table.cells) < 4:
            continue
        recovered=_recover_partial_grid(page,table,matrix,parse_key,index) if allow_borderless else None
        if recovered:
            result.append(recovered)
            continue
        issue = grid_issue(list(set(table.cells)))
        if issue:
            if rejected is not None:
                rejected.append(dict(reason=issue, native_bounding_box=list(table.bbox),
                                     cell_boxes=[list(b) for b in table.cells]))
            # Keep the original text as prose. Invalid rectangles must never
            # suppress words or become duplicate financial cell values.
            continue
        cells = []
        for bbox in sorted(set(table.cells), key=lambda c: (c[1], c[0])):
            # Filter by glyph centre; pdfplumber crop includes boundary-overlapping
            # glyphs, which otherwise duplicates adjacent financial values.
            chars = glyphs.within(bbox)
            text = extract_text(chars, x_tolerance=xt, y_tolerance=yt) or ""
            cells.append(dict(bbox=bbox, text=text))
        result.append(make_table(table.bbox, cells, page.page_number, matrix, parse_key, index,
                                 method))
        if method == "pdfplumber_aligned_text_candidate":
            result[-1]["review_reasons"].append("borderless_table_structure_not_verified")
    # A decorative two-cell frame may be found and then discarded. It must
    # not prevent recovery of the real numeric matrix elsewhere on the page.
    annual_header=any(any(c['row_index']<=2 and re.search('保单年度|保单年数',re.sub(r'\s','',c['raw_text'])) for c in t['cells']) for t in result)
    if allow_borderless and not annual_header and (not result or any(sum(len(c["raw_text"].splitlines()) >= 4 for c in t["cells"]) >= 2 for t in result)):
        aligned = extract_borderless_tables(page, matrix, parse_key)
        if aligned:
            return aligned
    for table in result:
        if any(len(c["raw_text"].splitlines()) >= 4 for c in table["cells"]):
            table["review_reasons"].append("table_body_rows_not_fully_recovered")
    return result


def raster_numeric_candidates(ocr_blocks, page, matrix, parse_key):
    from types import SimpleNamespace
    words = [dict(text=b["raw_text"], x0=b["native_bounding_box"][0], top=b["native_bounding_box"][1],
                  x1=b["native_bounding_box"][2], bottom=b["native_bounding_box"][3])
             for b in ocr_blocks if NUMERIC.fullmatch(b["raw_text"])]
    proxy = SimpleNamespace(page_number=page.page_number, extract_words=lambda **kwargs: words)
    tables = extract_borderless_tables(proxy, matrix, parse_key)
    for table in tables:
        table["extraction_method"] = "opencv_row_projection_numeric_alignment_rapidocr"
        table["review_reasons"] += ["ocr_numeric_values_not_verified", "raster_grid_spans_not_verified"]
        for cell in table["cells"]:
            box = cell["native_bounding_box"]
            selected = [b for b in ocr_blocks if box[0] <= sum(b["native_bounding_box"][::2])/2 < box[2]
                        and box[1] <= sum(b["native_bounding_box"][1::2])/2 < box[3]]
            cell["source_block_ids"] = [b["block_id"] for b in selected]
            cell["extraction_confidence"] = min((b["extraction_confidence"] for b in selected), default=None)
    return tables


def extract_raster_tables(image, ocr_blocks, page, matrix, parse_key):
    """Ruled rectangular grids only; OCR free-form tables stay in review."""
    import cv2
    import numpy as np
    gray = cv2.cvtColor(np.asarray(image.convert("RGB")), cv2.COLOR_RGB2GRAY)
    binary = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY_INV, 31, 15)
    h, w = gray.shape
    horizontal = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                                 cv2.getStructuringElement(cv2.MORPH_RECT, (max(20, w//25), 1)))
    vertical = cv2.morphologyEx(binary, cv2.MORPH_OPEN,
                               cv2.getStructuringElement(cv2.MORPH_RECT, (1, max(20, h//25))))
    grid = cv2.bitwise_or(horizontal, vertical)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(grid)
    sx, sy = page.width/w, page.height/h
    xoff, yoff = page.bbox[:2]
    tables = []
    for x, y, cw, ch, area in stats[1:]:
        if cw < w*.12 or ch < 30 or area < 100:
            continue
        def centers(indices):
            groups = []
            for i in indices:
                if not groups or i > groups[-1][-1]+2:
                    groups.append([int(i)])
                else:
                    groups[-1].append(int(i))
            return [sum(g)/len(g) for g in groups]
        xs = centers(np.flatnonzero((vertical[y:y+ch, x:x+cw]>0).sum(axis=0) > ch*.65))
        ys = centers(np.flatnonzero((horizontal[y:y+ch, x:x+cw]>0).sum(axis=1) > cw*.65))
        if len(xs) < 3 or len(ys) < 3:
            continue
        cells = []
        for row in range(len(ys)-1):
            for col in range(len(xs)-1):
                box = [xoff+(x+xs[col])*sx, yoff+(y+ys[row])*sy,
                       xoff+(x+xs[col+1])*sx, yoff+(y+ys[row+1])*sy]
                selected = [b for b in ocr_blocks if box[0] <= sum(b["native_bounding_box"][::2])/2 < box[2]
                            and box[1] <= sum(b["native_bounding_box"][1::2])/2 < box[3]]
                selected.sort(key=lambda b: (b["native_bounding_box"][1], b["native_bounding_box"][0]))
                cells.append(dict(bbox=box, text="\n".join(b["raw_text"] for b in selected),
                                  confidence=min((b["extraction_confidence"] for b in selected), default=None),
                                  block_ids=[b["block_id"] for b in selected]))
        bbox = [xoff+(x+xs[0])*sx, yoff+(y+ys[0])*sy,
                xoff+(x+xs[-1])*sx, yoff+(y+ys[-1])*sy]
        table = make_table(bbox, cells, page.page_number, matrix, parse_key, len(tables),
                           "opencv_ruled_grid_rapidocr")
        table["review_reasons"] += ["raster_grid_spans_not_verified", "ocr_numeric_values_not_verified"]
        tables.append(table)
    if not tables or any(sum(len(c["raw_text"].splitlines()) >= 4 for c in table["cells"]) >= 2 for table in tables):
        numeric = raster_numeric_candidates(ocr_blocks, page, matrix, parse_key)
        if numeric:
            return numeric
    return tables
