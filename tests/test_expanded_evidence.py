import unittest
from insurance_rag.retrieval.evaluation import evaluate
import test_stage_two as fixtures
class ExpandedEvidenceTests(unittest.TestCase):
 def test_reports_raw_and_restored_coverage_separately(self):
  required=[{'document_version_id':'d','block_id':'b','source_character_range':[0,10]}]
  record={'record_id':'r','document_version_id':'d','source_spans':[{'block_id':'b','source_character_range':[0,5]}]}
  case={'query_id':'q','query':'免责','scope':'chunk','relevant':{'r':2},'audit':fixtures.AUDIT,'required_evidence':required}
  context={'groups':{'v':[{'document_version_id':'d','source_spans':[{'block_id':'b','source_character_range':[0,10]}]}]},'omitted':[]}
  report=evaluate([case],lambda *a:[{'record':record}],context_builder=lambda h:context)
  self.assertEqual(report['results'][0]['evidence_coverage'],0);self.assertEqual(report['results'][0]['expanded_evidence_coverage'],1)
  self.assertEqual(report['groups']['chunk']['expanded_complete_evidence_rate'],1)
