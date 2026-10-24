import copy,unittest
from types import SimpleNamespace
from unittest.mock import Mock
from insurance_rag.ingestion.source_rules import extract_rule_target,prepare_rules,validate_rules
from insurance_rag.ingestion.source_rule_policy import lookup_kind
from insurance_rag.ingestion.source_package import reference
from insurance_rag.parsing.common import read_json,write_json
from test_current_source_rules import target,sources,proposal
import test_current_source_rules as fixtures

def named(name):return {**target(),'title':name,'header_path':[name]}

class AcquisitionPolicyTests(unittest.TestCase):
 def test_explicit_lookup_labels_skip_model_and_search(self):
  model=Mock();retrieve=Mock()
  for name,table in [('保费','premium_rate'),('年初保费','premium_rate'),('当年度\n保险费','premium_rate'),('年交保险费','premium_rate'),('当年度末现金价值','cash_value'),('退保金（现金价值）','cash_value')]:
   with self.subTest(name=name):
    result=extract_rule_target(named(name),[],model,retrieve=retrieve)
    self.assertEqual(result['state'],'rule_extracted');self.assertEqual(result['rule']['type'],'exact_table_lookup');self.assertEqual(result['rule']['table_type'],table);self.assertEqual((result['model_calls'],result['rag_calls']),(0,0))
  model.structured.assert_not_called();retrieve.assert_not_called()
 def test_cumulative_and_composite_labels_do_not_enter_raw_lookup(self):
  for name in ('累计保费','累积保险费','累计保险费','保费豁免金','保费返还保险金','现金价值与保险金之和','累计现金价值'):
   self.assertIsNone(lookup_kind(named(name)))
  result=extract_rule_target(named('累计保费'),sources(),SimpleNamespace(structured=lambda s,p:proposal(p)))
  self.assertEqual(result['rule']['type'],'formula');self.assertEqual(result['model_calls'],1)
 def test_surrender_default_is_conditional_and_retains_special_provisions(self):
  evidence=sources('解除合同时需扣除欠交保费和借款。');model=Mock()
  result=extract_rule_target(named('退保金'),evidence,model)
  self.assertEqual(result['rule']['basis'],'system_default');self.assertEqual(result['rule']['applicability'],'no_special_provisions');self.assertIn('特殊约定',result['rule']['guidance']);self.assertEqual(result['source_catalog'],evidence);self.assertEqual(result['rule']['context_source_ids'],['fresh']);model.structured.assert_not_called()
 def test_non_dividend_benefit_missing_formula_gets_program_reference_after_one_search(self):
  model=Mock();model.structured.return_value={'formulas':[]};retrieve=Mock(return_value=sources())
  result=extract_rule_target(named('护理保险金'),sources(),model,retrieve=retrieve)
  self.assertEqual(result['rule']['type'],'illustration_reference');self.assertEqual(result['formula_state'],'evidence_insufficient');self.assertEqual(result['state'],'rule_extracted');self.assertEqual((result['model_calls'],result['rag_calls']),(2,1));self.assertIn('不得',result['rule']['guidance'])
 def test_dividend_with_supported_formula_stays_formula(self):
  result=extract_rule_target(named('累积红利'),sources(),SimpleNamespace(structured=lambda s,p:proposal(p)))
  self.assertEqual(result['rule']['type'],'formula')
 def test_no_current_evidence_returns_reference_without_inventing_formula(self):
  model=Mock();result=extract_rule_target(named('其他给付'),[],model)
  self.assertEqual(result['rule']['type'],'illustration_reference');self.assertEqual(result['model_calls'],0);self.assertEqual(result['rule']['missing'],['no_current_source_evidence']);model.structured.assert_not_called()
 def test_invalid_candidate_or_service_failure_never_becomes_reference(self):
  for response in ({'formulas':[],'rule':{'type':'exact_table_lookup'}},{'wrong':[]},RuntimeError('deepseek_http_402')):
   model=Mock()
   if isinstance(response,Exception):model.structured.side_effect=response
   else:model.structured.return_value=response
   retrieve=Mock();result=extract_rule_target(named('其他给付'),sources(),model,retrieve=retrieve)
   self.assertNotIn('rule',result);self.assertIn(result['state'],('invalid_output','provider_error'));retrieve.assert_not_called()
 def test_identity_and_scenario_do_not_change_lookup_method(self):
  baseline=lookup_kind(named('年初保费'))
  for identity in ('arbitrary-a','arbitrary-b'):
   t={**named('年初保费'),'document_version_id':identity,'company_name':identity,'product_name':identity,'header_path':['年初保费','保证利益演示']}
   self.assertEqual(lookup_kind(t),baseline)

class AcquisitionPersistenceTests(unittest.TestCase):
 setUp=fixtures.CurrentExampleTests.setUp
 tearDown=fixtures.CurrentExampleTests.tearDown
 shifted=fixtures.CurrentExampleTests.shifted
 def package(self):
  ref=fixtures.CurrentExampleTests.package(self);from insurance_rag.ingestion.source_package import load_package
  package=load_package(self.root,ref);doc=package['source_documents'][0];data=read_json(self.root/doc['layout_file'])
  for table in data['tables']:
   if table['table_id'].startswith('annual'):
    for cell in table['cells']:
     if cell['raw_text']=='给付':cell['raw_text']='年初保费'
  write_json(self.root/doc['layout_file'],data);from insurance_rag.parsing.common import file_hash
  doc['layout_sha256']=file_hash(self.root/doc['layout_file'])
  from insurance_rag.ingestion.mapping import prepare_mapping
  return prepare_mapping(self.app,None,{'identity':{},'documents':[doc]},self.root)['package_ref']
 def test_lookup_only_preparation_needs_no_model_store_or_database(self):
  ref=self.package();result=prepare_rules(SimpleNamespace(root=self.root),ref)
  self.assertEqual(result['versions']['model'],{'provider':'not_invoked'});self.assertFalse(result['availability_checks']);self.assertEqual(result['model_calls'],0);self.assertTrue(all(r['rule']['type']=='exact_table_lookup' for r in result['rules']));validate_rules(self.root,ref,result['result_ref'])
  changed=read_json(self.root/result['result_ref']['path']);changed['rules'][0]['rule']['table_type']='cash_value';p=self.root/'changed-rule.json';write_json(p,changed)
  with self.assertRaisesRegex(ValueError,'current_acquisition_rule_changed'):validate_rules(self.root,ref,reference(self.root,p))
  changed=read_json(self.root/result['result_ref']['path']);changed['versions']['policy']='current-source-column-formulas-v1';write_json(p,changed)
  with self.assertRaisesRegex(ValueError,'current_rule_source_mismatch'):validate_rules(self.root,ref,reference(self.root,p))

 def test_reference_result_replays_evidence_and_rejects_relabeling(self):
  ref=fixtures.CurrentExampleTests.package(self);model=Mock(binding={'provider':'fixture'});model.structured.return_value={'formulas':[]}
  result=prepare_rules(SimpleNamespace(root=self.root,model=model),ref);validate_rules(self.root,ref,result['result_ref'])
  self.assertTrue(all(r['rule']['type']=='illustration_reference' for r in result['rules']))
  changed=read_json(self.root/result['result_ref']['path']);changed['rules'][0]['rule']['type']='exact_table_lookup';p=self.root/'fake-method.json';write_json(p,changed)
  with self.assertRaisesRegex(ValueError,'current_acquisition_rule_changed'):validate_rules(self.root,ref,reference(self.root,p))
