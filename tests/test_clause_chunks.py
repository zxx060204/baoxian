"""New phase-two clause, sentence, enrichment and context invariants."""
import unittest
from insurance_rag.indexing.chunks import build_clause_chunks,ChunkPolicy,ParsedDocument
from insurance_rag.indexing.opensearch import index_definition,index_record,SearchService
from insurance_rag.retrieval.context import construct_context
from insurance_rag.retrieval.reranking import rerank
class Counter:
    name='test_characters';measured_model_tokens=True
    def __call__(self,s):return len(s)
def layout(blocks):
    return {'document_id':'document1','document_version_id':'version1','metadata':{'product_name':'测试医疗保险','document_type':'terms'},
        'sections':[{'section_id':'s1','heading':'第六条 保险责任','section_path':['第二章','第六条 保险责任']},
                    {'section_id':'s2','heading':'第七条 责任免除','section_path':['第二章','第七条 责任免除']}],
        'tables':[],'blocks':blocks}
def block(text,i,clause='c1',section='s1',page=1,scope='native_text_line'):
    return {'block_id':'b'+str(i),'raw_text':text,'block_type':'paragraph','parent_section_id':section,'clause_id':clause,
        'page_number':page,'reading_order':i,'bounding_box':[1,2,3,4],'original_reference':{'extraction_scope':scope}}
def chunks(blocks,**kwargs):
    rows=build_clause_chunks(layout(blocks),'layoutHash',counter=Counter(),**kwargs)
    for r in rows:r.update(product_id='product1',product_version_id='productVersion1',product_name='测试医疗保险',publication_status='eligible')
    return rows
