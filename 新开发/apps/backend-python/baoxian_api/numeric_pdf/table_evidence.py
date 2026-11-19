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

def recover_coarse_grid(table,words,matrix,parse_key,ordinal):
 """Recover a sparse ruled grid whose large cells contain many text rows."""
 if not any(c.get('raw_text','').count('\n')>=3 for c in table.get('cells',[])):return None
 b=table['native_bounding_box'];rows=word_rows([w for w in words if inside(w,b)])
 if len(rows)<4 or len(rows)<table['row_count']*2:return None
 header=next((i for i,row in enumerate(rows) if len(row)>=table['column_count']*2 and sum(bool(NUMERIC.fullmatch(w['text'])) for w in row)>=3),None)
 if header is None:return None
 rows=rows[header:];headings=rows[0]
 if all(NUMERIC.fullmatch(w['text']) for w in headings):
  body=next((i for i,row in enumerate(rows[1:],1) if len(row)>len(headings) and centre(row[0])[0]<centre(headings[0])[0]),None)
  if body is None:return None
  anchor=rows[body][0];top=min(w['top'] for w in headings);height=median(w['bottom']-w['top'] for w in headings)
  labels=[w for w in words if centre(w)[0]<centre(headings[0])[0] and abs(w['top']-top)<=height*2.5 and re.search(r'年度|年龄|[/／\\]',w['text'])]
  if not labels:return None
  labels.sort(key=lambda w:(w['top'],w['x0']))
  axis={'text':''.join(w['text'] for w in labels),'x0':anchor['x0'],'x1':anchor['x1'],'top':top,'bottom':max(w['bottom'] for w in headings)}
  headings=[axis]+headings;rows=[headings]+rows[body:]
 centres=[centre(w)[0] for w in headings]
 if len(rows)<4 or len(headings)<4:return None
 if len(set(centres))!=len(centres):return None
 b=[b[0],min(w['top'] for w in headings)-.01,b[2],b[3]]
 xs=[b[0]]+[(a+c)/2 for a,c in zip(centres,centres[1:])]+[b[2]]
 ys_centres=[sum(centre(w)[1] for w in row)/len(row) for row in rows]
 ys=[b[1]]+[(a+c)/2 for a,c in zip(ys_centres,ys_centres[1:])]+[b[3]]
 cells=[]
 for ri,row in enumerate(rows):
  columns=defaultdict(list)
  for word in row:
   x=centre(word)[0];col=next((i for i in range(len(centres)) if xs[i]<=x<=xs[i+1]),None)
   if col is not None:columns[col].append(word['text'])
  for col,values in columns.items():cells.append({'bbox':[xs[col],ys[ri],xs[col+1],ys[ri+1]],'text':' '.join(values)})
 return make_table(b,cells,table['page_number'],matrix,parse_key,ordinal,'text_aligned_coarse_grid')

def capture(page,tables,matrix,parse_key,rejected=(), *, compact=False):
 """No product knowledge, inherited headings or semantic continuation decisions."""
 xt,yt=text_tolerances(page);words=page.extract_words(x_tolerance=xt,y_tolerance=yt)
 glyphs=GlyphIndex(page.chars);fixed=[]
 for ordinal,t in enumerate(tables):
  recovered=recover_coarse_grid(t,words,matrix,parse_key,ordinal)
  if recovered:t=recovered
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
  contexts=[]
  for ri,row in enumerate(word_rows(near)):
   item={'raw_text':' '.join(w['text'] for w in row),'page_number':page.page_number,'native_bounding_box':[min(w['x0'] for w in row),min(w['top'] for w in row),max(w['x1'] for w in row),max(w['bottom'] for w in row)],'position':'above' if max(w['bottom'] for w in row)<=b[1]+0.01 else 'below' if min(w['top'] for w in row)>=b[3]-0.01 else 'beside'}
   if not compact:
    item.update(context_id=stable_hash([t['table_id'],ri,[w['text'] for w in row]]),role='unclassified',tokens=[{'text':w['text'],'native_bounding_box':[w['x0'],w['top'],w['x1'],w['bottom']]} for w in row])
   contexts.append(item)
  t['context_candidates']=contexts
 return fixed

def partial_report(layout):
 return [{'table_id':t['table_id'],'page_number':t['page_number'],'layout_path':'tables','reason':t.get('fragment_reason','incomplete_physical_grid'),'token_count':len(t.get('tokens',[])),'state':'pending_mapping'} for t in layout.get('tables',[]) if t.get('structure_status')=='fragment']
