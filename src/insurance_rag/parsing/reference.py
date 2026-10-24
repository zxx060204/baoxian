"""Independent-engine auto-reference diagnostics, separate from human gold."""
from collections import Counter
import ctypes
import re
import pypdfium2
from pypdf import PdfReader
import pypdfium2.raw as pdfium
import pdfplumber

NUMBERS=re.compile(r'[-+]?\d+(?:[,.]\d+)*(?:[%％])?')


def characters(text, ignored=()):
    return Counter(c for c in text if not c.isspace() and c not in ignored)


def same_numeric_run(left, right, font_size):
    """Only tight horizontal digit neighbours; never join columns or rows."""
    width=max(left[2]-left[0],right[2]-right[0],font_size*.5)
    height=max(left[3]-left[1],right[3]-right[1])
    gap=right[0]-left[2]
    return (width>0 and height>0 and -width*.2<=gap<=width*.45
            and abs(left[1]-right[1])<=height*.35)


def choose_numeric_reference(raw, geometric, independent):
    """Apply a geometric candidate only when a second source engine agrees."""
    a=Counter(NUMBERS.findall(raw));b=Counter(NUMBERS.findall(geometric));c=Counter(NUMBERS.findall(independent))
    return b if a!=c and b==c else a


def symbol_space_evidence(chars):
    """PDFium can omit symbol-font blanks; retain original font/glyph evidence."""
    symbols=[c for c in chars if c.get('text')=='\uf020']
    evidence=[{'codepoint':'U+F020','font':c.get('fontname'),
               'bbox_pdf_top_left':[c['x0'],c['top'],c['x1'],c['bottom']],
               'method':'original_pdf_font_metadata','reason':'symbol_font_space'}
              for c in symbols if 'wingdings' in c.get('fontname','').lower()]
    return evidence if len(evidence)==len(symbols) else []


def source_geometry(textpage):
    """Source-only repairs to PDFium's generated separators, with evidence."""
    chars=[chr(pdfium.FPDFText_GetUnicode(textpage,i)) for i in range(textpage.count_chars())]
    removed=set();joins=[];font_spaces=[];unknown_space_font=False
    for i,c in enumerate(chars):
        if c=='\uf020' or (c==' ' and pdfium.FPDFText_IsGenerated(textpage,i)==0):
            flags=ctypes.c_int()
            size=pdfium.FPDFText_GetFontInfo(textpage,i,None,0,ctypes.byref(flags))
            buffer=ctypes.create_string_buffer(max(size,1))
            pdfium.FPDFText_GetFontInfo(textpage,i,buffer,len(buffer),ctypes.byref(flags))
            font=buffer.value.decode('utf-8',errors='replace')
            if 'wingdings' in font.lower():
                font_spaces.append({'character_index':i,'font':font,'codepoint':f'U+{ord(c):04X}',
                    'reason':'symbol_font_space','bbox_pdf_canvas':list(textpage.get_charbox(i,loose=True))})
            elif c=='\uf020':
                unknown_space_font=True
        if c not in (' ','\u00a0') or i==0 or i+1==len(chars):
            continue
        if not (chars[i-1] in '0123456789' and chars[i+1] in '0123456789'):
            continue
        if pdfium.FPDFText_IsGenerated(textpage,i)!=1:
            continue
        left=textpage.get_charbox(i-1);right=textpage.get_charbox(i+1)
        font_size=max(pdfium.FPDFText_GetFontSize(textpage,i-1),pdfium.FPDFText_GetFontSize(textpage,i+1))
        if same_numeric_run(left,right,font_size):
            removed.add(i)
            joins.append({'character_index':i,'reason':'generated_space_between_contiguous_digits',
                'left_bbox_pdf_canvas':list(left),'right_bbox_pdf_canvas':list(right)})
    return ''.join(c for i,c in enumerate(chars) if i not in removed), {
        'generated_digit_space_joins':joins,'symbol_font_spaces':font_spaces,
        'ignored_coverage_characters':['\uf020'] if font_spaces and not unknown_space_font else [],
        'coordinate_system':'PDF canvas, bottom-left origin','parser_layout_used':False}


def recall(expected,actual):
    total=sum(expected.values())
    return {'expected':total,'matched':sum(min(n,actual[k]) for k,n in expected.items()),
            'missing':dict(expected-actual),'extra':dict(actual-expected),
            'recall':sum(min(n,actual[k]) for k,n in expected.items())/total if total else 1.0}


