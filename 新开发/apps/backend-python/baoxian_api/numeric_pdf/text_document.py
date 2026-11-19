"""Parse narrative insurance PDFs and expose their embedded tables as grids."""
from hashlib import sha256

from .chunking import build_chunks
from .common import stable_hash
from .compact_native import serialize_grid
from .continuations import bind_continuations
from .models import ParseConfig
from .native import extract_document
from .validation import validate_layout


PARSER_VERSION = 'document-content-v1'


def parse_text_document(body, document_id, document_type, expected_digest):
    digest = sha256(body).hexdigest()
    if digest != expected_digest:
        raise ValueError('file_hash_mismatch')
    version = stable_hash(['document-version-v1', str(document_id), digest])
    config = ParseConfig(ocr_mode='auto', layout_mode='xy_cut', extract_tables=True)
    parse_key = stable_hash([PARSER_VERSION, version, document_type, config.to_dict()])
    extracted = bind_continuations(extract_document(body, config, parse_key, document_type))
    warnings = set(validate_layout(extracted))
    for page in extracted['pages']:
        warnings.update(page.get('review_reasons', []))
        for rejected in page.get('table_detection_rejections', []):
            rejected.pop('cell_boxes', None)
    for block in extracted['blocks']:
        block['original_reference'].update(document_id=document_id, document_version_id=version)
    layout = {
        **extracted,
        'schema': PARSER_VERSION,
        'schema_version': 1,
        'document_id': document_id,
        'document_type': document_type,
        'document_version_id': version,
        'source_sha256': digest,
        'parse_key': parse_key,
        'review_reasons': sorted(warnings),
        'tables': [serialize_grid(table) for table in extracted['tables']],
    }
    unread = sum(page['status'] in ('failed', 'needs_ocr', 'not_processed_limit') for page in layout['pages'])
    if unread == layout['page_count']:
        layout['status'] = 'failed'
    elif unread:
        layout['status'] = 'partial'
    else:
        layout['status'] = 'completed_with_warnings' if warnings else 'completed'
    layout['layout_sha256'] = stable_hash(layout)
    chunks = build_chunks(layout, layout['layout_sha256'])
    layout.update(chunks=chunks['chunks'], tokenizer=chunks['tokenizer'], chunk_budget=chunks['budget'])
    return layout