class ChunkTests(unittest.TestCase):
    def test_short_different_clauses_never_merge(self):
        rows=chunks([block('承担保险责任。',0),block('不承担以下责任。',1,'c2','s2')])
        self.assertEqual(len(rows),2);self.assertEqual([r['clause_id'] for r in rows],['c1','c2'])
    def test_complete_clause_under_max_kept_whole(self):
        rows=chunks([block('甲'*150+'。'+'乙'*150+'。',0)],policy=ChunkPolicy(target_tokens=300,max_body_tokens=500))
        self.assertEqual(len(rows),1)
    def test_long_clause_sentence_splits_same_id(self):
        sentence='甲'*120+'。';rows=chunks([block(sentence*10,0)],policy=ChunkPolicy(target_tokens=300,max_body_tokens=500))
        self.assertGreater(len(rows),1);self.assertEqual({r['clause_id'] for r in rows},{'c1'})
        self.assertTrue(all(r['raw_text'].endswith('。') for r in rows));self.assertEqual(''.join(r['raw_text'] for r in rows),sentence*10)
    def test_oversized_sentence_never_cut(self):
        rows=chunks([block('甲'*900+'。',0)])
        self.assertEqual(len(rows),1);self.assertIn('oversized_sentence_preserved',rows[0]['review_reasons'])
    def test_cross_page_sentence_not_split_at_line(self):
        rows=chunks([block('等待期为',0,page=1),block('九十天，期间不承担责任。',1,page=2)])
        self.assertEqual(len(rows),1);self.assertEqual((rows[0]['page_start'],rows[0]['page_end']),(1,2))
        self.assertEqual(rows[0]['raw_text'],'等待期为\n九十天，期间不承担责任。')
    def test_physical_lines_and_decimal_numbers(self):
        rows=chunks([block('费率为27.3，',0),block('其适用条件如下。',1)])
        self.assertEqual(len(rows),1);self.assertIn('27.3',rows[0]['raw_text'])
    def test_paragraph_priority(self):
        text='甲'*100+'。'+'乙'*100+'。'
        rows=chunks([block(text,0,scope='logical_paragraph'),block(text,1,scope='logical_paragraph'),block(text,2,scope='logical_paragraph')],
                    policy=ChunkPolicy(target_tokens=300,max_body_tokens=500))
        self.assertEqual(len(rows),2);self.assertTrue(rows[0]['raw_text'].endswith('\n\n'))
    def test_all_source_spans_reproduce_original_characters(self):
        blocks=[block('第一句。第二句。'*50,0),block('第三句。'*50,1,page=2)]
        rows=chunks(blocks,policy=ChunkPolicy(target_tokens=100,max_body_tokens=150));by_id={b['block_id']:b for b in blocks}
        for r in rows:
            for s in r['source_spans']:
                a,b=s['source_character_range'];x,y=s['chunk_character_range']
                self.assertEqual(r['raw_text'][x:y],by_id[s['block_id']]['raw_text'][a:b])
    def test_content_shared_and_metadata_not_embedded(self):
        r=chunks([block('正文。',0)],summary='保障范围说明。',contexts={'c1':'本条说明保险责任。'})[0]
        self.assertEqual(r['content'],r['embedding_text']);self.assertEqual(r['content'],r['bm25_text'])
        self.assertIn('文档摘要',r['content']);self.assertIn('第二章',r['content'])
        for value in ('document1','version1','productVersion1',r['chunk_id']):self.assertNotIn(value,r['content'])
    def test_summary_and_context_limits_reject_not_truncate(self):
        with self.assertRaisesRegex(ValueError,'summary_exceeds'):chunks([block('正文。',0)],summary='甲'*101)
        with self.assertRaisesRegex(ValueError,'context_exceeds'):chunks([block('正文。',0)],contexts={'c1':'甲'*101})
    def test_model_input_limit_flagged(self):
        r=chunks([block('正文。',0)],policy=ChunkPolicy(max_input_tokens=20))[0]
        self.assertIn('embedding_input_exceeds_limit',r['review_reasons'])
    def test_relationships_and_stable_ids(self):
        rows=chunks([block('甲。'*200,0)],policy=ChunkPolicy(target_tokens=100,max_body_tokens=150))
        self.assertEqual(rows,chunks([block('甲。'*200,0)],policy=ChunkPolicy(target_tokens=100,max_body_tokens=150)))
        for i,r in enumerate(rows):
            self.assertEqual(r['chunk_index'],i);self.assertEqual(r['prev_chunk_id'],rows[i-1]['chunk_id'] if i else None)
    def test_heading_candidates_do_not_merge_unknown_clauses(self):
        rows=chunks([block('第六条 保险责任',0,None,None),block('承担责任。',1,None,None),
                     block('第七条 责任免除',2,None,None),block('不承担责任。',3,None,None)])
        self.assertEqual(len(rows),2);self.assertNotEqual(rows[0]['clause_id'],rows[1]['clause_id'])
        self.assertIn('candidate_clause_boundary_requires_review',rows[0]['review_reasons'])
    def test_serializer_exact_fields(self):
        r=chunks([block('正文。',0)])[0];wire=index_record(r,vector=[1.,2.],binding={'dimension':2})
        self.assertIn('content',wire);self.assertIn('embedding',wire);self.assertNotIn('vector',wire)
        mapping=index_definition('chunk',config_hash='x',embedding_binding={'dimension':2})['mappings']['properties']
        for key in ('section_id','clause_id','document_id'):self.assertEqual(mapping[key]['type'],'keyword')
        for key in ('page_start','page_end','chunk_index'):self.assertEqual(mapping[key]['type'],'integer')
