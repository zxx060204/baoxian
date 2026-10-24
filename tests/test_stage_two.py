"""Stage-two provenance, publication isolation and backend contracts."""
import copy,json,tempfile,unittest
from pathlib import Path
from insurance_rag.parsing.common import write_json,file_hash,stable_hash
from insurance_rag.indexing.embedding import EmbeddingCache,validate_vector
from insurance_rag.indexing.opensearch import checked_bulk,index_definition,SearchService,OpenSearchHTTP,switch_aliases
from insurance_rag.retrieval.lexical import OfflineBM25,tokenize
from insurance_rag.retrieval.query import rrf,validate
from insurance_rag.retrieval.evaluation import evaluate,metrics

AUDIT={'status':'confirmed','reviewer':'test-only','reviewed_at':'2026-10-12T00:00:00+00:00','evidence':['synthetic fixture, not production approval']}
class Counter:
    name='test_character_counter';measured_model_tokens=True
    def __call__(self,s):return len(s)
class Provider:
    binding={'provider':'test_fixture','revision':'one','dimension':2}
    def __init__(self):self.calls=0
    def encode(self,texts):self.calls+=1;return [[1.,.5] for _ in texts]
class Client:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def request(self,*args,**kwargs):self.calls.append((args,kwargs));return next(self.responses)

def record(rid,text,*,status='eligible',version='v1'):
    return {'record_id':rid,'scope':'chunk','schema_version':2,'raw_text':text,'publication_status':status,
            'company_id':'A','product_id':'p1','product_version_id':version,'document_id':'doc1','document_version_id':'d1','document_type':'terms',
            'schema_version':2,'chunk_id':rid,'section_id':'s1','clause_id':'c1','content':text,'embedding_text':text,'bm25_text':text,
            'source_spans':[{'block_id':'b1'}],'page_start':1,'page_end':1,'chunk_index':0,
            'prev_chunk_id':None,'next_chunk_id':None,'review_reasons':[]}

class IndexingTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        (self.root/'source.pdf').write_bytes(b'%PDF synthetic fixture')
        layout={'document_version_id':'d1','metadata':{'company_id':'A','product_name':'测试寿险','document_type':'terms'},
          'sections':[],'tables':[],'blocks':[{'block_id':'b1','block_type':'text','page_number':1,'reading_order':0,
          'parent_section_id':'s1','clause_id':'c1','raw_text':'责任免除：故意行为不承担保险责任。','bounding_box':[1,2,3,4],
          'original_reference':{'page_number':1,'file_path':'source.pdf'}}]}
        write_json(self.root/'layout.json',layout)
        self.case={'document_version_id':'d1','layout':'layout.json','source':'source.pdf',
            'layout_sha256':file_hash(self.root/'layout.json'),'file_sha256':file_hash(self.root/'source.pdf')}
        self.index={'cases':[self.case]}
        self.review={'entries':[{'document_version_id':'d1','source_sha256':self.case['file_sha256'],
          'layout_sha256':self.case['layout_sha256'],'product_id':'p1','product_version_id':'v1',
          'identity_audit':AUDIT,'text_audit':AUDIT}]}
    def tearDown(self):self.temp.cleanup()

class RetrievalTests(unittest.TestCase):
    def test_chinese_and_decimal_terms(self):
        self.assertIn('责任',tokenize('保险责任27.3'));self.assertIn('27.3',tokenize('保险责任27.3'))
    def test_version_and_publication_filter(self):
        records=[record('a','免责条款'),record('b','免责条款',status='quarantined'),record('c','免责条款',version='v2')]
        hits=OfflineBM25(records).search('免责',filters={'product_version_id':'v1'})
        self.assertEqual([h['record']['record_id'] for h in hits],['a'])
        self.assertEqual(len(OfflineBM25(records,development=True).search('免责')),3)
    def test_request_cannot_override_publication(self):
        with self.assertRaises(ValueError):validate('保险',filters={'publication_status':'quarantined'})
    def test_rrf_duplicate_and_tie(self):
        a={'record':record('a',''),'score':100};b={'record':record('b',''),'score':1}
        hits=rrf({'bm25':[a,a,b],'dense':[b,a]})
        self.assertEqual(len(hits),2);self.assertGreater(hits[0]['score'],0)
    def test_relevance_metrics(self):
        m=metrics(['b','a'],{'a':2,'b':1,'c':1},2)
        self.assertAlmostEqual(m['recall'],2/3);self.assertEqual(m['mrr'],1);self.assertLess(m['ndcg'],1)
    def test_pending_query_never_counted(self):
        out=evaluate([{'audit':{'status':'pending'}}],lambda *a:[])
        self.assertEqual(out['status'],'awaiting_query_review');self.assertEqual(out['reviewed_queries'],0)
    def test_empty_query_and_invalid_gold(self):
        out=evaluate([{'query_id':'q','query':'不存在','expected_empty':True,'audit':AUDIT}],lambda *a:[])
        self.assertTrue(out['results'][0]['empty_correct'])
        with self.assertRaises(ValueError):evaluate([{'query_id':'q','query':'a','audit':AUDIT}],lambda *a:[])

