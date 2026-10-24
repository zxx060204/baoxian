import copy,unittest
from insurance_rag.retrieval.draft_assessment import assess_drafts
from insurance_rag.indexing.opensearch import SearchService
from test_business_retrieval import Client,Provider,mapping,record,response
RULES={'schema_version':1,'rules':[{'rule_id':'s','trigger':'退保','related_terms':['解除合同'],'relation':'query_related_not_equivalent','status':'agent_reviewed_candidate'},{'rule_id':'v','trigger':'退保','requires_any':['多少钱'],'related_terms':['现金价值'],'relation':'query_related_not_equivalent','status':'agent_reviewed_candidate'}]}
class LexiconTests(unittest.TestCase):
 def test_bm25_expansion_does_not_change_dense_query(self):
  provider=Provider();encoded=[];provider.encode=lambda texts:(encoded.extend(texts) or [[1.,0.]])
  client=Client([mapping(),response([record()]),response([record()])]);SearchService(client,{'chunk':'test-index'},development=True,embedding=provider).search('退保',mode='rrf',lexical_query='退保 解除合同')
  self.assertEqual(encoded,['退保']);self.assertEqual(client.calls[1][0][2]['query']['bool']['must'][0]['multi_match']['query'],'退保 解除合同')
 def test_assessment_never_mutates_audit(self):
  q={'queries':[{'query_id':'q','query':'免责如何规定','scope':'chunk','relevant':{'r':1},'source_evidence':[{'candidate_labels':'page_projection','page_number':1}],'audit':{'status':'pending'}}]};before=copy.deepcopy(q);r=assess_drafts(q);self.assertEqual(q,before);self.assertEqual(r['results'][0]['decision'],'revise')
