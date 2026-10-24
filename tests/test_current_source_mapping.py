import unittest
from types import SimpleNamespace
from unittest.mock import patch,Mock
from fractions import Fraction
from decimal import Decimal
from insurance_rag.tools.adapters.product_source_values import SourceRequest

class CurrentSourceMappingTests(unittest.TestCase):
 def test_source_query_requires_schema_and_axes(self):
  with self.assertRaises(ValueError):SourceRequest(bundle_id='b',action='query')




 def test_table_artifact_alias_is_limited_to_current_owned_results(self):
  from insurance_rag.agent.engine import AgentEngine
  from insurance_rag.agent.store import Forbidden
  packet={'data':{'table_values':{'id':'table-values:known'}}}
  store=SimpleNamespace(read_package=Mock(return_value=packet))
  app=SimpleNamespace(store=store)
  context={'owner':'owner-a','state':{'conditions_revision':2,'refs':{'outer':{'revision':2,'package_id':'package','tool':'query_product_values'}}},'tools':Mock()}
  with patch('insurance_rag.tools.adapters.product_table_steps.validate_packet'):
   self.assertEqual(AgentEngine.packet(app,'table-values:known',context),packet)
   store.read_package.assert_called_with('package','owner-a')
   with self.assertRaises(Forbidden):AgentEngine.packet(app,'table-values:unknown',context)
   context['state']['conditions_revision']=3
   with self.assertRaises(Forbidden):AgentEngine.packet(app,'table-values:known',context)
