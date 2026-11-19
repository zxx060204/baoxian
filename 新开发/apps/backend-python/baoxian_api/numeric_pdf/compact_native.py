"""Extract a compact numeric table input directly from native PDF content.

Cells are temporary recognition data, never part of the returned structure.
This entry point currently covers native PDF pages; it rejects pages needing OCR.
"""
from pathlib import Path
from hashlib import sha256
from io import BytesIO
from statistics import median

import pdfplumber
from pypdf import PdfReader

from .common import file_hash
from .continuations import bind_continuations
from .layout import native_lines
from .models import ParseConfig
from .preflight import inspect_page
from .table_evidence import capture
from .tables import extract_native_tables, temporary_table_cells


def _grid(table):
    rows, cols = table['row_count'], table['column_count']
    result = [[None for _ in range(cols)] for _ in range(rows)]
    for cell in table.get('cells', []):
        row, col = cell['row_index'], cell['column_index']
        height, width = cell.get('row_span', 1), cell.get('column_span', 1)
        if min(row, col) < 0 or min(height, width) < 1 or row + height > rows or col + width > cols:
            raise ValueError('invalid_temporary_cell_span')
        for r in range(row, row + height):
            for c in range(col, col + width):
                if result[r][c] is not None:
                    raise ValueError('overlapping_temporary_cells')
                result[r][c] = [cell['raw_text'], height, width] if (r, c) == (row, col) else -1
    if table.get('fill_empty_from_continuation_axis'):
        for row in result:
            for col, value in enumerate(row):
                if value is None:
                    row[col] = ['', 1, 1]
    return result


def _relations(line, tables, page=None):
    box = [line['x0'], line['top'], line['x1'], line['bottom']]
    compatible = [t for t in tables if min(box[2], t['native_bounding_box'][2]) >
                  max(box[0], t['native_bounding_box'][0])]
    before = [t for t in compatible if (page is not None and t['page_number'] < page) or
              (page is None or t['page_number'] == page) and t['native_bounding_box'][3] <= box[1] + .5]
    after = [t for t in compatible if (page is not None and t['page_number'] > page) or
             (page is None or t['page_number'] == page) and t['native_bounding_box'][1] >= box[3] - .5]
    previous = max(before, key=lambda t: (t.get('page_number', 1), t['native_bounding_box'][3]), default=None)
    following = min(after, key=lambda t: (t.get('page_number', 1), t['native_bounding_box'][1]), default=None)
    if previous and following and page is not None and previous['page_number'] != following['page_number']:
        candidates = following.get('continuation_candidates') or []
        unresolved = following.get('continuation_status') in ('ambiguous', 'unresolved')
        related = (following.get('continuation_of') == previous['table_id'] or
                   unresolved and (not candidates or previous['table_id'] in candidates))
        if not related:
            if previous['page_number'] != page:
                previous = None
            if following['page_number'] != page:
                following = None
    elif page is not None:
        if previous and previous['page_number'] != page:
            previous = None
        if following and following['page_number'] != page:
            following = None
    relations = []
    if previous:
        relations.append({'table_id': previous['table_id'], 'position': 'between_tables' if following else 'after'})
    if following:
        relations.append({'table_id': following['table_id'], 'position': 'between_tables' if previous else 'before'})
    return box, relations


def _fragment_text_line(line, tables):
    """Suppress only whole text lines already represented by fragment tokens."""
    text = ''.join(line['text'].split())
    for table in tables:
        if table.get('cells') or not table.get('tokens'):
            continue
        nearby = [token for token in table['tokens']
                  if min(line['x1'], token['native_bounding_box'][2]) > max(line['x0'], token['native_bounding_box'][0])
                  and min(line['bottom'], token['native_bounding_box'][3]) > max(line['top'], token['native_bounding_box'][1])]
        if nearby and ''.join(''.join(t['text'].split()) for t in sorted(nearby, key=lambda x: x['native_bounding_box'][0])) == text:
            return True
    return False


def _group_contexts(lines):
    groups = []
    for line in sorted(lines, key=lambda x: (x['page'], x['bbox'][1], x['bbox'][0])):
        selected = None
        for group in reversed(groups):
            if group['page'] != line['page'] or group['relations'] != line['relations']:
                continue
            last, box = group['_last'], line['bbox']
            height = median([last[3] - last[1], box[3] - box[1]])
            gap = box[1] - last[3]
            if (-.5 <= gap <= max(1, height) * 1.8 and
                    abs(box[0] - last[0]) <= max(1, height) * 1.5 and
                    min(box[2], last[2]) > max(box[0], last[0])):
                selected = group
                break
        if selected is None:
            groups.append({**line, '_last': line['bbox']})
        else:
            selected['text'] += '\n' + line['text']
            old, box = selected['bbox'], line['bbox']
            selected['bbox'] = [min(old[0], box[0]), min(old[1], box[1]),
                                max(old[2], box[2]), max(old[3], box[3])]
            selected['_last'] = box
    for group in groups:
        del group['_last']
    return groups


