import unittest
from insurance_rag.agent.presentation import display_quote,context_notice
from insurance_rag.agent.engine import AgentEngine
from types import SimpleNamespace

class PresentationTests(unittest.TestCase):
 def test_pdf_words_rejoined_without_changing_digits(self):
  self.assertEqual(display_quote('首次发生并经确\n诊，按所交保险费（不计利息）给付重\n大疾病保险金。'),'首次发生并经确诊，按所交保险费（不计利息）给付重大疾病保险金。')
 def test_paragraphs_headings_and_lists_retained(self):
  text='第六条 保险责任\n一、重大疾病保险金\n第一段。\n\n第二段。\n二、身故保险金'
  self.assertEqual(display_quote(text),text)
 def test_english_words_separated(self):
  self.assertEqual(display_quote('policy\nvalue'),'policy value')
 def test_public_display_does_not_mutate_persisted_result(self):
  run={k:None for k in ('run_id','thread_id','request_id','state','revision','budget','result','clarification','error_code','created_at','updated_at')}
  run['result']={'text':'确\n诊','claims':[{'quote':'确\n诊','quote_range':[0,3]}]}
  run.update(release={},bundle={'runtime_version':'native-loop-1'})
  view=AgentEngine.public(SimpleNamespace(validate_calculation_result=lambda *args:None),run)
  self.assertEqual(view['result']['display_text'],'确诊');self.assertNotIn('display_text',run['result'])
  self.assertEqual(view['result']['claims'][0]['quote'],'确\n诊')
 def test_partial_and_omitted_notices_are_distinct(self):
  packet={'data':{'context_recovery':{'fallbacks':[{'clause_title':'第五条','neighbors_before':1,'neighbors_after':2}],'omitted':[]}}}
  notice=context_notice(packet)
  self.assertIn('前1块、后2块',notice);self.assertIn('未包含完整条款',notice);self.assertNotIn('另有条款',notice)
  packet['data']['context_recovery']['omitted']=[{'reason':'budget'}]
  self.assertIn('另有条款',context_notice(packet))
