"""Group literal context by page geometry only; no business scope or field inference."""
import re
from statistics import median
from insurance_rag.parsing.common import stable_hash

POLICY='literal-table-contexts-v1'


def bbox(item):return item.get('native_bounding_box') or item.get('bounding_box')
def overlap_x(a,b):return max(0,min(a[2],b[2])-max(a[0],b[0]))
def overlap(a,b):return overlap_x(a,b)*max(0,min(a[3],b[3])-max(a[1],b[1]))
def text_key(text):return re.sub(r'\s+','',text)


def document_contexts(layout,model):
 tables=[{'table_id':t['table_id'],'page_number':t['page_number'],'bbox':bbox(next((p for p in layout['tables'] if p['table_id']==t.get('source_table_ref',{}).get('table_id',t['table_id'])),t))} for t in model['tables']]
 lines=[]
 def add(ref,text,page,box):
  if not text.strip():return
  # Multiple extraction paths may describe the same occurrence. Keep every source.
  for n in lines:
   if n['page_number']!=page:continue
   same=ref in n['source_refs'] or box and n['bbox'] and overlap(box,n['bbox'])>=.85*min(max(1,(box[2]-box[0])*(box[3]-box[1])),max(1,(n['bbox'][2]-n['bbox'][0])*(n['bbox'][3]-n['bbox'][1]))) and (text_key(text) in text_key(n['raw_text']) or text_key(n['raw_text']) in text_key(text))
   if same:
    if ref not in n['source_refs']:n['source_refs'].append(ref)
    n['source_fragments'].append({'source_ref':ref,'raw_text':text,'bbox':box})
    if len(text_key(text))>len(text_key(n['raw_text'])):n.update(raw_text=text,bbox=box)
    return
  lines.append({'raw_text':text,'page_number':page,'bbox':box,'source_refs':[ref],'source_fragments':[{'source_ref':ref,'raw_text':text,'bbox':box}]})
 for b in layout.get('blocks',[]):
  if b.get('block_type') in ('table','image'):continue
  box=bbox(b)
  if box and any(t['page_number']==b['page_number'] and t['bbox'] and overlap(box,t['bbox'])>0 for t in tables):continue
  add(b['block_id'],b.get('raw_text',''),b['page_number'],box)
 for t in model['tables']:
  for c in t.get('context_candidates',[]):add(c['context_id'],c['raw_text'],c['page_number'],bbox(c))
 lines.sort(key=lambda n:(n['page_number'],n['bbox'][1] if n['bbox'] else float('inf'),n['bbox'][0] if n['bbox'] else 0,n['source_refs'][0]))
 def neighbors(n):
  if not n['bbox']:return (),()
  a=n['bbox'];near=[t for t in tables if t['bbox'] and overlap_x(a,t['bbox'])>0]
  before=[t for t in near if t['page_number']<n['page_number'] or t['page_number']==n['page_number'] and t['bbox'][3]<=a[1]+.5]
  after=[t for t in near if t['page_number']>n['page_number'] or t['page_number']==n['page_number'] and t['bbox'][1]>=a[3]-.5]
  prev=max(before,key=lambda t:(t['page_number'],t['bbox'][3]),default=None);nxt=min(after,key=lambda t:(t['page_number'],t['bbox'][1]),default=None)
  return (prev['table_id'] if prev else None,nxt['table_id'] if nxt else None)
 groups=[]
 for n in lines:
  a=n['bbox'];selected=None
  if a:
   # Find the last compatible region, rather than following alternating columns.
   for g in reversed(groups):
    b=g['bbox']
    if g['page_number']!=n['page_number'] or not b or g['neighbors']!=neighbors(n):continue
    last=g['members'][-1]['bbox'];height=median([max(1,m['bbox'][3]-m['bbox'][1]) for m in g['members']]+[max(1,a[3]-a[1])])
    if not -.5<=a[1]-last[3]<=height*1.8:continue
    aligned=abs(a[0]-last[0])<=height*1.5 and overlap_x(a,last)>0
    barrier=any(t['page_number']==n['page_number'] and t['bbox'] and overlap_x([min(a[0],b[0]),b[1],max(a[2],b[2]),a[3]],t['bbox'])>0 and t['bbox'][1]<a[1] and t['bbox'][3]>last[3] for t in tables)
    if aligned and not barrier:selected=g;break
  if selected is None:
   groups.append({'page_number':n['page_number'],'bbox':a,'neighbors':neighbors(n),'members':[n]})
  else:
   selected['members'].append(n);b=selected['bbox'];selected['bbox']=[min(a[0],b[0]),min(a[1],b[1]),max(a[2],b[2]),max(a[3],b[3])]
 first=min((t for t in tables if t['bbox']),key=lambda t:(t['page_number'],t['bbox'][1]),default=None)
 contexts=[]
 for g in groups:
  raw='\n'.join(m['raw_text'] for m in g['members']);a=g['bbox'];prev,nxt=g['neighbors'];relations=[]
  opening=bool(first and a and (g['page_number']<first['page_number'] or g['page_number']==first['page_number'] and a[3]<=first['bbox'][1]))
  if prev:relations.append({'table_id':prev,'position':'between_tables' if nxt else 'after'})
  if nxt:relations.append({'table_id':nxt,'position':'between_tables' if prev else 'before'})
  if a:
   for t in tables:
    b=t['bbox']
    if b and t['page_number']==g['page_number'] and min(a[3],b[3])>max(a[1],b[1]) and not overlap_x(a,b) and min(abs(a[0]-b[2]),abs(b[0]-a[2]))<=90:relations.append({'table_id':t['table_id'],'position':'beside'})
  contexts.append({'context_id':stable_hash([POLICY,g['page_number'],g['members']]),'raw_text':raw,'page_number':g['page_number'],'bbox':a,'members':g['members'],'document_position':'opening' if opening else 'unlocated' if a is None else 'body','relations':relations,'association':'spatial_only_not_business_applicability'})
 return contexts
