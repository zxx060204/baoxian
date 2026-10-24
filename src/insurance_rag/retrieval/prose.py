"""Recover prose mistakenly boxed as tables, without treating rate cells as text."""
import re
from insurance_rag.parsing.common import stable_hash

def prose_table_blocks(layout):
    if layout.get('metadata',{}).get('document_type')!='terms':return []
    blocks=sorted(layout['blocks'],key=lambda b:(b['page_number'],b.get('reading_order') or 0))
    tables={t['table_id']:t for t in layout.get('tables',[])};recovered=[]
    for i,b in enumerate(blocks):
        if b.get('block_type')!='table':continue
        t=tables.get(b.get('table_id'));cells=(t or {}).get('cells',[])
        populated=[c for c in cells if c.get('raw_text','').strip()]
        if not populated or any(c.get('numeric_tokens') for c in populated):continue
        rows=[c['row_index'] for c in populated]
        if len(rows)!=len(set(rows)) or any(len(re.sub(r'\s+','',c['raw_text']))<12 for c in populated):continue
        text=b.get('raw_text','')
        if len(re.findall(r'[\u3400-\u9fff]',text))<60 or not re.search(r'[。；]',text):continue
        before=blocks[i-1] if i else {};after=blocks[i+1] if i+1<len(blocks) else {}
        cid=before.get('clause_id');sid=before.get('parent_section_id')
        if not cid or not sid or (cid,sid)!=(after.get('clause_id'),after.get('parent_section_id')):continue
        if any(abs(x.get('page_number',-99)-b['page_number'])>1 for x in (before,after)):continue
        recovered.append({**b,'block_type':'paragraph','clause_id':cid,'parent_section_id':sid,'recovery_reason':'single_prose_cell_per_row_between_same_clause_blocks'})
    return recovered

def restore_prose_members(members,layout):
    if not members:return members
    anchor=members[0]
    if layout.get('document_version_id')!=anchor['document_version_id']:raise ValueError('context_layout_version_mismatch')
    represented={s['block_id'] for m in members for s in m['source_spans']}
    extra=[]
    for b in prose_table_blocks(layout):
        if b['clause_id']!=anchor['clause_id'] or b['parent_section_id']!=anchor['section_id'] or b['block_id'] in represented:continue
        text=b['raw_text'];sid=stable_hash(['layout-prose-recovery-v1',anchor['document_version_id'],anchor['layout_sha256'],b['block_id']])
        extra.append({**anchor,'chunk_id':sid,'raw_text':text,'chunk_index':0,'publication_status':'quarantined',
            'review_reasons':['prose_table_recovery_requires_review'],'restored_source_block_id':b['block_id'],
            'source_spans':[{'block_id':b['block_id'],'source_character_range':[0,len(text)],'chunk_character_range':[0,len(text)],'page_number':b['page_number'],'source_bbox':b['bounding_box'],'original_reference':b.get('original_reference')}]})
    order={b['block_id']:(b['page_number'],b.get('reading_order') or 0) for b in layout['blocks']}
    return sorted(members+extra,key=lambda m:order.get(m['source_spans'][0]['block_id'],(m['page_start'],m['chunk_index']))) if extra else members
