import copy,unittest
from insurance_rag.retrieval.context import construct_context
import test_stage_two as fixtures
class ContextIdentityTests(unittest.TestCase):
 def test_same_id_and_text_with_wrong_version_rejected(self):
  row=fixtures.record('id','免责');incoming=copy.deepcopy(row);incoming['product_version_id']='wrong'
  with self.assertRaisesRegex(ValueError,'snapshot_mismatch'):construct_context([{'record':incoming}], [row],counter=len)
