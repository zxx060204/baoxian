"""Current, content-bound source package; historical business bundles are not inputs."""
from pathlib import Path
from contextlib import contextmanager
from contextvars import ContextVar
from insurance_rag.parsing.common import file_hash,read_json,stable_hash,workspace_path
from insurance_rag.parsing.models import PARSER_VERSION
from insurance_rag.parsing.table_contract import POLICY as TABLE_POLICY
from insurance_rag.parsing.generic_mapping import POLICY as MAPPING_POLICY

SCHEMA=2
TEXT_POLICY='source-text-blocks-v1'
PREPARATION_POLICY='source-preparation-v2:'+stable_hash([SCHEMA,PARSER_VERSION,TABLE_POLICY,MAPPING_POLICY,TEXT_POLICY])[:16]

def policies():
 return {'parser':PARSER_VERSION,'physical_tables':TABLE_POLICY,'mapping':MAPPING_POLICY,'text':TEXT_POLICY}

def check_layout(layout,doc):
 if layout.get('parser_version')!=PARSER_VERSION or layout.get('table_preparation_policy')!=TABLE_POLICY:raise ValueError('current_source_parse_required')
 if layout.get('document_version_id')!=doc['document_version_id'] or layout.get('metadata',{}).get('document_type')!=doc['document_type']:raise ValueError('source_document_identity_changed')
 if any(t.get('continuation_of') or t.get('inherited_headers') for t in layout.get('tables',[])):raise ValueError('physical_layout_contains_logical_mapping')

def package_id(documents,identity):
 keys=('document_type','document_version_id','source_sha256','layout_sha256','source_file','layout_file')
 rows=sorted([{k:d[k] for k in keys} for d in documents],key=lambda d:(d['document_version_id'],d['document_type']))
 if not rows or len({d['document_version_id'] for d in rows})!=len(rows):raise ValueError('unique_source_documents_required')
 return 'source-package:'+stable_hash([SCHEMA,policies(),identity,rows])

def reference(root,path):
 return {'path':Path(path).relative_to(root).as_posix(),'sha256':file_hash(path)}

_bound_reads=ContextVar('source_bound_reads',default=None)

@contextmanager
def bound_reads():
 """Capture the complete transitive manifest verified by existing validators."""
 prior=_bound_reads.get();manifest={};token=_bound_reads.set(manifest)
 try:yield manifest
 finally:
  _bound_reads.reset(token)
  if prior is not None:prior.update(manifest)

def read_bound(root,ref,code='source_artifact_changed'):
 path=workspace_path(ref['path'],root,must_exist=True)
 if file_hash(path)!=ref['sha256']:raise ValueError(code)
 manifest=_bound_reads.get()
 if manifest is not None:manifest[str(path)]={'path':ref['path'],'sha256':ref['sha256']}
 return path

def load_package(root,ref):
 """Verify every input/output edge before any consumer performs a side effect."""
 root=Path(root);p=read_bound(root,ref);report=read_json(p)
 if report.get('schema_version')!=SCHEMA or report.get('scope')!='source_relations' or report.get('policies')!=policies():raise ValueError('current_source_package_required')
 if report['package_id']!=package_id(report['source_documents'],report['identity']):raise ValueError('source_package_identity_changed')
 if any(k in report for k in ('bindings','numeric_batch_id','retrieval','metrics')):raise ValueError('business_payload_in_source_package')
 inputs={d['document_version_id']:d for d in report['source_documents']}
 if len(report['documents'])!=len(inputs) or {d['document_version_id'] for d in report['documents']}!=set(inputs):raise ValueError('source_package_documents_changed')
 total=0
 for mapped in report['documents']:
  doc=inputs[mapped['document_version_id']]
  if mapped['document_type']!=doc['document_type']:raise ValueError('source_document_identity_changed')
  read_bound(root,{'path':doc['source_file'],'sha256':doc['source_sha256']});lp=read_bound(root,{'path':doc['layout_file'],'sha256':doc['layout_sha256']});layout=read_json(lp);check_layout(layout,doc)
  model=read_json(read_bound(root,mapped['model_ref']));text=read_json(read_bound(root,mapped['text_ref']))
  if model.get('policy')!=MAPPING_POLICY or model.get('layout_sha256')!=doc['layout_sha256'] or model.get('document_version_id')!=doc['document_version_id']:raise ValueError('source_model_binding_changed')
  read_bound(root,model['records_ref'])
  if text.get('layout_sha256')!=doc['layout_sha256'] or text.get('document_version_id')!=doc['document_version_id'] or text.get('policy')!=TEXT_POLICY:raise ValueError('source_text_binding_changed')
  if any(b.get('block_type') in ('table','image') for b in text['blocks']) or doc['document_type'] in ('premium_rate','cash_value') and text['blocks']:raise ValueError('invalid_source_text_payload')
  if mapped['state']!=model['status'] or mapped['record_count']!=model['record_count']:raise ValueError('source_mapping_summary_changed')
  total+=mapped['record_count']
 expected='partial' if any(d['state']=='partial' for d in report['documents']) else 'mapped'
 if report['record_count']!=total or report['structure_status']!=expected:raise ValueError('source_package_summary_changed')
 return report
