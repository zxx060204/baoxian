"""Keep appended source catalogs out of the preceding insurance clause."""
import re
from insurance_rag.parsing.common import stable_hash
APPENDIX=re.compile(r'^\s*附(?:表|录)\s*[一二三四五六七八九十0-9A-Z]+\s*[:：、.]')
ROW=re.compile(r'^\s*(\d{1,3})\s+\S')
def annotate_appendix_catalogs(layout):
 current=None;list_id=None;last=None;last_number=None
 sections=layout['sections']
 for b in layout['blocks']:
  text=b['raw_text'].strip()
  if APPENDIX.match(text):
   sid=stable_hash(['appendix-source-v1',layout['document_version_id'],b['block_id']]);current={'section_id':sid,'clause_id':stable_hash([sid,'appendix']),'heading':text,'section_path':[text],'review_status':'source_appendix_boundary_candidate'};sections.append(current);b['block_type']='heading';list_id=last=last_number=None
  if current:
   b.update(parent_section_id=current['section_id'],clause_id=current['clause_id'])
   if '序号' in text and any(w in text for w in ('药品','商品名','通用名','厂商')):list_id=stable_hash([b['block_id'],'source_table_rows']);last=last_number=None
   match=ROW.match(text) if list_id else None
   if match:
    number=int(match[1])
    if last_number is not None and number<=last_number:list_id=stable_hash([b['block_id'],'source_table_rows'])
    last_number=number;last=stable_hash([list_id,number])
   if list_id and last:b.update(list_id=list_id,list_item_id=last,list_item_number=last_number,semantic_role='numbered_catalog')
 return layout
