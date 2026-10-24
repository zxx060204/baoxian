"""Clause-aware, sentence-preserving chunks with lossless PDF source spans."""
from dataclasses import dataclass
import re
from insurance_rag.parsing.common import stable_hash
from .schema import Chunk

@dataclass(frozen=True)
class ChunkPolicy:
    target_tokens:int=400
    max_body_tokens:int=500
    max_input_tokens:int=8192
    summary_tokens:int=100
    context_tokens:int=100
    def __post_init__(self):
        values=(self.target_tokens,self.max_body_tokens,self.max_input_tokens,self.summary_tokens,self.context_tokens)
        if any(isinstance(v,bool) or not isinstance(v,int) or v<1 for v in values):raise ValueError('invalid_chunk_policy')
        if self.target_tokens>self.max_body_tokens:raise ValueError('target_exceeds_body_limit')

@dataclass
class ParsedDocument:
    document_version_id:str
    document_id:str
    metadata:dict
    blocks:list
    sections:list
    tables:list
    @classmethod
    def from_layout(cls,layout):
        blocks=layout['blocks']
        if len({b['block_id'] for b in blocks})!=len(blocks):raise ValueError('duplicate_source_block_id')
        return cls(layout['document_version_id'],layout.get('document_id',layout['document_version_id']),
                   layout['metadata'],blocks,layout.get('sections',[]),layout.get('tables',[]))

TYPES={'terms':'保险条款','brochure':'产品说明书','premium_rate':'费率表','cash_value':'现金价值表','other':'其他资料'}
HEADING=re.compile(r'^第[一二三四五六七八九十百零〇\d]+[章节条]\s*[^。！？!?]*$')

def build_content(*,product_name,document_type,section_path,clause_title,summary,context,body):
    # Identifiers and page numbers are deliberately absent from semantic content.
    parts=['产品名称：\n'+product_name,'文档类型：\n'+TYPES.get(document_type,document_type)]
    if section_path:parts.append('章节：\n'+'/'.join(section_path))
    if clause_title:parts.append('条款标题：\n'+clause_title)
    if summary:parts.append('文档摘要：\n'+summary)
    if context:parts.append('上下文：\n'+context)
    parts.append('正文：\n'+body)
    return '\n\n'.join(parts)

def _groups(document):
    groups=[];list_groups={};barrier=True;fallback=None;previous_page=None
    sections={s['section_id']:s for s in document.sections}
    from insurance_rag.retrieval.prose import prose_table_blocks
    recovered={b['block_id']:b for b in prose_table_blocks({'metadata':document.metadata,'blocks':document.blocks,'tables':document.tables})}
    for original in sorted(document.blocks,key=lambda b:(b['page_number'],b.get('reading_order') is None,b.get('reading_order') or 0)):
        b=recovered.get(original['block_id'],original)
        if previous_page is not None and b['page_number']>previous_page+1:barrier=True;fallback=None
        previous_page=b['page_number']
        if b['block_type'] in ('image','table'):
            barrier=True;continue
        if not b['raw_text']:continue
        if b.get('list_item_id'):
            item=b['list_item_id']
            if item not in list_groups:
                group={'key':(b.get('parent_section_id'),b.get('clause_id'),'numbered_catalog',None),'blocks':[],'section':{},'candidate_boundary':False,'list_id':b['list_id'],'list_item_id':item,'list_item_number':b['list_item_number']}
                groups.append(group);list_groups[item]=group
            list_groups[item]['blocks'].append(b);barrier=True;fallback=None
            continue
        sid=b.get('parent_section_id');cid=b.get('clause_id');role=b.get('semantic_role','body')
        heading=b['raw_text'].strip()
        if role=='table_of_contents':fallback=None
        if cid is None and role!='table_of_contents' and (b['block_type']=='heading' or HEADING.fullmatch(heading)):
            fallback={'section_id':sid or stable_hash(['candidate-section-v2',document.document_version_id,b['block_id']]),
                      'clause_id':stable_hash(['candidate-clause-v2',document.document_version_id,b['block_id']]),
                      'heading':heading,'section_path':sections.get(sid,{}).get('section_path',[heading])}
            barrier=True
        if cid is None and fallback and role!='table_of_contents':
            sid=fallback['section_id'];cid=fallback['clause_id']
        elif cid is not None:fallback=None
        # Unknown regions are isolated by contiguous page, never treated as verified clauses.
        key=(sid,cid,role,b['page_number'] if sid is None and cid is None else None)
        if barrier or not groups or groups[-1]['key']!=key:
            section=sections.get(sid,{})
            groups.append({'key':key,'blocks':[],'section':section or fallback or {},
                           'candidate_boundary':bool(fallback)})
        groups[-1]['blocks'].append(b);barrier=False
    return groups

def _join(blocks):
    text='';spans=[]
    for b in blocks:
        # Native text-line blocks are not independent paragraphs. Line breaks are
        # preserved in text but never used as sentence boundaries.
        if text:
            logical_paragraph=(b['block_type']=='paragraph' and b.get('original_reference',{}).get('extraction_scope')!='native_text_line')
            text+='\n\n' if logical_paragraph else '\n'
        start=len(text);text+=b['raw_text']
        spans.append({'block_id':b['block_id'],'start':start,'end':len(text),
            'page_number':b['page_number'],'source_bbox':b.get('bounding_box'),
            'original_reference':b.get('original_reference')})
    return text,spans

