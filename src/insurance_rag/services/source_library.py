"""Current-source library context; no snapshot releases or historical numeric adapters."""
from types import SimpleNamespace
from insurance_rag.parsing.common import file_hash,workspace_path
from insurance_rag.onboarding.store import IntakeStore


def current_release():
 return {'release_id':'current-source-library-v1','manifest':{'scope':'current_source_business','development_only':True},'state':'active'}

class SourceLibraryTools:
 def __init__(self,store,root):
  self.root=root;self.store=store;self.db=store.db;self.release=current_release();self.dev=True;self.m=self.release['manifest'];self.versions={}
  for row in IntakeStore(store).list('library_product',limit=500):
   if row['state']!='published':continue
   parent=IntakeStore(store).get('product_bundle',row['content']['bundle_id'])
   if parent['content'].get('scope')!='current_source_business':continue
   identity=parent['content']['identity'];self.versions[identity['product_version_id']]={**identity,'bundle_id':parent['id'],'publication_status':'source_available'}
 def candidates(self,hint=None):
  import re
  compact=lambda x:re.sub(r'[\s（）()]','',x)
  return [p for p in self.versions.values() if not hint or compact(hint) in compact(p['product_name'])]
 def capability_catalog(self):
  from insurance_rag.ingestion.readiness import current_capabilities
  out={};app=SimpleNamespace(root=self.root,store=self.store)
  for pv,p in self.versions.items():out[pv]=current_capabilities(app,IntakeStore(self.store).get('product_bundle',p['bundle_id'])['content'])
  return out
 def validate_source(self,source):
  if source.get('source_file') and file_hash(workspace_path(source['source_file'],self.root,must_exist=True))!=source['source_sha256']:raise ValueError('evidence_source_changed')
  if source.get('layout_file') and file_hash(workspace_path(source['layout_file'],self.root,must_exist=True))!=source['layout_sha256']:raise ValueError('evidence_layout_changed')
 def availability(self):return {'status':'ready','scope':'current_source_business'}
