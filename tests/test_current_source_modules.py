import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from test_generic_source_mapping import layout,table,cell
from insurance_rag.parsing.common import write_json,file_hash,stable_hash
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.ingestion.source_package import reference

class PublicationTests(unittest.TestCase):
 def test_current_bundle_publication_is_independent_and_owner_scoped(self):
  from insurance_rag.agent.store import AgentStore,Forbidden
  from insurance_rag.ingestion.store import asset_folder,register_asset,owner_key,bundle
  from insurance_rag.ingestion.source_publication import prepare_source_bundle,publish_source
  from insurance_rag.ingestion.library import candidates
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);store=AgentStore.sqlite(root/'db');app=SimpleNamespace(root=root,store=store)
   try:
    folder=asset_folder(root,'alice');folder.mkdir(parents=True);pdf=folder/'source.pdf';pdf.write_bytes(b'%PDF-1.7 fixture');asset=register_asset(app,'alice',pdf)
    data=layout([table([cell(0,0,'条件'),cell(0,1,'值'),cell(1,0,'A'),cell(1,1,'2')],2,2)]);write_json(root/'layout.json',data)
    doc={'document_type':'cash_value','document_version_id':'d','asset_id':asset['id'],'source_file':pdf.relative_to(root).as_posix(),'source_sha256':file_hash(pdf),'layout_file':'layout.json','layout_sha256':file_hash(root/'layout.json')}
    identity={'product_id':'p','product_version_id':'v','product_name':'new','space_id':owner_key('alice')};ref=prepare_mapping(app,'alice',{'identity':identity,'documents':[doc]},root)['package_ref']
    with patch('insurance_rag.ingestion.source_tables.prepare_tables',side_effect=AssertionError('numeric executed')),patch('insurance_rag.ingestion.source_index.build_source_index',side_effect=AssertionError('index executed')):
     registered=prepare_source_bundle(app,'alice',ref);self.assertEqual(registered['content']['capabilities']['source_tables']['status'],'not_imported');publish_source(app,'alice',registered['id'])
    self.assertEqual(len(candidates(app)),1);self.assertEqual(bundle(app,'bob',registered['id'])['owner_id'],'alice')
    with self.assertRaises(Forbidden):publish_source(app,'bob',registered['id'])
    publish_source(app,'alice',registered['id'],False);self.assertEqual(candidates(app),[]);self.assertTrue(pdf.exists());self.assertEqual(bundle(app,'alice',registered['id'])['content']['source_package_ref'],ref)
    with self.assertRaises(Forbidden):bundle(app,'bob',registered['id'])
   finally:store.close()
 def test_index_uses_only_current_text_and_never_numeric_grid(self):
  from insurance_rag.ingestion.source_index import source_chunks
  from insurance_rag.ingestion.source_package import load_package
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);app=SimpleNamespace(root=root);(root/'source.pdf').write_bytes(b'%PDF-1.7 fixture');data=layout([table([cell(0,0,'条件'),cell(0,1,'值'),cell(1,0,'A'),cell(1,1,'999999999')],2,2)],kind='terms')
   data['blocks']=[{'block_id':'body','block_type':'paragraph','raw_text':'保险责任。','page_number':1,'reading_order':1,'bounding_box':[0,0,10,10]}];write_json(root/'layout.json',data)
   doc={'document_type':'terms','document_version_id':'d','company_id':'c','source_file':'source.pdf','source_sha256':file_hash(root/'source.pdf'),'layout_file':'layout.json','layout_sha256':file_hash(root/'layout.json')}
   ref=prepare_mapping(app,None,{'identity':{},'documents':[doc]},root)['package_ref']
   chunks=source_chunks(root,load_package(root,ref))
   self.assertEqual(len(chunks),1);self.assertIn('保险责任',chunks[0]['content']);self.assertNotIn('999999999',chunks[0]['content'])

if __name__=='__main__':unittest.main()
