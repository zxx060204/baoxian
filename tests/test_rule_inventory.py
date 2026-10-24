import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from insurance_rag.parsing.common import write_json,file_hash

class RuleInventoryTests(unittest.TestCase):
 def source(self,i,text):return {'source_id':str(i),'text':text,'source':{'document_version_id':'doc','source_sha256':'hash','page_number':1}}
 def test_original_page_offsets_survive_long_source_split(self):
  from insurance_rag.ingestion.evidence import literal
  source=self.source(1,'前言。等待期为90天。');source['page_character_range']=[120,132]
  result=literal({'source_id':'1','quote':'等待期为90天。'},{'1':source})
  self.assertEqual(result['page_character_range'],[123,131])
if __name__=='__main__':unittest.main()
