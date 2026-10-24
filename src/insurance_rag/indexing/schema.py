"""Validated Chunk v2 contract shared by export and OpenSearch serialization."""
from typing import TypedDict

class Chunk(TypedDict):
    schema_version:int
    chunk_id:str
    record_id:str
    document_id:str
    document_version_id:str
    product_id:str | None
    product_version_id:str | None
    section_id:str | None
    clause_id:str | None
    content:str
    raw_text:str
    embedding_text:str
    bm25_text:str
    source_spans:list[dict]
    page_start:int
    page_end:int
    chunk_index:int
    prev_chunk_id:str | None
    next_chunk_id:str | None

REQUIRED=('chunk_id','record_id','document_id','document_version_id','product_id','product_version_id',
          'section_id','clause_id','content','raw_text','embedding_text','bm25_text','source_spans',
          'page_start','page_end','chunk_index','prev_chunk_id','next_chunk_id')
def validate_chunk(chunk):
    if any(k not in chunk for k in REQUIRED):raise ValueError('chunk_schema_missing_field')
    if chunk.get('schema_version')!=2:raise ValueError('chunk_schema_requires_rebuild')
    if chunk['content']!=chunk['embedding_text'] or chunk['content']!=chunk['bm25_text']:
        raise ValueError('semantic_and_lexical_content_differ')
    if chunk['record_id']!=chunk['chunk_id']:raise ValueError('chunk_identity_mismatch')
    if not chunk['source_spans']:raise ValueError('chunk_source_spans_missing')
    for k in ('page_start','page_end','chunk_index'):
        if isinstance(chunk[k],bool) or not isinstance(chunk[k],int):raise ValueError('chunk_integer_field_invalid')
    if chunk['page_start']<1 or chunk['page_end']<chunk['page_start'] or chunk['chunk_index']<0:
        raise ValueError('chunk_page_or_index_invalid')
    if chunk.get('publication_status')=='eligible' and (chunk['review_reasons'] or not chunk['clause_id']):
        raise ValueError('unresolved_chunk_cannot_publish')
    return chunk
