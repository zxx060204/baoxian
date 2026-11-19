"""Bind headerless page-boundary tables to physical predecessors."""
from collections import defaultdict
from copy import deepcopy
from statistics import median
import re

from .common import stable_hash
from .preflight import transform_bbox

POLICY = 'physical-continuation-v1'


def _box(item):
    return item.get('native_bounding_box') or item.get('bounding_box')


def _axis(table):
    if table.get('column_edges_native'):
        return table['column_edges_native']
    cells = table.get('cells', [])
    if not cells:
        return []
    edges = sorted({round(v, 3) for c in cells if _box(c) for v in (_box(c)[0], _box(c)[2])})
    return edges if len(edges) == table.get('column_count', 0) + 1 else []


def _row_head(table, last=False):
    cells = [c for c in table.get('cells', []) if c.get('column_index') == 0 and str(c.get('raw_text', '')).strip()]
    if cells:
        c = max(cells, key=lambda c: c['row_index']) if last else min(cells, key=lambda c: c['row_index'])
        return str(c['raw_text']).strip()
    tokens = table.get('tokens', [])
    if tokens:
        row = max(t['row_index'] for t in tokens) if last else min(t['row_index'] for t in tokens)
        return min((t for t in tokens if t['row_index'] == row), key=lambda t: _box(t)[0])['text'].strip()
    return ''


def _progresses(previous, current):
    if not previous or not current:
        return False
    if re.fullmatch(r'\d+(?:\.\d+)?', previous) and re.fullmatch(r'\d+(?:\.\d+)?', current):
        return float(current) >= float(previous)
    return re.sub(r'\s+', '', previous) == re.sub(r'\s+', '', current)


def _has_header_or_context(table):
    # Only above-table context with horizontally overlapping geometry belongs here.
    box = _box(table)
    for context in table.get('context_candidates', []):
        if context.get('position') != 'above' or not str(context.get('raw_text', '')).strip():
            continue
        cb = _box(context)
        if not cb or not box or min(cb[2], box[2]) > max(cb[0], box[0]):
            return True
    cells = table.get('cells', [])
    if not cells:
        return False
    first = min(c['row_index'] for c in cells)
    row = [c for c in cells if c['row_index'] == first]
    return any(re.search(r'年度|年龄|性别|保险期间|保障期间|缴费|交费', str(c.get('raw_text', ''))) for c in row)


def _match(parent, child, scale):
    edges = _axis(parent)
    if not edges or not _progresses(_row_head(parent, True), _row_head(child)):
        return None
    edges = [x * scale for x in edges]
    if child.get('tokens') and not child.get('cells'):
        locations = [_box(t) for t in child['tokens']]
        if not all(locations):
            return None
        centers = [(b[0] + b[2]) / 2 for b in locations]
        matches = [next((i for i in range(len(edges)-1) if edges[i] <= x < edges[i+1]), None) for x in centers]
        if any(i is None for i in matches):
            return None
        # The first source column must align with the parent's first column.
        if matches[centers.index(min(centers))] != 0:
            return None
        score = abs(centers[0] - (edges[matches[0]] + edges[matches[0]+1])/2)
        return score, edges
    child_edges = _axis(child)
    if len(child_edges) != len(edges):
        return None
    tolerance = max(2.0, median(b-a for a, b in zip(edges, edges[1:])) * .1)
    differences = [abs(a-b) for a, b in zip(edges, child_edges)]
    if max(differences) > tolerance:
        return None
    return sum(differences)/len(differences), edges


