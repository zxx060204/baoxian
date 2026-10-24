"""Owner-scoped table registration and Agent/API reads use the same literal batch."""
from unittest.mock import patch
import unittest
import test_current_source_api as fixture
from insurance_rag.parsing.common import write_json
from insurance_rag.ingestion.source_package import reference
from insurance_rag.tools.adapters.product_source_values import SourceRequest,query_source


class LiteralTableAccessTests(unittest.TestCase):
 setUp=fixture.CurrentSourceAPITests.setUp
 tearDown=fixture.CurrentSourceAPITests.tearDown
 def register(self):
  prepared=self.client.post('/v1/product-ingestion/jobs/job/table-preparation',json={}).json()
  value={'scope':'literal_source_tables_import','source_package_ref':prepared['source_package_ref'],'source_package_id':prepared['source_package_id'],'prepared_ref':prepared['result_ref'],'contract':'literal-source-tables-v1','table_batch_id':'literal-batch','state':'ready','table_count':1,'cell_count':4,'record_count':1,'integrity':'matched'}
  write_json(self.root/'import.json',value);r=self.client.post('/v1/product-ingestion/jobs/job/business-bundle',json={'table_ref':reference(self.root,self.root/'import.json')});self.assertEqual(r.status_code,200);self.assertEqual(r.json()['capabilities']['source_tables']['table_count'],1);return r.json()['bundle_id']
 def test_owner_scoped_api_and_tool_read_raw_cells_without_model(self):
  bid=self.register();packet={'status':'matched','table':{'table_id':'t'},'cells':[{'raw_text':'100'}],'contexts':[{'raw_text':'不统一业务字段'}]}
  with patch('insurance_rag.ingestion.source_table_store.list_tables',return_value={'status':'matched','tables':[{'table_id':'t'}]}) as listing,patch('insurance_rag.ingestion.source_table_store.read_table',return_value=packet) as reading:
   self.assertEqual(self.client.get('/v1/product-ingestion/bundles/'+bid+'/tables').json()['tables'][0]['table_id'],'t')
   response=self.client.post('/v1/product-ingestion/bundles/'+bid+'/table-read',json={'document_id':'d','table_id':'t','row_start':1,'column_start':1,'row_count':1,'column_count':1});self.assertEqual(response.json()['cells'][0]['raw_text'],'100');self.assertEqual(reading.call_args.args[1],'literal-batch')
   args=SourceRequest(bundle_id=bid,action='read',document_id='d',table_id='t',row_start=1,column_start=1,row_count=1,column_count=1).model_dump();result=query_source(args,{'app':self.app,'owner':'alice'});self.assertEqual(result['data']['source_table']['cells'][0]['raw_text'],'100')
   before=reading.call_count;self.owner='bob';self.assertEqual(self.client.post('/v1/product-ingestion/bundles/'+bid+'/table-read',json={'document_id':'d','table_id':'t'}).status_code,404);self.assertEqual(reading.call_count,before)
  self.app.model.structured.assert_not_called()
 def test_table_access_rejects_oversized_or_missing_coordinate_requests(self):
  with self.assertRaises(ValueError):SourceRequest(bundle_id='b',action='read')
  with self.assertRaises(ValueError):SourceRequest(bundle_id='b',action='read',document_id='d',table_id='t',row_count=200,column_count=200)
  bid=self.register();r=self.client.post('/v1/product-ingestion/bundles/'+bid+'/table-read',json={'document_id':'d','table_id':'t','row_start':-1});self.assertEqual(r.status_code,422)
 def test_capability_points_to_literal_table_reading(self):
  bid=self.register()
  from insurance_rag.tools.adapters.capabilities import capability_view
  r=capability_view({'bundle_id':bid},{'app':self.app,'owner':'alice','state':{}});entries=r['data']['capability_view']['capabilities'];entry=next(e for e in entries if e['capability']=='source_tables');self.assertEqual(entry['next_step']['action'],'tables');self.assertFalse(any(e['capability']=='source_numeric_tables' for e in entries))


if __name__=='__main__':unittest.main()
