import unittest,tempfile,copy
from pathlib import Path
from types import SimpleNamespace
from insurance_rag.parsing.common import file_hash,write_json
from insurance_rag.agent.store import Conflict
from insurance_rag.illustration.schema import standard_metrics

class SourceExampleTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name);self.app=SimpleNamespace(root=self.root);self.pdf=self.root/'s.pdf';self.pdf.write_bytes(b'%PDF-source-fixture');self.layout=self.root/'layout.json'
  self.headers=['保单年度','年度末年龄','年度有效保额','特别津贴','红利 / 非保证利益演示']
  cells=[]
  for row,values in enumerate([self.headers,['1','31','100.5','—','2'],['3','33','123','50','5']]):
   for col,text in enumerate(values):cells.append({'cell_id':str(row)+'-'+str(col),'row_index':row,'column_index':col,'row_span':1,'column_span':1,'raw_text':text,'bbox':[col*10,row*10,col*10+10,row*10+10]})
  self.data={'pages':[{'page_number':1,'raw_text':'利益演示 单位：元\n案例：张先生30周岁，交费期间5年，年交保费10000元。\n保单年度 年度有效保额\n1 100.5\n3 123'}],'tables':[{'table_id':'t','page_number':1,'column_count':5,'cells':cells}]};self.save()
 def save(self):
  write_json(self.layout,self.data);self.content={'bundle_id':'b','identity':{'product_version_id':'v'},'documents':[{'document_type':'brochure','document_version_id':'d','source_file':'s.pdf','source_sha256':file_hash(self.pdf),'layout_file':'layout.json','layout_sha256':file_hash(self.layout)}]}
 def tearDown(self):self.temp.cleanup()
 def inputs(self):return {'entry_age':55,'sex':'female','payment_years':3,'annual_premium_input':'50000','end_year':4,'scenario':'normal_annual_payment_no_changes_no_loans'}
 def test_year_count_is_explicit_not_an_implicit_amount_time_conversion(self):
  from insurance_rag.illustration.expressions import evaluate
  tree={'type':'multiply','args':[{'type':'parameter','name':'base_amount'},{'type':'year_count','arg':{'type':'min','args':[{'type':'parameter','name':'policy_year'},{'type':'parameter','name':'payment_years'}]}}]};units={'base_amount':'CNY','policy_year':'year','payment_years':'year'}
  self.assertEqual(evaluate(tree,{'base_amount':'2240','policy_year':10,'payment_years':3},units,'CNY')['value'],'6720')
  with self.assertRaisesRegex(ValueError,'invalid_complete_year_count'):evaluate(tree,{'base_amount':'2240','policy_year':'1.5','payment_years':3},units,'CNY')
  tree['args'][1]=tree['args'][1]['arg']
  with self.assertRaisesRegex(ValueError,'unsupported_compound_unit'):evaluate(tree,{'base_amount':'2240','policy_year':10,'payment_years':3},units,'CNY')
if __name__=='__main__':unittest.main()