class BackendTests(unittest.TestCase):
    def test_local_endpoint_and_no_url_credentials(self):
        with self.assertRaises(ValueError):OpenSearchHTTP('http://outside.example')
        with self.assertRaises(ValueError):OpenSearchHTTP('http://user:secret@localhost:9200')
    def test_hnsw_mapping(self):
        m=index_definition('chunk',config_hash='c',embedding_binding=Provider.binding)
        self.assertEqual(m['mappings']['properties']['embedding']['method']['engine'],'lucene')
        self.assertEqual(m['mappings']['dynamic'],'strict')
    def test_bulk_serialization_matches_strict_mapping_and_search_field(self):
        rows=[record('a','责任免除'), {'record_id':'p1','scope':'product','product_name':'测试寿险',
              'raw_text':'测试寿险','content':'测试寿险'}]
        for row in rows:
            c=Client([{'errors':False,'items':[{'index':{'status':201}}]},{}])
            checked_bulk(c,'test-index',[row])
            body=json.loads(c.calls[0][0][2].splitlines()[1])
            props=index_definition(row['scope'],config_hash='c')['mappings']['properties']
            self.assertFalse(set(body)-set(props))
            self.assertEqual(body['content'],row['content'])
            self.assertEqual(body['record'],row)

    def test_partial_bulk_blocks_progress(self):
        c=Client([{'errors':True,'items':[{'index':{'status':400}}]}])
        with self.assertRaisesRegex(RuntimeError,'partial_failure'):checked_bulk(c,'test-index',[record('a','免责')])
        self.assertEqual(len(c.calls),1)
    def test_bulk_newline_and_refresh(self):
        c=Client([{'errors':False,'items':[{'index':{'status':201}}]},{}])
        checked_bulk(c,'test-index',[record('a','免责')]);self.assertTrue(c.calls[0][0][2].endswith('\n'))
        self.assertEqual(c.calls[-1][0][1],'/test-index/_refresh')
    def test_aliases_switch_together(self):
        c=Client([{'acknowledged':True}]);switch_aliases(c,{'test-product':'test-p-1','test-chunk':'test-c-1'})
        self.assertEqual(len(c.calls),1);self.assertEqual(len(c.calls[0][0][2]['actions']),4)
    def test_dense_filter_and_model_binding(self):
        c=Client([{'index':{'mappings':{'_meta':{'schema_version':2,'scope':'chunk','embedding_binding':Provider.binding}}}},
                  {'hits':{'hits':[]}}])
        s=SearchService(c,{'chunk':'test-index'},embedding=Provider())
        s.search('免责',filters={'product_version_id':'v1'},mode='dense')
        q=c.calls[-1][0][2]['query']['knn']['embedding']
        self.assertIn({'term':{'publication_status':'eligible'}},q['filter']['bool']['filter'])
        self.assertIn({'term':{'product_version_id':'v1'}},q['filter']['bool']['filter'])
    def test_wrong_model_rejected_before_search(self):
        c=Client([{'index':{'mappings':{'_meta':{'schema_version':2,'scope':'chunk','embedding_binding':{'revision':'wrong'}}}}}])
        with self.assertRaisesRegex(ValueError,'model_mismatch'):
            SearchService(c,{'chunk':'test-index'},embedding=Provider()).search('免责',mode='dense')
        self.assertEqual(len(c.calls),1)
    def test_backend_cannot_return_quarantined_result(self):
        c=Client([{'index':{'mappings':{'_meta':{'schema_version':2,'scope':'chunk'}}}},
            {'hits':{'hits':[{'_source':{'record':record('a','保险',status='quarantined')},'_score':1}]}}])
        with self.assertRaisesRegex(ValueError,'isolation_violation'):
            SearchService(c,{'chunk':'test-index'}).search('保险')
    def test_published_result_requires_source_verification(self):
        c=Client([{'index':{'mappings':{'_meta':{'schema_version':2,'scope':'chunk'}}}},
            {'hits':{'hits':[{'_source':{'record':record('a','保险')},'_score':1}]}}])
        with self.assertRaisesRegex(ValueError,'workspace_root_required'):
            SearchService(c,{'chunk':'test-index'}).search('保险')
    def test_published_result_changed_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            folder=Path(t);(folder/'original.pdf').write_bytes(b'changed')
            r=record('a','保险');r.update(source_file='original.pdf',source_sha256='old')
            c=Client([{'index':{'mappings':{'_meta':{'schema_version':2,'scope':'chunk'}}}},
                {'hits':{'hits':[{'_source':{'record':r},'_score':1}]}}])
            with self.assertRaisesRegex(ValueError,'source_changed'):
                SearchService(c,{'chunk':'test-index'},workspace_root=folder).search('保险')
    def test_embedding_cache_resume_and_revision(self):
        with tempfile.TemporaryDirectory() as t:
            cache=EmbeddingCache(Path(t)/'cache.db');p=Provider()
            one,stats=cache.encode(['a','a','b'],p,batch_size=1)
            self.assertEqual(p.calls,2);self.assertEqual(stats['computed_unique_texts'],2)
            two,stats=cache.encode(['a','b'],p);self.assertEqual(stats['cache_hits'],2)
            p.binding={**p.binding,'revision':'two'};cache.encode(['a'],p);self.assertEqual(p.calls,3)
            cache.close()
    def test_bad_vectors_rejected(self):
        for v in ([0,0],[1,float('nan')],[1],[True,1]):
            with self.assertRaises(ValueError):validate_vector(v,2)

if __name__=='__main__':unittest.main()
