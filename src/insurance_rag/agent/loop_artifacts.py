"""Read and revalidate current or historical evidence and report artifacts without a graph."""
import json
from insurance_rag.parsing.common import stable_hash,workspace_path,file_hash
from insurance_rag.agent.store import Conflict,Forbidden
class ArtifactAccess:
 def _execution_value(self,value):
  if '_audit' not in value and str(value.get('id','')).startswith(('benefit-table:','table-values:')):
   # Earlier current-source answers omitted audit fields; recover only an identical immutable tool result.
   matched=None
   for encoded, in self.store.execute('SELECT package FROM insurance_agent_evidence_package WHERE package LIKE ?',('%'+value['id']+'%',)).fetchall():
    packet=json.loads(encoded)
    for key in ('benefit_table','table_values'):
     candidate=packet.get('data',{}).get(key)
     if candidate and {k:v for k,v in candidate.items() if k!='_audit'}==value:matched=candidate;break
    if matched:break
   if not matched:raise Conflict('historical_source_result_unavailable')
   value=matched
  return value
 def validate_calculation_result(self,data,release):
  from insurance_rag.ingestion.source_execution import validate_result,load_execution
  from insurance_rag.ingestion.store import bundle
  from insurance_rag.onboarding.store import IntakeStore
  current=[data[key] for key in ('benefit_table','table_values') if data.get(key)]+data.get('benefit_tables',[])
  for value in current:
   value=self._execution_value(value)
   if value.get('_audit',{}).get('scope')!='current_source_execution':raise Conflict('retired_execution_result')
   parent=IntakeStore(self.store).get('product_bundle',value['bundle_id'])
   content=bundle(self,parent['owner_id'],value['bundle_id'])['content']
   validate_result(value,content,app=self);load_execution(self,content)
  if any(data.get(k) for k in ('illustration','official_illustration','calculation','matrix','screening','reference_analysis','reports','numeric')):raise Conflict('retired_business_result')
 def evidence(self,pid,owner):
  packet=self.store.read_package(pid,owner);rid=self.store.execute('SELECT run_id FROM insurance_agent_evidence_package WHERE package_id=?',(pid,)).fetchone()[0];run=self.store.get(rid,owner);tools=self.tools_factory(run['release'])
  self.validate_calculation_result(run.get('result') or {},run['release'])
  for e in packet.get('evidence',[]):tools.validate_source(e)
  if any(packet.get(k) for k in ('numeric_sources','calculation_rules','calculation_sources')):raise Conflict('retired_business_evidence')
  allowed=('evidence_id','release_id','product_version_id','product_name','document_version_id','source_sha256','layout_sha256','text','page_start','page_end','clause_id','publication_status','textual_clause_complete','restoration_mode','neighbors_before','neighbors_after')
  return {'package_id':pid,'evidence':[{k:v for k,v in e.items() if k in allowed} for e in packet.get('evidence',[])]}
 def _source_entries(self,pid,owner):
  self.evidence(pid,owner);packet=self.store.read_package(pid,owner);entries={}
  def add(kind,source,metadata):
   if not source.get('file_path') or not source.get('file_sha256'):return
   if file_hash(workspace_path(source['file_path'],self.root,must_exist=True))!=source['file_sha256']:raise Conflict('source_changed')
   sid=stable_hash([kind,source['file_sha256'],source['page_number'],metadata]);entries[sid]={'source_id':sid,'kind':kind,'page_number':source['page_number'],'metadata':metadata,'source':source}
  for evidence in packet.get('evidence',[]):
   add('document',{'file_path':evidence.get('source_file'),'file_sha256':evidence.get('source_sha256'),'page_number':evidence.get('page_start',1)},{'evidence_id':evidence['evidence_id'],'document_version_id':evidence.get('document_version_id')})
  rid=self.store.execute('SELECT run_id FROM insurance_agent_evidence_package WHERE package_id=?',(pid,)).fetchone()[0];data=self.store.get(rid,owner).get('result') or {};values=[data['table_values']] if data.get('table_values') else []
  tables=data.get('benefit_tables',[]) or ([data['benefit_table']] if data.get('benefit_table') else [])
  for table in tables:
   table=self._execution_value(table)
   for ref in table.get('_audit',{}).get('value_refs',[]):
    if ref.startswith('table-values:'):
     found=[json.loads(raw).get('data',{}).get('table_values') for raw, in self.store.execute('SELECT package FROM insurance_agent_evidence_package WHERE package LIKE ?',('%'+ref+'%',)).fetchall()]
     value=next((v for v in found if v and v.get('id')==ref),None)
    else:value=self.store.read_package(ref,owner).get('data',{}).get('table_values')
    if value:values.append(value)
  from insurance_rag.ingestion.store import bundle
  from insurance_rag.ingestion.source_execution import validate_result
  for value in values:
   value=self._execution_value(value)
   content=bundle(self,owner,value['bundle_id'])['content'];validate_result(value,content,app=self);docs={d['document_version_id']:d for d in content['documents']}
   for year,sources in value.get('sources',{}).items():
    for cell in sources:
     doc=docs[cell['document_version_id']];add('source_cell',{'file_path':doc['source_file'],'file_sha256':doc['source_sha256'],'page_number':cell['page_number']},{'document_version_id':cell['document_version_id'],'cell_id':cell['cell_id'],'row_index':cell['row_index'],'column_index':cell['column_index'],'policy_year':year})
  return entries
 def source_catalog(self,pid,owner):return {'package_id':pid,'sources':[{k:v for k,v in entry.items() if k!='source'} for entry in self._source_entries(pid,owner).values()]}
 def source_file(self,pid,sid,owner):
  entry=self._source_entries(pid,owner).get(sid)
  if not entry:raise Forbidden('source_unavailable')
  path=workspace_path(entry['source']['file_path'],self.root,must_exist=True)
  if path.suffix.lower()!='.pdf' or file_hash(path)!=entry['source']['file_sha256']:raise Conflict('source_changed')
  return path
 def result(self,rid,owner):
  run=self.store.get(rid,owner);reference=(run.get('result') or {}).get('workflow_result_id')
  if not reference:raise ValueError('workflow_result_not_ready')
  stored=self.workflow_store.read_result(reference,owner);self.validate_calculation_result(stored['content'],run['release'])
  if stored['content'].get('evidence_package_id'):self.evidence(stored['content']['evidence_package_id'],owner)
  from insurance_rag.agent.presentation import display_result
  return {**stored,'content':display_result(stored['content'])}
 def public_events(self,run,events):return [{**e,'data':self.public({**run,'result':e['data']})['result']} if e['event']=='run_completed' else e for e in events]
