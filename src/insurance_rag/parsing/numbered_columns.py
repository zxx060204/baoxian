"""Conservative numbered-column detection; no hardcoded insurer/page coordinates."""
import re
from statistics import median
MARKER=re.compile(r'^(\d{1,3})[.．、](?!\d)')

def numbered_columns(words,width):
 anchors=[]
 for w in words:
  m=MARKER.match(w['text'])
  if m and int(m.group(1))>0:anchors.append((w,int(m.group(1))))
 clusters=[]
 for w,n in sorted(anchors,key=lambda pair:pair[0]['x0']):
  if not clusters or abs(w['x0']-median(a[0]['x0'] for a in clusters[-1]))>5:clusters.append([])
  clusters[-1].append((w,n))
 good=[]
 for cluster in clusters:
  cluster.sort(key=lambda pair:pair[0]['top']);nums=[n for w,n in cluster]
  if len(nums)>=4 and len(set(nums))==len(nums) and sum(b==a+1 for a,b in zip(nums,nums[1:]))>=.8*(len(nums)-1):good.append(cluster)
 if not 2<=len(good)<=4:return None
 flat=[n for col in good for w,n in col]
 if len(flat)!=len(set(flat)):return None
 starts=[median(w['x0'] for w,n in col) for col in good]
 if any(b-a<width*.15 for a,b in zip(starts,starts[1:])):return None
 height=median(w['bottom']-w['top'] for col in good for w,n in col)
 top=min(w['top'] for col in good for w,n in col)
 if max(col[0][0]['top'] for col in good)-top>height*4:return None
 bottom=max(w['bottom'] for col in good for w,n in col)+height*3
 return {'starts':starts,'top':top,'bottom':bottom,'marker_counts':[len(c) for c in good]}

def ordered_numbered_words(words,width):
 region=numbered_columns(words,width)
 if not region:return None
 starts=region['starts'];columns=[[] for _ in starts];before=[];after=[]
 for word in words:
  if word['top']<region['top']-2:before.append(word);continue
  if word['top']>region['bottom']:after.append(word);continue
  if word['x0']<starts[0]-6:before.append(word);continue
  col=max(i for i,x in enumerate(starts) if word['x0']>=x-2)
  tagged={**word,'numbered_column':col,'numbered_region':region};columns[col].append(tagged)
 return before,columns,after

def annotate_catalog_blocks(blocks,document_key):
 from .common import stable_hash
 regions={}
 for b in blocks:
  ref=b.get('original_reference',{})
  if 'numbered_column' in ref:regions.setdefault(b['page_number'],[]).append(b)
 previous=None
 for page,rows in sorted(regions.items()):
  columns={}
  for b in rows:columns.setdefault(b['original_reference']['numbered_column'],[]).append(b)
  first={}
  for col,items in columns.items():
   values=[int(m.group(1)) for b in items if (m:=MARKER.match(b['raw_text'].strip()))]
   if values:first[col]=values[0]
  continuation=previous and page==previous['page']+1 and set(first)==set(previous['last']) and all(first[c]==previous['last'][c]+1 for c in first)
  list_id=previous['list_id'] if continuation else stable_hash(['numbered-catalog-v1',document_key,page])
  last={}
  for col,items in columns.items():
   number=previous['last'].get(col) if continuation else None
   for b in items:
    m=MARKER.match(b['raw_text'].strip())
    if m:number=int(m.group(1))
    if number is None:continue
    b.update(list_id=list_id,list_item_number=number,list_item_id=stable_hash([list_id,number]),semantic_role='numbered_catalog')
   if number is not None:last[col]=number
  previous={'page':page,'last':last,'list_id':list_id}