def _sentence_ranges(text):
    # Decimal numbers, clause numbers, abbreviations, semicolons and line wraps
    # are not treated as full stops. Chinese closing quotes stay with the sentence.
    cuts=[m.end() for m in re.finditer(r'[。！？!?]+[”’」』）)]*',text)]
    start=0;ranges=[]
    for end in cuts:
        ranges.append((start,end));start=end
    if start<len(text):ranges.append((start,len(text)))
    return ranges

def _split_clause(text,count,policy):
    if count(text)<=policy.max_body_tokens:return [(0,len(text))]
    # Prefer logical paragraphs; a PDF physical line is not a paragraph.
    boundaries=[m.end() for m in re.finditer(r'(?<=[。！？!?])\n\n+',text)]
    paragraphs=[];cursor=0
    for stop in boundaries:paragraphs.append((cursor,stop));cursor=stop
    if cursor<len(text):paragraphs.append((cursor,len(text)))
    units=[]
    for a,b in paragraphs:
        if count(text[a:b])<=policy.max_body_tokens:units.append((a,b))
        else:units.extend((a+x,a+y) for x,y in _sentence_ranges(text[a:b]))
    out=[];start=None;end=None
    for a,b in units:
        if start is not None and count(text[start:b])>policy.max_body_tokens:
            out.append((start,end));start=None
        if start is None:start=a
        end=b
        if count(text[start:end])>=policy.target_tokens:
            out.append((start,end));start=end=None
    if start is not None:out.append((start,end))
    return out

def _project(spans,start,end):
    out=[]
    for s in spans:
        a=max(start,s['start']);b=min(end,s['end'])
        if a>=b:continue
        out.append({'block_id':s['block_id'],'source_character_range':[a-s['start'],b-s['start']],
            'chunk_character_range':[a-start,b-start],'page_number':s['page_number'],
            'source_bbox':s['source_bbox'],'original_reference':s['original_reference']})
    return out

def build_clause_chunks(document,layout_sha256,*,counter,policy=None,summary='',contexts=None) -> list[Chunk]:
    """ParsedDocument -> List[Chunk dict]. No hard token/character slicing."""
    if isinstance(document,dict):document=ParsedDocument.from_layout(document)
    policy=policy or ChunkPolicy();contexts=contexts or {}
    if counter(summary)>policy.summary_tokens:raise ValueError('document_summary_exceeds_limit')
    meta=document.metadata;chunks=[]
    for group in _groups(document):
        text,spans=_join(group['blocks']);sid,cid,role,_=group['key'];section=group['section']
        path=section.get('section_path',[]);title=section.get('heading','') if cid else ''
        context=contexts.get(cid,'')
        # Without an audited semantic context, a title-based locator adds no new facts.
        if not context and title:
            locator='本片段属于“'+title+'”。'
            context=locator if counter(locator)<=policy.context_tokens else ''
        if counter(context)>policy.context_tokens:raise ValueError('chunk_context_exceeds_limit')
        ranges=_split_clause(text,counter,policy)
        for part,(a,b) in enumerate(ranges):
            body=text[a:b]
            if not body.strip():continue
            sources=_project(spans,a,b);pages=sorted({s['page_number'] for s in sources})
            reasons=[]
            if any(b.get('recovery_reason') for b in group['blocks']):reasons.append('prose_table_recovery_requires_review')
            if counter(body)>policy.max_body_tokens:reasons.append('oversized_sentence_preserved')
            if sid is None or cid is None:reasons.append('clause_boundary_unverified')
            if group['candidate_boundary']:reasons.append('candidate_clause_boundary_requires_review')
            if role=='table_of_contents':reasons.append('table_of_contents_not_clause_evidence')
            content=build_content(product_name=meta.get('product_name',''),document_type=meta['document_type'],
                section_path=path,clause_title=title,summary=summary,context=context,body=body)
            if counter(content)>policy.max_input_tokens:reasons.append('embedding_input_exceeds_limit')
            rid=stable_hash(['clause-chunk-v2',document.document_version_id,layout_sha256,sid,cid,
                             sources,counter.name,policy.__dict__,stable_hash(content)])
            chunks.append({'schema_version':2,'record_id':rid,'chunk_id':rid,'kind':'text','scope':'chunk',
                'document_id':document.document_id,'document_version_id':document.document_version_id,
                'product_id':meta.get('product_id'),'product_version_id':meta.get('product_version_id'),'product_name':meta.get('product_name',''),
                'section_id':sid,'clause_id':cid,'section_path':path,'clause_title':title,'clause_chunk_index':part,
                'raw_text':body,'content':content,'embedding_text':content,'bm25_text':content,
                'document_summary':summary,'chunk_context':context,'body_token_count':counter(body),
                'embedding_token_count':counter(content),'token_count':counter(body),'tokenizer':counter.name,
                'page_start':pages[0],'page_end':pages[-1],'page_numbers':pages,'page_number':pages[0],
                'source_spans':sources,'block_ids':list(dict.fromkeys(s['block_id'] for s in sources)),
                'source_character_range':None,'source_bbox':None,'original_reference':None,
                'layout_sha256':layout_sha256,'review_reasons':reasons,'semantic_role':role,
                'publication_status':'quarantined','review_status':'pending_review'})
            if group.get('list_item_id'):chunks[-1].update({k:group[k] for k in ('list_id','list_item_id','list_item_number')})
    for i,c in enumerate(chunks):
        c.update(chunk_index=i,prev_chunk_id=chunks[i-1]['chunk_id'] if i else None,
                 next_chunk_id=chunks[i+1]['chunk_id'] if i+1<len(chunks) else None)
    return chunks
