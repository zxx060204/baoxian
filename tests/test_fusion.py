"""Fusion arithmetic and service failure semantics without remote APIs."""
import math,unittest
from insurance_rag.retrieval.fusion import fuse,validate_fusion
from insurance_rag.indexing.opensearch import SearchService
from test_business_retrieval import Client,Provider,mapping,record,response,BINDING

def hit(rid,score):return {'record':record(rid),'score':score}
class FusionTests(unittest.TestCase):
    def test_rrf_common_evidence_beats_single_route(self):
        out=fuse({'bm25':[hit('a',9),hit('b',8)],'dense':[hit('b',.8),hit('c',.7)]})
        self.assertEqual(out['hits'][0]['record']['record_id'],'b');self.assertAlmostEqual(out['hits'][0]['score'],1/62+1/61)
    def test_weighted_exact_arithmetic(self):
        out=fuse({'bm25':[hit('a',10),hit('b',0)],'dense':[hit('b',.9),hit('a',.1)]},method='weighted',dense_weight=.8)
        self.assertEqual(out['hits'][0]['record']['record_id'],'b');self.assertAlmostEqual(out['hits'][0]['score'],.8)
        self.assertEqual(out['hits'][0]['route_scores'],{'bm25':0,'dense':.9})
    def test_missing_candidate_contribution_zero(self):
        out=fuse({'bm25':[hit('a',5)],'dense':[hit('b',.1)]},method='weighted',dense_weight=.2)
        self.assertAlmostEqual(out['hits'][0]['score'],.8)
    def test_constant_and_singleton_scores_are_one(self):
        out=fuse({'bm25':[hit('a',0),hit('b',0)],'dense':[]},method='weighted')
        self.assertTrue(all(h['score']==.5 for h in out['hits']))
    def test_empty_route_keeps_configured_weight(self):
        out=fuse({'bm25':[hit('a',5)],'dense':[]},method='weighted',dense_weight=.8)
        self.assertAlmostEqual(out['hits'][0]['score'],.2)
    def test_failed_route_renormalizes(self):
        out=fuse({'bm25':[hit('a',5)]},method='weighted',dense_weight=.8)
        self.assertEqual(out['hits'][0]['score'],1);self.assertEqual(out['fusion']['effective_weights'],{'bm25':1.0})
    def test_zero_weight_excludes_other_route_only_ids(self):
        out=fuse({'bm25':[hit('a',5)],'dense':[hit('b',.1)]},method='weighted',dense_weight=0)
        self.assertEqual([h['record']['record_id'] for h in out['hits']],['a'])
    def test_duplicate_does_not_inflate_rank_or_score(self):
        out=fuse({'bm25':[hit('a',5),hit('a',5),hit('b',4)]})
        self.assertEqual(out['hits'][1]['ranks']['bm25'],2)
    def test_conflicting_identity_rejected_even_zero_weight(self):
        conflict=hit('a',1);conflict['record']['content']='changed'
        with self.assertRaisesRegex(ValueError,'identity_conflict'):fuse({'bm25':[hit('a',1)],'dense':[conflict]},method='weighted',dense_weight=0)
    def test_invalid_route_scores_rejected(self):
        for v in (True,float('nan'),float('inf'),None):
            with self.subTest(v=v),self.assertRaises(ValueError):fuse({'bm25':[hit('a',v)]})
    def test_invalid_parameters_rejected(self):
        for kwargs in ({'method':'sum'},{'rrf_k':True},{'rrf_k':0},{'dense_weight':-1},{'dense_weight':1.1},{'dense_weight':float('nan')},{'dense_weight':True},{'top_k':0}):
            with self.subTest(kwargs=kwargs),self.assertRaises(ValueError):fuse({},**kwargs)
    def test_ties_and_negative_scores(self):
        out=fuse({'bm25':[hit('b',-1),hit('a',-1)]},method='weighted')
        self.assertEqual([h['record']['record_id'] for h in out['hits']],['a','b'])
    def test_weighted_service_uses_both_routes(self):
        client=Client([mapping(),response([record('a')]),response([record('b')])])
        out=SearchService(client,{'chunk':'test-index'},development=True,embedding=Provider()).search('免责',mode='weighted',dense_weight=.8)
        self.assertEqual(out['hits'][0]['record']['record_id'],'b');self.assertFalse(out['degraded'])
    def test_weighted_embedding_failure_is_explicit(self):
        provider=type('Broken',(),{'binding':BINDING,'encode':lambda self,t:(_ for _ in ()).throw(RuntimeError('outage'))})()
        client=Client([mapping(),response([record('a')])])
        out=SearchService(client,{'chunk':'test-index'},development=True,embedding=provider).search('免责',mode='weighted',dense_weight=.8)
        self.assertTrue(out['degraded']);self.assertEqual(out['hits'][0]['score'],1)
    def test_invalid_weight_rejected_before_any_request(self):
        client=Client([])
        with self.assertRaises(ValueError):SearchService(client,{'chunk':'test-index'}).search('免责',mode='weighted',dense_weight=float('nan'))
        self.assertEqual(client.calls,[])
