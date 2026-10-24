import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from test_generic_source_mapping import layout,table,cell
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.ingestion.source_tables import prepare_tables,validate_tables
from insurance_rag.ingestion.source_package import reference
from insurance_rag.ingestion.table_contexts import document_contexts
from insurance_rag.parsing.common import write_json,file_hash


def text(ref,raw,box,page=1):return {'block_id':ref,'block_type':'paragraph','raw_text':raw,'page_number':page,'native_bounding_box':box,'reading_order':0}
def region(tid,box,page=1):return {'table_id':tid,'page_number':page,'native_bounding_box':box,'context_candidates':[]}


class SpatialContextTests(unittest.TestCase):
 def test_adjacent_lines_join_without_semantic_or_model_decisions(self):
  t=region('t',[0,100,200,200]);blocks=[text('a','说明的第一行',[0,210,180,220]),text('b','后续行原样保留：',[0,222,170,232]),text('c','（1）公式也只保留原文',[0,234,180,244])]
  result=document_contexts({'tables':[t],'blocks':blocks},{'tables':[t]})
  self.assertEqual(len(result),1);self.assertEqual(result[0]['raw_text'],'说明的第一行\n后续行原样保留：\n（1）公式也只保留原文');self.assertEqual(len(result[0]['members']),3)
 def test_two_columns_do_not_merge(self):
  t=region('t',[0,100,250,200]);blocks=[text('a','左栏一',[0,10,90,20]),text('b','右栏一',[140,10,240,20]),text('c','左栏二',[0,22,90,32]),text('d','右栏二',[140,22,240,32])]
  result=document_contexts({'tables':[t],'blocks':blocks},{'tables':[t]});self.assertEqual([c['raw_text'] for c in result],['左栏一\n左栏二','右栏一\n右栏二'])
 def test_between_tables_is_shared_spatial_evidence_not_business_scope(self):
  a=region('a',[0,100,200,200]);b=region('b',[0,250,200,350]);result=document_contexts({'tables':[a,b],'blocks':[text('c','不判定适用范围',[0,210,180,220])]},{'tables':[a,b]})
  self.assertEqual(result[0]['relations'],[{'table_id':'a','position':'between_tables'},{'table_id':'b','position':'between_tables'}]);self.assertNotIn('scope',result[0])
 def test_table_boundary_and_pages_prevent_merging(self):
  t=region('t',[0,30,200,40]);blocks=[text('a','表前',[0,10,180,20]),text('b','表后',[0,42,180,52]),text('c','下一页',[0,10,180,20],2)]
  self.assertEqual(len(document_contexts({'tables':[t],'blocks':blocks},{'tables':[t]})),3)
 def test_duplicate_extraction_preserves_both_source_fragments(self):
  t=region('t',[0,100,200,200]);t['context_candidates']=[{'context_id':'b','raw_text':'相同 原文','page_number':1,'native_bounding_box':[0,10,100,20]}]
  result=document_contexts({'tables':[t],'blocks':[text('a','相同 原文',[0,10,100,20])]},{'tables':[t]})
  self.assertEqual(len(result),1);self.assertEqual(result[0]['raw_text'],'相同 原文');self.assertEqual(result[0]['members'][0]['source_refs'],['a','b'])


class LiteralPreparationTests(unittest.TestCase):
 def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.app=SimpleNamespace(root=self.root)
 def tearDown(self):self.tmp.cleanup()
 def package(self,label='被保人年龄',value='0'):
  t=table([cell(0,0,label),cell(0,1,'原表数值'),cell(1,0,'35'),cell(1,1,value),cell(2,0,'36'),cell(2,1,'')],3,2);t['native_bounding_box']=[0,100,100,160]
  t['context_candidates']=[{'context_id':'a','raw_text':'单位和业务含义不作判断','page_number':1,'native_bounding_box':[0,80,100,90],'position':'above'}]
  data=layout([t]);(self.root/'source.pdf').write_bytes(b'%PDF-1.7 fixture');write_json(self.root/'layout.json',data)
  doc={'document_type':'cash_value','document_version_id':'d','source_file':'source.pdf','source_sha256':file_hash(self.root/'source.pdf'),'layout_file':'layout.json','layout_sha256':file_hash(self.root/'layout.json')}
  return prepare_mapping(self.app,None,{'identity':{'product_id':'p','product_version_id':'v'},'documents':[doc]},self.root)['package_ref']
 def test_raw_headers_blanks_zero_and_source_mapping_survive_without_model(self):
  ref=self.package();before=(self.root/'layout.json').read_bytes()
  with patch('insurance_rag.agent.models.DeepSeekChat.structured',side_effect=AssertionError('model forbidden')):
   prepared=prepare_tables(self.root,ref);_,_,docs,tables,cells=validate_tables(self.root,ref,prepared['result_ref'])
  self.assertEqual(prepared['record_count'],2);self.assertEqual(prepared['cell_count'],6);self.assertEqual(prepared['model_calls'],0);self.assertEqual(tables[0]['header_cells'][0]['raw_text'],'被保人年龄');self.assertEqual(cells[-1]['raw_text'],'');self.assertEqual(cells[-3]['raw_text'],'0');self.assertNotIn('dimensions',cells[-3]);self.assertEqual(before,(self.root/'layout.json').read_bytes())
 def test_different_age_labels_are_never_canonicalized(self):
  for label in ('被保人年龄','投保年龄','年度年龄'):
   ref=self.package(label);r=prepare_tables(self.root,ref);_,_,_,tables,_=validate_tables(self.root,ref,r['result_ref']);self.assertEqual(tables[0]['header_cells'][0]['raw_text'],label)
 def test_rehashed_cell_tampering_is_rejected(self):
  ref=self.package();r=prepare_tables(self.root,ref);path=self.root/r['cells_ref']['path'];rows=[json.loads(x) for x in path.read_text('utf8').splitlines()];rows[-3]['raw_text']='999';path.write_text('\n'.join(json.dumps(c) for c in rows)+'\n',encoding='utf8');r.pop('result_ref');r['cells_ref']=reference(self.root,path);write_json(self.root/'forged.json',r)
  with self.assertRaisesRegex(ValueError,'literal_table_cell_changed'):validate_tables(self.root,ref,reference(self.root,self.root/'forged.json'))
 def test_prepared_result_cannot_cross_source_revision(self):
  ref=self.package();r=prepare_tables(self.root,ref);other=self.package(value='1')
  with self.assertRaisesRegex(ValueError,'literal_table_source_mismatch'):validate_tables(self.root,other,r['result_ref'])


if __name__=='__main__':unittest.main()
