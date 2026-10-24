"""Idempotent table enrichment for current and historical text-only layouts."""
from copy import deepcopy
import pdfplumber
from pypdf import PdfReader
from .common import stable_hash
from .preflight import inspect_page
from .models import ParseConfig
from .tables import extract_native_tables
import re

POLICY='source-table-structure-v5'


def enrich(layout, source, source_sha256, document_type):
    if layout.get('table_preparation_policy')==POLICY:
        return deepcopy(layout),False
    result=deepcopy(layout);reader=PdfReader(source);tables=[]
    if len(reader.pages)!=len(result['pages']):raise ValueError('table_source_page_count_changed')
    with pdfplumber.open(source) as pdf:
        for index,page in enumerate(pdf.pages):
            info=inspect_page(page,reader.pages[index],ParseConfig());rejected=[]
            found=extract_native_tables(page,info['native_to_standard'],stable_hash([POLICY,source_sha256]),
                                        allow_borderless=document_type in ('terms','brochure','premium_rate','cash_value'),rejected=rejected)
            from .table_evidence import capture
            found=capture(page,found,info['native_to_standard'],stable_hash([POLICY,source_sha256]),rejected)
            # Preserve identifiers for an unchanged, already source-bound grid.
            old=[t for t in result.get('tables',[]) if t['page_number']==index+1]
            for table in found:
                same=next((t for t in old if signature(t)==signature(table)),None)
                if same:
                    table['table_id']=same['table_id']
                    for cell,prior in zip(table['cells'],same['cells']):cell['cell_id']=prior['cell_id']
                tables.append(table)
            if rejected:result['pages'][index]['table_detection_rejections']=rejected
            page.close()
    unique={signature(t):t for t in tables}
    result['tables']=list(unique.values());result['table_preparation_policy']=POLICY
    # Logical continuation and header inheritance belong to the mapper.
    from .table_evidence import partial_report
    result['partial_tables']=partial_report(result)
    # Existing indexed text/spans stay valid: enrichment never rewrites them.
    if result.get('blocks')!=layout.get('blocks') or [p.get('raw_text') for p in result['pages']]!=[p.get('raw_text') for p in layout['pages']]:
        raise ValueError('table_enrichment_changed_indexed_text')
    return result,True


def signature(table):
    return stable_hash([table['page_number'],table.get('tokens',[]),[(c['row_index'],c['column_index'],c.get('row_span',1),c.get('column_span',1),
                       c['raw_text'],[round(x,1) for x in c.get('native_bounding_box',[])]) for c in table['cells']]])


def header_boundary(table):
    cells=table.get('cells',[])
    first=''.join(re.sub(r'\s','',c.get('raw_text','')) for c in cells if c['row_index']==0)
    if '保险金给付公式' in first and '保障内容' in first:
        return 1,'benefit_formula'
    numeric=[c['row_index'] for c in cells if c.get('column_index')==0 and re.fullmatch(r'[\d,]+(?:\.\d+)?',c.get('raw_text','').strip())]
    start=min(numeric) if numeric else None
    return start,'annual' if table.get('inherited_headers') or start is not None and any(re.search('保单年度|保单年数',re.sub(r'\s','',c.get('raw_text',''))) for c in cells if c['row_index']<start) else 'other'


def link_continuations(tables):
    previous=None
    for table in sorted(tables,key=lambda t:(t['page_number'],t.get('native_bounding_box',[0,0])[1])):
        start,kind=header_boundary(table)
        years=[int(c['raw_text'].strip()) for c in table['cells'] if c['column_index']==0 and re.fullmatch(r'\d{1,3}',c['raw_text'].strip())]
        if previous and start==0 and years and table['page_number']==previous['page_number']+1 and table['column_count']==previous['column_count']:
            old_years=[int(c['raw_text'].strip()) for c in previous['cells'] if c['column_index']==0 and re.fullmatch(r'\d{1,3}',c['raw_text'].strip())]
            a=previous.get('native_bounding_box');b=table.get('native_bounding_box')
            if old_years and min(years)>max(old_years) and a and b and abs(a[0]-b[0])<2 and abs(a[2]-b[2])<2:
                boundary,_=header_boundary(previous)
                table['continuation_of']=previous['table_id'];table['inherited_headers']=deepcopy(previous.get('inherited_headers') or [c for c in previous['cells'] if c['row_index']<boundary])
                table['inherited_header_page']=previous.get('inherited_header_page',previous['page_number']);kind='annual'
        if kind=='annual':previous=table
