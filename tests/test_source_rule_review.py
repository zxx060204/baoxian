import unittest,copy
from types import SimpleNamespace
from unittest.mock import Mock
import test_current_source_rules as fixtures
from insurance_rag.ingestion.source_rules import extract_target,prepare_rules,validate_rules
from insurance_rag.ingestion.source_package import reference
from insurance_rag.parsing.common import read_json,write_json

def valid(payload):return fixtures.proposal(payload,tree={'type':'parameter','name':'unregistered_amount'})

class SingleReviewTests(unittest.TestCase):
 def test_unit_error_feedback_identifies_path_and_correct_variable_usage(self):
  calls=[]
  def invoke(system,payload):
   calls.append(payload);raw=valid(payload);f=raw['formulas'][0];eid=payload['source_catalog']['evidence'][0][0]
   for name,unit in [('age','year'),('boundary','date'),('event','date')]:f['variables'].append({'name':name,'unit':unit,'meaning':name,'source_kind':'input','evidence':[{'evidence_id':eid}]})
   f['applicability']={'type':'compare','parameter':'age' if len(calls)==1 else 'event','operator':'lt','right_parameter':'boundary'}
   return raw
  r=extract_target(fixtures.target(),fixtures.sources(),SimpleNamespace(structured=invoke));self.assertEqual(r['state'],'deterministic_checks_passed');self.assertEqual(r['review_calls'],1)
  review=calls[1]['validation_review'];conflict=review['validation_error']['unit_conflicts'][0];self.assertEqual(conflict['path'],'formulas[0].applicability');self.assertEqual((conflict['left']['unit'],conflict['right']['unit']),('year','date'))
  self.assertEqual(review['previous_output']['formulas'][0]['applicability']['parameter'],'age')
  for p in calls:self.assertNotIn('column_format_reference',p);self.assertNotIn('basic_unit_reference',p)
 def test_repeat_invalid_stops_after_one_review_without_rag(self):
  model=Mock();model.structured.return_value={'wrong':[]};retrieve=Mock();r=extract_target(fixtures.target(),fixtures.sources(),model,retrieve=retrieve)
  self.assertEqual((r['model_calls'],r['review_calls'],r['rag_calls']),(2,1,0));self.assertEqual(r['state'],'invalid_output');retrieve.assert_not_called()
 def test_rag_then_review_uses_at_most_three_calls(self):
  count=0
  def invoke(system,payload):
   nonlocal count
   count+=1
   return {'formulas':[]} if count==1 else {'wrong':[]} if count==2 else valid(payload)
  r=extract_target(fixtures.target(),fixtures.sources(),SimpleNamespace(structured=invoke),retrieve=lambda *a:fixtures.sources())
  self.assertEqual((r['model_calls'],r['rag_calls'],r['review_calls']),(3,1,1));self.assertEqual(r['state'],'deterministic_checks_passed')
 def test_review_then_rag_cannot_start_another_review(self):
  count=0
  def invoke(system,payload):
   nonlocal count
   count+=1
   return {'formulas':[]} if count==2 else {'wrong':[]}
  r=extract_target(fixtures.target(),fixtures.sources(),SimpleNamespace(structured=invoke),retrieve=lambda *a:fixtures.sources())
  self.assertEqual((r['model_calls'],r['rag_calls'],r['review_calls']),(3,1,1));self.assertEqual(r['state'],'invalid_output')
 def test_model_json_error_can_regenerate_once(self):
  count=0
  def invoke(system,payload):
   nonlocal count
   count+=1
   if count==1:raise ValueError('invalid_chat_response')
   return valid(payload)
  r=extract_target(fixtures.target(),fixtures.sources(),SimpleNamespace(structured=invoke));self.assertEqual(r['state'],'deterministic_checks_passed');self.assertEqual(r['review_calls'],1)
 def test_provider_error_does_not_trigger_review(self):
  model=Mock();model.structured.side_effect=RuntimeError('deepseek_connection_failed');r=extract_target(fixtures.target(),fixtures.sources(),model)
  self.assertEqual(r['state'],'provider_error');self.assertEqual((r['model_calls'],r['review_calls']),(1,0))

class ReviewPersistenceTests(unittest.TestCase):
 setUp=fixtures.CurrentExampleTests.setUp
 tearDown=fixtures.CurrentExampleTests.tearDown
 shifted=fixtures.CurrentExampleTests.shifted
 package=fixtures.CurrentExampleTests.package
 def test_review_provenance_and_feedback_tampering_are_checked(self):
  ref=self.package();counts={}
  def invoke(system,payload):
   key=payload['target']['target_id'];counts[key]=counts.get(key,0)+1
   return {'wrong':[]} if counts[key]==1 else valid(payload)
  self.app.model=SimpleNamespace(binding={'provider':'fixture'},structured=invoke);r=prepare_rules(self.app,ref);validate_rules(self.root,ref,r['result_ref']);self.assertEqual(r['review_calls'],2)
  changed=read_json(self.root/r['result_ref']['path']);changed['rules'][0]['reviews'][0]['validation_error']['code']='fake';p=self.root/'changed-review.json';write_json(p,changed)
  with self.assertRaisesRegex(ValueError,'current_review_(feedback|error)_changed'):validate_rules(self.root,ref,reference(self.root,p))