def build_reference(path,entry,page_limit=2):
    pdf=pypdfium2.PdfDocument(str(path));other=PdfReader(path)
    if other.is_encrypted and not other.decrypt(''):
        pdf.close()
        raise ValueError('password_required_for_reference')
    pages=[]
    # First/last pages exercise a new page and avoid repeatedly testing only page1.
    selected=sorted({0,len(pdf)-1}) if page_limit==2 else list(range(min(page_limit,len(pdf))))
    font_pdf=pdfplumber.open(path)
    try:
        for index in selected:
            page=pdf[index];textpage=page.get_textpage()
            try:
                text=textpage.get_text_range()
                geometry_text,normalization=source_geometry(textpage)
                omitted_symbol_spaces=symbol_space_evidence(font_pdf.pages[index].chars)
                if omitted_symbol_spaces:
                    normalization['symbol_font_spaces'].extend(omitted_symbol_spaces)
                    normalization['ignored_coverage_characters']=['\uf020']
                alternate=other.pages[index].extract_text() or ''
                raw_numbers=Counter(NUMBERS.findall(text))
                geometric_numbers=Counter(NUMBERS.findall(geometry_text));other_numbers=Counter(NUMBERS.findall(alternate))
                # Geometry alone cannot certify a correction: tight neighbouring
                # table columns and formatted phone numbers can also look close.
                # Keep the original reference unless an independent engine agrees.
                numbers=choose_numeric_reference(text,geometry_text,alternate)
                normalization['numeric_candidate_status']='applied_cross_engine_agree' if numbers!=raw_numbers else 'not_applied'
                agreement=recall(numbers,other_numbers)
                pages.append({'page_number':index+1,'reference_text':text,'numeric_tokens':dict(numbers),
                    'raw_numeric_tokens':dict(raw_numbers),'normalization':normalization,
                    'geometry_numeric_tokens':dict(geometric_numbers),
                    'source_engine_agreement':{'pdfium_to_pypdf':agreement,'pypdf_extra':dict(other_numbers-numbers)},
                    'reference_status':'no_native_text' if not text.strip() else 'cross_engine_agree' if numbers==other_numbers else 'source_engines_disagree'})
            finally:
                textpage.close();page.close()
    finally:
        pdf.close();font_pdf.close()
    return {'schema_version':1,'reference_version':'pdfium-geometry-0.1.1','document_version_id':entry['document_version_id'],'file_sha256':entry['file_sha256'],
        'company_id':entry['company_id'],'document_type':entry['document_type'],'source_file':entry['file_path'],
        'original_pages':len(other.pages),'pages':pages,'audit':{'status':'auto_generated','human_confirmed':False},
        'method':'PDFium original text + pypdf numeric multiset cross-check; no parser layout used as reference',
        'scope':'Character and numeric token coverage diagnostics; no certified table structure, reading order or business semantics'}


def evaluate_reference(reference,layout):
    if reference['document_version_id']!=layout['document_version_id']:
        raise ValueError('Reference binding mismatch')
    results=[]
    for page in reference['pages']:
        text='\n'.join(b['raw_text'] for b in layout['blocks'] if b['page_number']==page['page_number'] and b['block_type']!='image')
        ignored=page.get('normalization',{}).get('ignored_coverage_characters',[])
        raw_chars=recall(characters(page['reference_text']),characters(text))
        chars=recall(characters(page['reference_text'],ignored),characters(text,ignored))
        nums=recall(Counter(page['numeric_tokens']),Counter(NUMBERS.findall(text)))
        trustworthy=page['reference_status']=='cross_engine_agree'
        results.append({'page_number':page['page_number'],'character_coverage':chars,'numeric_coverage':nums,
            'raw_character_coverage':raw_chars,
            'raw_numeric_coverage':recall(Counter(page.get('raw_numeric_tokens',page['numeric_tokens'])),Counter(NUMBERS.findall(text))),
            'normalization_applied':bool(ignored or page.get('normalization',{}).get('numeric_candidate_status')=='applied_cross_engine_agree'),
            'status':'needs_source_review' if not trustworthy else 'coverage_difference' if chars['missing'] or nums['missing'] or chars['extra'] or nums['extra'] else 'passed_coverage_checks',
            'table_count':sum(t['page_number']==page['page_number'] for t in layout['tables'])})
    return {'status':'auto_reference_diagnostic','human_gold':False,'pages':results,'publication_status':'quarantined'}
