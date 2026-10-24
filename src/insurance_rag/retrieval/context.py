"""Restore clauses, falling back to bounded contiguous hit neighborhoods."""
from insurance_rag.parsing.common import stable_hash

def construct_context(hits,chunks,*,counter,max_tokens=6000,development=False,layout_loader=None):
    if isinstance(max_tokens,bool) or not isinstance(max_tokens,int) or max_tokens<1:raise ValueError('invalid_context_budget')
    registry={c['chunk_id']:c for c in chunks}
    if len(registry)!=len(chunks):raise ValueError('duplicate_context_chunk_id')
    def identity(c):
        return (c['product_version_id'],c['document_version_id'],c['section_id'],c['clause_id'] or c['chunk_id'],c.get('semantic_role','body'))
    # Validate every incoming hit, including hits covered by an earlier restoration.
    for hit in hits:
        incoming=hit['record'];rid=incoming['chunk_id']
        if rid not in registry:raise ValueError('retrieved_chunk_not_in_frozen_snapshot')
        fields=('content','product_version_id','document_version_id','section_id','clause_id','source_file','source_sha256','layout_sha256')
        if any(incoming.get(field)!=registry[rid].get(field) for field in fields):raise ValueError('retrieved_chunk_snapshot_mismatch')
    groups={};seen=set();included=set();selected=[];omitted=[];fallbacks=[];full_text=''
    def assemble(members,c):
        body='';spans=[]
        for member in members:
            offset=len(body)
            for span in member['source_spans']:
                a,b=span['chunk_character_range']
                spans.append({**span,'chunk_id':member['chunk_id'],'body_character_range':[offset+a,offset+b]})
            body+=member['raw_text']
        item_text='产品：'+c['product_name']+'\n条款：'+c['clause_title']+'\n'+body
        candidate=(full_text+'\n\n' if full_text else '')+item_text
        return body,spans,candidate
    for hit in hits:
        rid=hit['record']['chunk_id']
        if rid in seen or rid in included:continue
        seen.add(rid);c=registry[rid];key=identity(c)
        if key in groups:continue
        members=sorted([r for r in chunks if identity(r)==key],key=lambda r:r['chunk_index'])
        if not development and any(r['publication_status']!='eligible' for r in members):
            omitted.append({'clause_id':c['clause_id'],'clause_title':c['clause_title'],'reason':'clause_contains_quarantined_content'});groups[key]=True;continue
        if development and layout_loader and c.get('clause_id'):
            from .prose import restore_prose_members
            members=restore_prose_members(members,layout_loader(c['document_version_id']))
        body,spans,candidate=assemble(members,c);mode='complete_clause';chosen=members;before=after=0
        if counter(candidate)>max_tokens:
            anchor=next(i for i,r in enumerate(members) if r['chunk_id']==rid)
            left=right=anchor
            # Do not duplicate an earlier window or bridge a gap through it.
            while left>max(0,anchor-2) and members[left-1]['chunk_id'] not in included:left-=1
            while right<min(len(members)-1,anchor+2) and members[right+1]['chunk_id'] not in included:right+=1
            while True:
                chosen=members[left:right+1];body,spans,candidate=assemble(chosen,c)
                if counter(candidate)<=max_tokens:break
                if left==right:
                    chosen=[];break
                if anchor-left>=right-anchor and left<anchor:left+=1
                else:right-=1
            if not chosen:
                omitted.append({'clause_id':c['clause_id'],'clause_title':c['clause_title'],'reason':'complete_clause_exceeds_context_budget',
                                'fallback_reason':'hit_chunk_exceeds_remaining_context_budget','hit_chunk_id':rid,'chunk_ids':[r['chunk_id'] for r in members]})
                continue
            before=anchor-left;after=right-anchor;mode='neighbor_window'
            fallbacks.append({'clause_id':c['clause_id'],'clause_title':c['clause_title'],'hit_chunk_id':rid,
                              'reason':'complete_clause_exceeds_context_budget','requested_neighbors':2,
                              'neighbors_before':before,'neighbors_after':after,'chunk_ids':[r['chunk_id'] for r in chosen],
                              'total_clause_chunks':len(members)})
        else:groups[key]=True
        full_text=candidate;included.update(r['chunk_id'] for r in chosen)
        selected.append({'product_version_id':key[0],'document_version_id':key[1],'section_id':key[2],
            'clause_id':c['clause_id'],'clause_title':c['clause_title'],'body':body,
            'chunk_ids':[r['chunk_id'] for r in chosen],'source_spans':spans,
            'page_start':min(r['page_start'] for r in chosen),'page_end':max(r['page_end'] for r in chosen),
            'textual_clause_complete':bool(c['clause_id']) and mode=='complete_clause',
            'restoration_mode':mode,'hit_chunk_id':rid,'neighbors_before':before,'neighbors_after':after,
            'restored_source_block_ids':[r['restored_source_block_id'] for r in chosen if r.get('restored_source_block_id')],
            'boundary_requires_review':any(r['review_reasons'] for r in chosen)})
    by_product={}
    for item in selected:by_product.setdefault(item['product_version_id'],[]).append(item)
    return {'content':full_text,'token_count':counter(full_text),'groups':by_product,'omitted':omitted,'fallbacks':fallbacks,
            'development_only':development,'source_snapshot_digest':stable_hash([c['chunk_id'] for c in chunks])}