class ContextTests(unittest.TestCase):
    def test_restore_clause_deduplicate_hits_never_cross_免责(self):
        rows=chunks([block('甲。'*200,0),block('免责条款。',1,'c2','s2')],policy=ChunkPolicy(target_tokens=100,max_body_tokens=150))
        hit={'record':rows[1],'score':1};result=construct_context([hit,hit],rows,counter=Counter(),development=True)
        groups=result['groups']['productVersion1'];self.assertEqual(len(groups),1)
        self.assertEqual(groups[0]['body'],'甲。'*200);self.assertNotIn('免责条款',result['content'])
    def test_context_source_ranges_shift_after_restoration(self):
        original='甲。'*200;rows=chunks([block(original,0)],policy=ChunkPolicy(target_tokens=100,max_body_tokens=150))
        result=construct_context([{'record':rows[1],'score':1}],rows,counter=Counter(),development=True)
        item=result['groups']['productVersion1'][0]
        for span in item['source_spans']:
            a,b=span['body_character_range'];x,y=span['source_character_range']
            self.assertEqual(item['body'][a:b],original[x:y]);self.assertIn('chunk_id',span)
    def test_context_does_not_restore_toc_as_clause_body(self):
        rows=chunks([block('责任正文。',0)]);toc={**rows[0],'chunk_id':'toc','record_id':'toc','chunk_index':1,
            'raw_text':'目录。','content':'目录。','semantic_role':'table_of_contents'}
        result=construct_context([{'record':rows[0],'score':1}],rows+[toc],counter=Counter(),development=True)
        self.assertNotIn('目录',result['content'])
    def test_context_budget_omits_entire_clause(self):
        rows=chunks([block('甲。'*200,0)],policy=ChunkPolicy(target_tokens=100,max_body_tokens=150))
        result=construct_context([{'record':rows[0],'score':1}],rows,counter=Counter(),max_tokens=50,development=True)
        self.assertEqual(result['groups'],{});self.assertEqual(result['omitted'][0]['reason'],'complete_clause_exceeds_context_budget')
    def long_rows(self):
        return chunks([block(('甲'*60+'。')*30,0),block('免责条款。',1,'c2','s2')],policy=ChunkPolicy(target_tokens=80,max_body_tokens=100))
    def test_over_budget_restores_two_neighbors_each_side(self):
        rows=self.long_rows();hit=rows[8]
        result=construct_context([{'record':hit}],rows,counter=Counter(),max_tokens=400)
        item=result['groups']['productVersion1'][0]
        self.assertEqual(item['chunk_ids'],[r['chunk_id'] for r in rows[6:11]])
        self.assertEqual(item['restoration_mode'],'neighbor_window');self.assertFalse(item['textual_clause_complete'])
        self.assertEqual((item['neighbors_before'],item['neighbors_after']),(2,2))
        self.assertEqual(result['omitted'],[]);self.assertEqual(len(result['fallbacks']),1)
        self.assertNotIn('免责',item['body']);self.assertLessEqual(result['token_count'],400)
        original=('甲'*60+'。')*30
        for span in item['source_spans']:
            a,b=span['body_character_range'];x,y=span['source_character_range']
            self.assertEqual(item['body'][a:b],original[x:y])
    def test_window_shrinks_but_keeps_hit(self):
        rows=self.long_rows();result=construct_context([{'record':rows[8]}],rows,counter=Counter(),max_tokens=150)
        item=result['groups']['productVersion1'][0]
        self.assertIn(rows[8]['chunk_id'],item['chunk_ids']);self.assertLess(len(item['chunk_ids']),5)
        self.assertLessEqual(result['token_count'],150)
    def test_boundary_window_stays_in_clause(self):
        rows=self.long_rows();result=construct_context([{'record':rows[0]}],rows,counter=Counter(),max_tokens=400)
        item=result['groups']['productVersion1'][0]
        self.assertEqual(item['chunk_ids'],[r['chunk_id'] for r in rows[:3]])
        self.assertEqual((item['neighbors_before'],item['neighbors_after']),(0,2))
    def test_multiple_windows_do_not_duplicate_or_bridge_missing_text(self):
        rows=self.long_rows();hits=[{'record':rows[i]} for i in (3,4,15)]
        result=construct_context(hits,rows,counter=Counter(),max_tokens=800)
        items=result['groups']['productVersion1'];ids=[rid for item in items for rid in item['chunk_ids']]
        self.assertEqual(len(items),2);self.assertEqual(len(ids),len(set(ids)))
        self.assertIn(rows[15]['chunk_id'],ids);self.assertNotIn(rows[9]['chunk_id'],ids)
        self.assertLessEqual(result['token_count'],800)
    def test_fallback_does_not_bypass_quarantined_neighbor(self):
        rows=self.long_rows();rows[7]['publication_status']='quarantined'
        result=construct_context([{'record':rows[8]}],rows,counter=Counter(),max_tokens=400)
        self.assertEqual(result['groups'],{});self.assertEqual(result['fallbacks'],[])
    def test_changed_hit_covered_by_window_still_rejected(self):
        rows=self.long_rows();bad={**rows[9],'content':'changed'}
        with self.assertRaisesRegex(ValueError,'snapshot_mismatch'):
            construct_context([{'record':rows[8]},{'record':bad}],rows,counter=Counter(),max_tokens=400)
    def test_unreviewed_neighbor_not_released(self):
        rows=chunks([block('甲。'*200,0)],policy=ChunkPolicy(target_tokens=100,max_body_tokens=150));rows[1]['publication_status']='quarantined'
        result=construct_context([{'record':rows[0],'score':1}],rows,counter=Counter())
        self.assertEqual(result['groups'],{});self.assertIn('quarantined',result['omitted'][0]['reason'])
    def test_changed_retrieved_content_rejected(self):
        rows=chunks([block('正文。',0)]);hit={'record':{**rows[0],'content':'changed'},'score':1}
        with self.assertRaisesRegex(ValueError,'snapshot_mismatch'):construct_context([hit],rows,counter=Counter())
    def test_cross_encoder_scores_replace_retrieval_rank(self):
        rows=chunks([block('保障。',0),block('免责。',1,'c2','s2')]);hits=[{'record':r,'score':100-i} for i,r in enumerate(rows)]
        provider=type('Scorer',(),{'score':lambda self,q,c:[.1,.9]})()
        result=rerank('免责',hits,provider,1);self.assertEqual(result[0]['record']['clause_id'],'c2');self.assertEqual(result[0]['retrieval_score'],99)
