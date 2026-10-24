import copy,unittest
from insurance_rag.retrieval.prose import prose_table_blocks,restore_prose_members
from insurance_rag.indexing.chunks import build_clause_chunks
from insurance_rag.retrieval.context import construct_context
import test_clause_chunks as f

def prose_layout():
    first=f.block('10.1 如实告知。',0);last=f.block('申请恢复合同效力时也应如实告知。',2)
    text='如果您故意不履行如实告知义务，对于本合同解除前发生的保险事故，我们不承担保险责任。\n如果您因重大过失未履行如实告知义务，对于本合同解除前发生的保险事故，我们向您退还保险费。'
    middle={**f.block(text,1,None,None),'block_type':'table','table_id':'t'}
    l=f.layout([first,middle,last]);l['tables']=[{'table_id':'t','cells':[{'row_index':i,'raw_text':t,'numeric_tokens':[]} for i,t in enumerate(text.split('\n'))]}]
    return l
class ProseRecoveryTests(unittest.TestCase):
    def test_source_spans_reproduce_recovered_prose(self):
        l=prose_layout();rows=build_clause_chunks(l,'h',counter=f.Counter())
        text=''.join(r['raw_text'] for r in rows)
        self.assertIn(l['blocks'][1]['raw_text'],text)
        by_id={b['block_id']:b for b in l['blocks']}
        for r in rows:
            for s in r['source_spans']:
                a,b=s['source_character_range'];x,y=s['chunk_character_range']
                self.assertEqual(r['raw_text'][x:y],by_id[s['block_id']]['raw_text'][a:b])
    def test_numeric_tables_and_parallel_columns_are_not_prose(self):
        for numeric,parallel in ((True,False),(False,True)):
            l=prose_layout()
            if numeric:l['tables'][0]['cells'][0]['numeric_tokens']=['123']
            if parallel:l['tables'][0]['cells'].append({**l['tables'][0]['cells'][0]})
            self.assertEqual(prose_table_blocks(l),[])
    def test_different_clause_or_non_terms_not_recovered(self):
        l=prose_layout();l['blocks'][-1]['clause_id']='c2';self.assertEqual(prose_table_blocks(l),[])
        l=prose_layout();l['metadata']['document_type']='premium_rate';self.assertEqual(prose_table_blocks(l),[])
    def test_legacy_context_restores_prose_but_keeps_production_gate(self):
        l=prose_layout();old=copy.deepcopy(l);old['tables']=[]
        rows=build_clause_chunks(old,'h',counter=f.Counter())
        for r in rows:r.update(product_version_id='pv',product_name='测试',publication_status='eligible')
        old_bytes=copy.deepcopy(rows)
        result=construct_context([{'record':rows[0]}],rows,counter=len,development=True,layout_loader=lambda d:l)
        item=result['groups']['pv'][0];self.assertIn(l['blocks'][1]['raw_text'],item['body'])
        self.assertEqual(item['restored_source_block_ids'],['b1']);self.assertEqual(rows,old_bytes)
        for s in item['source_spans']:
            source=next(b for b in l['blocks'] if b['block_id']==s['block_id'])
            a,b=s['source_character_range'];x,y=s['body_character_range']
            self.assertEqual(item['body'][x:y],source['raw_text'][a:b])
        production=construct_context([{'record':rows[0]}],rows,counter=len,layout_loader=lambda d:l)
        self.assertNotIn(l['blocks'][1]['raw_text'],production['content'])
    def test_wrong_layout_version_rejected(self):
        l=prose_layout();old=copy.deepcopy(l);old['tables']=[]
        rows=build_clause_chunks(old,'h',counter=f.Counter());l['document_version_id']='wrong'
        with self.assertRaisesRegex(ValueError,'layout_version'):restore_prose_members(rows,l)
if __name__=='__main__':unittest.main()