def serialize_grid(table):
    cells, tokens = table.get('cells', []), table.get('tokens', [])
    grid = _grid(table) if cells else []
    missing = any(slot is None for row in grid for slot in row)
    status = 'partial' if missing else 'complete' if cells else 'fragment'
    item = {'table_id': table['table_id'], 'page': table['page_number'],
            'bbox': table['native_bounding_box'], 'row_count': table['row_count'],
            'column_count': table['column_count'],
            'x_edges': table.get('column_edges_native', []),
            'y_edges': table.get('row_edges_native', []),
            'header_rows': table.get('header_rows_candidate', []),
            'continuation_of': table.get('continuation_of'),
            'structure_status': status, 'grid': grid}
    if table.get('fill_empty_from_continuation_axis'):
        occupied = sum(cell.get('row_span', 1) * cell.get('column_span', 1) for cell in cells)
        item['empty_cell_inference'] = {'method': 'inherited_continuation_axis',
                                        'count': table['row_count'] * table['column_count'] - occupied}
    if tokens and (not cells or table.get('structure_status') == 'recovered_continuation'):
        item['tokens'] = [{'text': t['text'], 'bbox': t['native_bounding_box'],
                           'row_index': t.get('row_index')} for t in tokens]
    if table.get('continuation_status') == 'ambiguous':
        item['continuation_status'] = 'ambiguous'
        item['continuation_candidates'] = table['continuation_candidates']
    return item


def extract_mapping_input(source, document_version_id, document_type, *, config=None, expected_source_sha256=None):
    """Return native-PDF mapping input; no database, cache or per-cell IDs.

    layout_sha256 is null because there is no preceding layout file. The source
    hash is recorded separately and is never presented as a layout hash.
    """
    if document_type not in ('premium_rate', 'cash_value'):
        raise ValueError('numeric_document_type_required')
    if not isinstance(source, bytes):
        source = Path(source)
    config = config or ParseConfig(extract_tables=True, ocr_mode='off')
    if config.ocr_mode != 'off':
        raise ValueError('compact_native_requires_ocr_off')
    if config.page_selection != 'all' or config.page_numbers:
        raise ValueError('compact_native_requires_all_pages')
    in_memory = isinstance(source, bytes)
    digest = sha256(source).hexdigest() if in_memory else file_hash(source)
    if expected_source_sha256 is not None and digest != expected_source_sha256:
        raise ValueError('file_hash_mismatch')
    reader = PdfReader(BytesIO(source) if in_memory else source, strict=True)
    if reader.is_encrypted and not reader.decrypt(''):
        raise ValueError('encrypted_pdf_not_supported')
    if not reader.pages or len(reader.pages) > config.max_pages:
        raise ValueError('compact_native_page_count_invalid')
    pages, tables, lines = [], [], []
    with temporary_table_cells(), pdfplumber.open(BytesIO(source) if in_memory else source) as pdf:
        if len(pdf.pages) != len(reader.pages):
            raise ValueError('pdf_page_count_mismatch')
        for index, page in enumerate(pdf.pages):
            info = inspect_page(page, reader.pages[index], config)
            if info['content_type'] in ('scanned', 'mixed') or info.get('suspicious_char_ratio', 0) > config.suspicious_char_ratio:
                raise ValueError('compact_native_page_needs_ocr')
            rejected = []
            found = extract_native_tables(page, info['native_to_standard'], digest,
                                          allow_borderless=True, rejected=rejected)
            found = capture(page, found, info['native_to_standard'], digest, rejected, compact=True)
            page_lines = native_lines(page, found)
            for line in page_lines:
                if line['text'].strip() and not _fragment_text_line(line, found):
                    box = [line['x0'], line['top'], line['x1'], line['bottom']]
                    lines.append({'text': line['text'], 'page': index + 1,
                                  'bbox': box})
            tables.extend(found)
            pages.append(info)
            page.close()
    layout = bind_continuations({'pages': pages, 'tables': tables}, temporary=True)
    for line in lines:
        box = line['bbox']
        _, line['relations'] = _relations({'x0': box[0], 'top': box[1], 'x1': box[2], 'bottom': box[3]},
                                         layout['tables'], page=line['page'])
    first = min(((t['page_number'], t['native_bounding_box'][1]) for t in layout['tables']), default=None)
    contexts = []
    for index, line in enumerate(_group_contexts(lines)):
        opening = first is not None and (line['page'], line['bbox'][3]) <= first
        contexts.append({'context_id': f'c{index + 1}', **line,
                         'document_position': 'opening' if opening else 'body'})
    output_tables = [serialize_grid(table) for table in layout['tables']]
    if not in_memory and file_hash(source) != digest:
        raise ValueError('input_changed_during_parse')
    return {'schema': 'mapping-input-v1', 'document_version_id': document_version_id,
            'document_type': document_type, 'layout_sha256': None, 'source_sha256': digest,
            'pages': [{'page': p['page_number'], 'width': p['width'], 'height': p['height'],
                       'rotation': p['rotation']} for p in pages],
            'contexts': contexts, 'tables': output_tables}