def _recover(table, edges, matrix, *, temporary=False):
    groups = defaultdict(list)
    for index, token in enumerate(table['tokens']):
        b = _box(token); x = (b[0]+b[2])/2
        col = next(i for i in range(len(edges)-1) if edges[i] <= x < edges[i+1])
        groups[(token['row_index'], col)].append((index, token))
    cells = []
    for (row, col), tokens in sorted(groups.items()):
        tokens.sort(key=lambda item: _box(item[1])[0])
        boxes = [_box(t) for _, t in tokens]
        box = [edges[col], min(b[1] for b in boxes), edges[col+1], max(b[3] for b in boxes)]
        text = ' '.join(t['text'] for _, t in tokens)
        cell = {'row_index': row,
                      'column_index': col, 'row_span': 1, 'column_span': 1, 'raw_text': text,
                      'native_bounding_box': box, 'source_token_indices': [i for i, _ in tokens],
                      'header_candidate': False}
        if not temporary:
            cell.update(cell_id=stable_hash([POLICY, table['table_id'], row, col]), normalized_text=text,
                        numeric_tokens=[], bounding_box=transform_bbox(box, matrix), source_block_ids=[],
                        extraction_confidence=None,
                        original_reference={'page_number': table['page_number'], 'native_bbox': box})
        cells.append(cell)
    result = {**table, 'cells': cells, 'column_count': len(edges)-1,
              'structure_status': 'recovered_continuation', 'header_rows_candidate': [],
              'column_edges_native': edges}
    if temporary:
        # A uniquely bound token continuation occupies the full inherited axis.
        # Empty slots are materialized when constructing the output grid; do not
        # fabricate per-cell geometry or temporary cell records for them.
        result['fill_empty_from_continuation_axis'] = True
    return result


def bind_continuations(layout, *, temporary=False):
    """Use adjacent physical pages; preserve uncertain candidates without binding."""
    # Compact extraction owns these temporary tables and can bind in place.
    out = layout if temporary else deepcopy(layout)
    tables = out.get('tables', [])
    pages = {p['page_number']: p for p in out.get('pages', [])}
    for number in sorted(pages):
        if number-1 not in pages:
            continue
        parents = [t for t in tables if t['page_number'] == number-1 and t.get('cells') and _axis(t)]
        children = [t for t in tables if t['page_number'] == number and not t.get('continuation_of') and not _has_header_or_context(t)]
        # Continuations must be the first table in their horizontal lane on the new page.
        current = [t for t in tables if t['page_number'] == number and _box(t)]
        children = [c for c in children if not any(q is not c and _box(q)[1] < _box(c)[1] and
                    min(_box(q)[2], _box(c)[2]) > max(_box(q)[0], _box(c)[0]) for q in current)]
        if not parents or not children:
            continue
        # A predecessor must be the lowest table in its own horizontal lane.
        parents = [p for p in parents if not any(q is not p and _box(q)[1] > _box(p)[1] and
                   min(_box(q)[2], _box(p)[2]) > max(_box(q)[0], _box(p)[0]) for q in parents)]
        old_width = pages[number-1].get('width') or 1
        width = pages[number].get('width') or old_width
        scale = width / old_width
        candidates = {}
        for child in children:
            matches = []
            for parent in parents:
                match = _match(parent, child, scale)
                if match is not None:
                    matches.append((match[0], parent, match[1]))
            if matches:
                matches.sort(key=lambda x: x[0]); candidates[child['table_id']] = matches
        # Do not assign either side when a plausible competing match exists.
        for child in children:
            matches = candidates.get(child['table_id'], [])
            if not matches:
                continue
            if len(matches) != 1 or sum(any(p['table_id'] == matches[0][1]['table_id'] for _, p, _ in other)
                                        for other in candidates.values()) != 1:
                child['continuation_status'] = 'ambiguous'
                child['continuation_candidates'] = [p['table_id'] for _, p, _ in matches]
                continue
            _, parent, edges = matches[0]
            if child.get('tokens') and not child.get('cells'):
                recovered = _recover(child, edges, pages[number].get('native_to_standard', [1, 0, 0, 1, 0, 0]), temporary=temporary)
                child.clear(); child.update(recovered)
            child['continuation_of'] = parent['table_id']
            child['continuation_status'] = 'linked'
            child['continuation_evidence'] = {'policy': POLICY, 'parent_page': number-1,
                'previous_row_head': _row_head(parent, True), 'current_row_head': _row_head(child),
                'column_alignment': 'parent_grid', 'page_width_scale': scale}
    out['continuation_policy'] = POLICY
    return out
