"""Evidence-preserving segmentation with an offline conservative byte budget."""
import re
from .common import stable_hash


def segment_text(text,limit,count):
    pieces=[];start=0
    while start<len(text):
        low,high=start+1,len(text)
        if count(text[start:low])>limit:raise ValueError('Budget cannot fit one source character')
        while low<high:
            mid=(low+high+1)//2
            if count(text[start:mid])<=limit:low=mid
            else:high=mid-1
        end=low
        if end<len(text):
            boundaries=[m.end()+start for m in re.finditer(r'[。！？!?\n]',text[start:end])]
            if boundaries and boundaries[-1]>start:end=boundaries[-1]
        pieces.append((start,end,text[start:end]));start=end
    return pieces


def build_chunks(layout,layout_sha256,limit=500,*,token_counter=None,tokenizer_name=None):
    if isinstance(limit,bool) or not isinstance(limit,int) or limit<4:raise ValueError('Invalid chunk budget')
    if token_counter is None:
        token_counter=lambda s:len(s.encode('utf-8'))
        tokenizer_name='utf8_byte_upper_bound_v1'
    elif not tokenizer_name:raise ValueError('An explicit tokenizer version is required')
    chunks=[]
    for block in sorted(layout['blocks'],key=lambda b:(b['page_number'],b.get('reading_order') is None,b.get('reading_order') or 0)):
        if block['block_type'] in ('image','table'):
            chunks.append({'chunk_id':stable_hash(['reference-chunk-v1',layout['document_version_id'],layout_sha256,block['block_id']]),
                'kind':block['block_type']+'_reference','raw_text':'','block_id':block['block_id'],
                'page_number':block['page_number'],'source_character_range':None,'token_count':0,
                'table_id':block.get('table_id'),'section_id':block.get('parent_section_id'),'clause_id':block.get('clause_id')})
            continue
        for start,end,text in segment_text(block['raw_text'],limit,token_counter):
            chunks.append({'chunk_id':stable_hash(['text-chunk-v1',layout['document_version_id'],layout_sha256,block['block_id'],start,end,tokenizer_name,limit]),
                'kind':'text','raw_text':text,'block_id':block['block_id'],'page_number':block['page_number'],
                'source_character_range':[start,end],'token_count':token_counter(text),
                'section_id':block.get('parent_section_id'),'clause_id':block.get('clause_id')})
    block_map={b['block_id']:b for b in layout['blocks']}
    for chunk in chunks:
        block=block_map[chunk['block_id']]
        chunk.update(document_version_id=layout['document_version_id'],parse_key=layout.get('parse_key'),
            product_version_id=layout.get('metadata',{}).get('product_version_id'),layout_sha256=layout_sha256,
            source_bbox=block.get('bounding_box'),original_reference=block.get('original_reference'),
            publication_status='quarantined',review_status='pending_review',tokenizer=tokenizer_name)
        chunk['block_ids']=[chunk['block_id']]
        chunk['source_spans']=[{'block_id':chunk['block_id'],'source_character_range':chunk['source_character_range'],
                              'chunk_character_range':[0,len(chunk['raw_text'])],
                              'source_bbox':block.get('bounding_box'),'original_reference':block.get('original_reference')}] if chunk['kind']=='text' else []
    packed=[]
    for chunk in chunks:
        previous=packed[-1] if packed else None
        combined=previous['raw_text']+'\n'+chunk['raw_text'] if previous else ''
        if (previous and previous['kind']==chunk['kind']=='text' and
            (previous['page_number'],previous['section_id'],previous['clause_id'])==
            (chunk['page_number'],chunk['section_id'],chunk['clause_id']) and token_counter(combined)<=limit):
            offset=len(previous['raw_text'])+1
            previous['raw_text']=combined;previous['token_count']=token_counter(combined)
            previous['block_ids']+=chunk['block_ids']
            previous['source_spans'] += [{**s,'chunk_character_range':[v+offset for v in s['chunk_character_range']]} for s in chunk['source_spans']]
            previous['source_character_range']=None
            previous['chunk_id']=stable_hash(['packed-chunk-v1',layout['document_version_id'],layout_sha256,previous['source_spans'],tokenizer_name,limit])
        else:packed.append(chunk)
    return {'schema_version':1,'chunks':packed,'tokenizer':tokenizer_name,'budget':limit,
        'token_count_semantics':'UTF-8 byte upper bound' if token_counter and tokenizer_name=='utf8_byte_upper_bound_v1' else 'provided tokenizer count',
        'publication_status':'quarantined'}
