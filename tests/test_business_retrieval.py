"""Hybrid retrieval failure contracts, entirely offline."""
import unittest
from insurance_rag.indexing.opensearch import SearchService
from insurance_rag.retrieval.query import rrf

BINDING={'model':'fixture','dimension':2}
class Client:
    def __init__(self,responses):self.responses=iter(responses);self.calls=[]
    def request(self,*args,**kwargs):
        self.calls.append((args,kwargs));result=next(self.responses)
        if isinstance(result,Exception):raise result
        return result
def mapping():return {'index':{'mappings':{'_meta':{'schema_version':2,'scope':'chunk','embedding_binding':BINDING}}}}
def record(rid='a',version='v1'):
    return {'record_id':rid,'content':'免责条款','scope':'chunk','product_version_id':version,'document_version_id':'d1','publication_status':'quarantined'}
def response(rows):return {'hits':{'hits':[{'_source':{'record':r},'_score':1.} for r in rows]}}
class Provider:
    binding=BINDING
    def encode(self,texts):return [[1.,0.] for t in texts]



class HybridTests(unittest.TestCase):
    def test_business_candidates_enter_both_query_filters(self):
        client=Client([mapping(),response([]),response([])]);service=SearchService(client,{'chunk':'test-index'},embedding=Provider(),development=True)
        service.search('免责',mode='rrf',restrict_versions=['v1','v2'])
        bodies=[call[0][2]['query'] for call in client.calls[1:]]
        self.assertIn({'terms':{'product_version_id':['v1','v2']}},bodies[0]['bool']['filter'])
        self.assertIn({'terms':{'product_version_id':['v1','v2']}},bodies[1]['knn']['embedding']['filter']['bool']['filter'])
    def test_empty_candidates_skip_all_search_requests(self):
        client=Client([]);result=SearchService(client,{'chunk':'test-index'},embedding=Provider()).search('免责',mode='rrf',restrict_versions=[])
        self.assertEqual(client.calls,[]);self.assertEqual(result['hits'],[])
    def test_business_backend_leak_rejected(self):
        client=Client([mapping(),response([record(version='wrong')])])
        with self.assertRaisesRegex(ValueError,'candidate_isolation'):SearchService(client,{'chunk':'test-index'},development=True).search('免责',restrict_versions=['v1'])
    def test_query_embedding_outage_preserves_bm25(self):
        provider=type('Broken',(),{'binding':BINDING,'encode':lambda self,t:(_ for _ in ()).throw(RuntimeError('service_failed'))})()
        client=Client([mapping(),response([record()])]);result=SearchService(client,{'chunk':'test-index'},embedding=provider,development=True).search('免责',mode='rrf')
        self.assertTrue(result['degraded']);self.assertEqual(result['routes']['dense'],'failed');self.assertEqual(len(result['hits']),1)
    def test_dense_outage_does_not_silently_become_empty(self):
        provider=type('Broken',(),{'binding':BINDING,'encode':lambda self,t:(_ for _ in ()).throw(RuntimeError('service_failed'))})()
        client=Client([mapping()])
        with self.assertRaises(RuntimeError):SearchService(client,{'chunk':'test-index'},embedding=provider).search('免责',mode='dense')
    def test_index_route_outage_is_explicit(self):
        client=Client([mapping(),response([record()]),RuntimeError('opensearch_failed')]);result=SearchService(client,{'chunk':'test-index'},embedding=Provider(),development=True).search('免责',mode='rrf')
        self.assertTrue(result['degraded']);self.assertEqual(len(result['hits']),1)
    def test_double_outage_fails(self):
        client=Client([mapping(),RuntimeError('failed'),RuntimeError('failed')])
        with self.assertRaisesRegex(RuntimeError,'all_search_routes'):SearchService(client,{'chunk':'test-index'},embedding=Provider(),development=True).search('免责',mode='rrf')
    def test_source_hash_cached_within_request_only(self):
        import tempfile
        from pathlib import Path
        from unittest.mock import patch
        from insurance_rag.parsing.common import file_hash
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);(root/'source.pdf').write_bytes(b'fixture');(root/'layout.json').write_bytes(b'fixture-layout')
            a={**record(),'source_file':'source.pdf','source_sha256':file_hash(root/'source.pdf'),
               'layout_file':'layout.json','layout_sha256':file_hash(root/'layout.json')};b={**a,'record_id':'b'}
            client=Client([mapping(),response([a,b]),response([a,b]),mapping(),response([a,b]),response([a,b])])
            service=SearchService(client,{'chunk':'test-index'},embedding=Provider(),development=True,workspace_root=root)
            with patch('insurance_rag.indexing.opensearch.file_hash',wraps=file_hash) as hasher:
                service.search('免责',mode='rrf');self.assertEqual(hasher.call_count,2)
                service.search('免责',mode='rrf');self.assertEqual(hasher.call_count,4)
    def test_fusion_conflicting_same_id_rejected(self):
        a={'record':record(),'score':1};b={'record':record(version='other'),'score':2}
        with self.assertRaisesRegex(ValueError,'identity_conflict'):rrf({'bm25':[a],'dense':[b]})
if __name__=='__main__':unittest.main()
