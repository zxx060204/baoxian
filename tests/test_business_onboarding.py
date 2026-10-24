import copy,unittest
from insurance_rag.parsing.catalog_boundaries import annotate_appendix_catalogs
from insurance_rag.parsing.common import stable_hash
from insurance_rag.agent.conditions import validate_conditions
class OnboardingTests(unittest.TestCase):
 def test_appendix_and_source_rows_are_not_preceding_clause(self):
  blocks=[{'block_id':str(i),'raw_text':text,'page_number':1,'block_type':'paragraph','parent_section_id':'old','clause_id':'old'} for i,text in enumerate(['9.3 康复特定疾病','附表2：特定药品清单','序号 药品商品名 药品通用名 厂商','1 甲药 通用名甲 厂商甲','2 乙药 通用名乙 厂商乙'])]
  layout={'document_version_id':'d','sections':[],'blocks':blocks};annotate_appendix_catalogs(layout)
  self.assertEqual(blocks[0]['clause_id'],'old');self.assertNotEqual(blocks[1]['clause_id'],'old');self.assertNotEqual(blocks[3]['list_item_id'],blocks[4]['list_item_id'])
 def test_special_conditions_are_explicit_and_typed(self):
  validate_conditions({'family_policy':True,'family_member_count':4,'benefit_paid':False,'accident_occurred':False,'waiting_period_category':'without_waiting_period','waiting_period_waiver_confirmed':True})
  for changes in ({'benefit_paid':'false'},{'family_member_count':True},{'waiting_period_category':'automatic'}):
   with self.assertRaises(ValueError):validate_conditions(changes)
if __name__=='__main__':unittest.main()
