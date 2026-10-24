import copy,unittest
from insurance_rag.parsing.numbered_columns import numbered_columns,annotate_catalog_blocks
from insurance_rag.parsing.layout import native_lines
from insurance_rag.indexing.chunks import build_clause_chunks
from insurance_rag.indexing.tokenization import ByteCounter

def words():
 return [{'text':str(n)+'.病名','x0':x,'x1':x+50,'top':y,'bottom':y+10} for x,start in ((20,1),(150,6)) for y,n in zip(range(100,200,20),range(start,start+5))]
class NumberedTests(unittest.TestCase):
 def test_disjoint_consecutive_columns_detected(self):
  r=numbered_columns(words(),300);self.assertEqual(r['starts'],[20,150]);self.assertEqual(r['marker_counts'],[5,5])
 def test_decimal_values_not_list_markers(self):
  rows=words()
  for w in rows:w['text']=w['text'].replace('.病名','.35')
  self.assertIsNone(numbered_columns(rows,300))
 def test_repeated_number_sequences_are_not_one_catalog(self):
  rows=words()
  for w in rows:
   if w['x0']==150:w['text']=str(int(w['text'].split('.')[0])-5)+'.项目'
  self.assertIsNone(numbered_columns(rows,300))
 def test_native_lines_preserve_column_order(self):
  class Page:
   width=300;chars=[]
   def extract_words(self,**kwargs):return words()
  lines=native_lines(Page(),[])
  self.assertEqual([int(l['text'].split('.')[0]) for l in lines],list(range(1,11)))
 def test_across_page_continuation_attaches_only_when_all_columns_match(self):
  blocks=[]
  for page,cols in ((1,{0:['1.甲','2.乙'],1:['5.丙','6.丁']}),(2,{0:['3.戊','4.己'],1:['续行','7.庚']})):
   for col,texts in cols.items():
    for text in texts:blocks.append({'block_id':str(len(blocks)),'page_number':page,'raw_text':text,'original_reference':{'numbered_column':col}})
  annotate_catalog_blocks(blocks,'doc');continued=next(b for b in blocks if b['raw_text']=='续行')
  self.assertEqual(continued['list_item_number'],6)
  prior=next(b for b in blocks if b['raw_text']=='6.丁');self.assertEqual(prior['list_item_id'],continued['list_item_id'])
 def test_list_items_are_separate_and_source_ranges_preserved(self):
  blocks=[]
  for i,(number,text) in enumerate(((1,'1.病名——须手术'),(2,'2.另一个病名'),(1,'续行限制'))):
   blocks.append({'block_id':str(i),'block_type':'paragraph','page_number':1 if i<2 else 2,'reading_order':i,'raw_text':text,'bounding_box':[0,0,10,10],'list_id':'list','list_item_id':str(number),'list_item_number':number,'semantic_role':'numbered_catalog','original_reference':{'extraction_scope':'native_text_line'}})
  doc={'document_version_id':'doc','metadata':{'document_type':'terms'},'blocks':blocks}
  chunks=build_clause_chunks(doc,'hash',counter=ByteCounter());self.assertEqual(len(chunks),2)
  first=next(c for c in chunks if c['list_item_number']==1);self.assertEqual(first['raw_text'],'1.病名——须手术\n续行限制');self.assertEqual(first['page_numbers'],[1,2]);self.assertEqual(first['source_spans'][1]['source_character_range'],[0,4])