class ProductFilterTests(unittest.TestCase):
    def test_product_vector_has_distinct_name(self):
        r={'scope':'product','record_id':'v1','content':'产品摘要','age_range':{'min':18,'max':60}}
        wire=index_record(r,vector=[1.,2.],binding={'dimension':2})
        self.assertIn('summary_embedding',wire);self.assertNotIn('embedding',wire)
        self.assertEqual((wire['age_min'],wire['age_max']),(18,60))
        mapping=index_definition('product',config_hash='x',embedding_binding={'dimension':2})['mappings']['properties']
        self.assertIn('summary_embedding',mapping);self.assertNotIn('embedding',mapping)
    def test_age_filter_ranges_and_unknown_exclusion(self):
        from insurance_rag.retrieval.query import validate,filter_clauses,record_matches
        _,filters=validate('医疗险','product',{'age':35,'insurance_type':'medical'})
        self.assertIn({'range':{'age_min':{'lte':35}}},filter_clauses(filters,'product'))
        self.assertTrue(record_matches({'age_range':{'min':18,'max':60},'insurance_type':'medical'},filters,'product'))
        self.assertFalse(record_matches({'insurance_type':'medical'},filters,'product'))
        self.assertFalse(record_matches({'age_range':{'min':40,'max':60},'insurance_type':'medical'},filters,'product'))
    def test_age_filter_requires_product_integer(self):
        from insurance_rag.retrieval.query import validate
        for scope,age in [('chunk',35),('product',True),('product','35'),('product',151)]:
            with self.assertRaisesRegex(ValueError,'invalid_age_filter'):validate('保险',scope,{'age':age})
    def test_old_opensearch_schema_requires_rebuild(self):
        client=type('Client',(),{'request':lambda self,*a,**k:{'old':{'mappings':{'_meta':{'scope':'chunk','schema_version':1}}}}})()
        with self.assertRaisesRegex(ValueError,'requires_rebuild'):SearchService(client,{'chunk':'test-chunk'},development=True).search('责任')
if __name__=='__main__':unittest.main()
