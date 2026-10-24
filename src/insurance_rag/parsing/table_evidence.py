"""Physical evidence only: retain grid gaps, numeric fragments and nearby words."""
from collections import defaultdict
from statistics import median
import re
from pdfplumber.utils import extract_text
from .common import stable_hash
from .preflight import transform_bbox
from .tables import GlyphIndex,make_table,NUMERIC
from .text_geometry import text_tolerances

POLICY='physical-table-evidence-v1'
def centre(w):return ((w['x0']+w['x1'])/2,(w['top']+w['bottom'])/2)
def inside(w,b):
 x,y=centre(w);return b[0]<=x<b[2] and b[1]<=y<b[3]
def word_rows(words):
 if not words:return []
 tol=max(.7,min(3,median(w['bottom']-w['top'] for w in words)*.4));rows=[]
 for w in sorted(words,key=lambda w:(w['top'],w['x0'])):
  if not rows or abs(w['top']-rows[-1][0]['top'])>tol:rows.append([w])
  else:rows[-1].append(w)
 return [sorted(r,key=lambda w:w['x0']) for r in rows]
def capture(page,tables,matrix,parse_key,rejected=()):
 """No product knowledge, inherited headings or semantic continuation decisions."""
 xt,yt=text_tolerances(page);words=page.extract_words(x_tolerance=xt,y_tolerance=yt)
 glyphs=GlyphIndex(page.chars);fixed=[]
 for ordinal,t in enumerate(tables):
  cells=t['cells'];xs=sorted(set(round(v,3) for c in cells for v in (c['native_bounding_box'][0],c['native_bounding_box'][2])));ys=sorted(set(round(v,3) for c in cells for v in (c['native_bounding_box'][1],c['native_bounding_box'][3])))
  occupied={(r,k) for c in cells for r in range(c['row_index'],c['row_index']+c.get('row_span',1)) for k in range(c['column_index'],c['column_index']+c.get('column_span',1))};extra=[]
  if len(xs)==t['column_count']+1 and len(ys)==t['row_count']+1:
   for r in range(t['row_count']):
    for k in range(t['column_count']):
     if (r,k) in occupied:continue
     b=[xs[k],ys[r],xs[k+1],ys[r+1]];text=extract_text(glyphs.within(b),x_tolerance=xt,y_tolerance=yt) or ''
     if text.strip():extra.append({'bbox':b,'text':text})
  if extra:
   original=[{'bbox':c['native_bounding_box'],'text':c['raw_text']} for c in cells]
   new=make_table(t['native_bounding_box'],original+extra,page.page_number,matrix,parse_key,ordinal,t['extraction_method'])
   new['physical_recovery']={'method':'existing_grid_gap_with_source_glyphs','cells_added':len(extra)};t=new
  fixed.append(t)
 outside=[w for w in words if not any(inside(w,t['native_bounding_box']) for t in fixed)]
 rows=word_rows(outside);groups=[]
 for row in rows:
  numeric=sum(bool(NUMERIC.fullmatch(w['text'])) for w in row)
  # Preserve small/ragged candidates; do not promote them to a complete table.
  if numeric<2 or numeric/len(row)<.75:continue
  if not groups or row[0]['top']-max(w['bottom'] for w in groups[-1][-1])>max(8,median(w['bottom']-w['top'] for w in row)*2.5):groups.append([row])
  else:groups[-1].append(row)
 for group in groups:
  if len(group)==1 and len(group[0])<3:continue
  tokens=[{'text':w['text'],'native_bounding_box':[w['x0'],w['top'],w['x1'],w['bottom']],'row_index':ri} for ri,row in enumerate(group) for w in row]
  box=[min(w['x0'] for row in group for w in row),min(w['top'] for row in group for w in row),max(w['x1'] for row in group for w in row),max(w['bottom'] for row in group for w in row)]
  tid=stable_hash([POLICY,parse_key,page.page_number,tokens])
  fixed.append({'table_id':tid,'page_number':page.page_number,'bounding_box':transform_bbox(box,matrix),'native_bounding_box':box,'row_count':len(group),'column_count':max(map(len,group)),'cells':[],'tokens':tokens,'structure_status':'fragment','fragment_reason':'uncovered_aligned_numeric_region','extraction_method':POLICY,'review_status':'needs_structure_mapping','review_reasons':['partial_table_fragment','column_grid_unverified'],'header_rows_candidate':[],'continuation_of':None,'raw_text':'\n'.join(' '.join(w['text'] for w in row) for row in group)})
 for candidate in rejected:
  if candidate.get('reason')!='undersized_table_candidate':continue
  b=candidate['native_bounding_box'];local=[w for w in words if inside(w,b)]
  if not local or any(all(inside(w,t['native_bounding_box']) for w in local) for t in fixed):continue
  rs=word_rows(local);tokens=[{'text':w['text'],'native_bounding_box':[w['x0'],w['top'],w['x1'],w['bottom']],'row_index':ri} for ri,row in enumerate(rs) for w in row];tid=stable_hash([POLICY,parse_key,page.page_number,tokens])
  fixed.append({'table_id':tid,'page_number':page.page_number,'bounding_box':transform_bbox(b,matrix),'native_bounding_box':b,'row_count':len(rs),'column_count':max(map(len,rs)),'cells':[],'tokens':tokens,'structure_status':'fragment','fragment_reason':'undersized_ruled_table','extraction_method':POLICY,'review_status':'needs_structure_mapping','review_reasons':['partial_table_fragment'],'header_rows_candidate':[],'continuation_of':None,'raw_text':'\n'.join(' '.join(w['text'] for w in row) for row in rs)})
 for t in fixed:
  b=t['native_bounding_box'];near=[]
  for w in words:
   x,y=centre(w)
   if b[0]-20<=x<=b[2]+20 and b[1]-90<=y<=b[3]+70 and not inside(w,b) and not any(inside(w,other['native_bounding_box']) for other in fixed if other is not t):near.append(w)
  t['context_candidates']=[{'context_id':stable_hash([t['table_id'],ri,[w['text'] for w in row]]),'raw_text':' '.join(w['text'] for w in row),'page_number':page.page_number,'native_bounding_box':[min(w['x0'] for w in row),min(w['top'] for w in row),max(w['x1'] for w in row),max(w['bottom'] for w in row)],'position':'above' if max(w['bottom'] for w in row)<=b[1] else 'below' if min(w['top'] for w in row)>=b[3] else 'beside','role':'unclassified'} for ri,row in enumerate(word_rows(near))]
 return fixed

def partial_report(layout):
 return [{'table_id':t['table_id'],'page_number':t['page_number'],'layout_path':'tables','reason':t.get('fragment_reason','incomplete_physical_grid'),'token_count':len(t.get('tokens',[])),'state':'pending_mapping'} for t in layout.get('tables',[]) if t.get('structure_status')=='fragment']
