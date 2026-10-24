"""Synthetic evidence for harness tests only; no production business data."""
from insurance_rag.tools.contracts import result
class FixtureTools:
 """Synthetic exact-query fixtures never alter or release real financial records."""
 def __init__(self,release=None):
  self.release=release or {'release_id':'fixture-release','manifest':{'development_only':True}}
  self.dev=True;self.calls=0;self.versions={'fixture-version':{'product_version_id':'fixture-version','product_name':'测试保险','publication_status':'quarantined'}}
 def candidates(self,hint=None):return list(self.versions.values()) if not hint or '测试保险' in hint else []
 def availability(self):return None
 def validate_source(self,e):
  if e.get('release_id')!=self.release['release_id']:raise ValueError('fixture_release_mismatch')
 def call(self,kind,args):
  self.calls+=1
  if kind=='discover_products':return result('ok',data={'products':list(self.versions.values())})
  if kind=='rag_search':
   e={'synthetic_fixture':True,'evidence_id':'fixture-evidence','release_id':self.release['release_id'],'product_version_id':'fixture-version','product_name':'测试保险','document_version_id':'fixture-document','source_file':'fixture.pdf','source_sha256':'fixture','page_start':1,'page_end':1,'text':'第五条 保险期间：本合同的保险期间为被保险人终身。','source_spans':[],'publication_status':'quarantined','textual_clause_complete':True}
   return result('partial',evidence=[e],warnings=['synthetic_fixture'])
  raise ValueError('unregistered_agent_tool')
