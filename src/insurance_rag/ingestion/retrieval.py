"""Current-source retrieval with explicit evidence provenance."""
from insurance_rag.parsing.common import stable_hash


def retrieve(app,content,query,*,top_k=6):
 if content.get('scope')!='current_source_business':raise ValueError('retired_bundle_requires_migration')
 from .source_index import retrieve_source
 ref=content.get('result_refs',{}).get('index')
 if not ref:return [],{'status':'not_prepared','reason_code':'source_index_not_prepared'}
 found=retrieve_source(app,content['source_package_ref'],ref,query,top_k=top_k)
 if found['status']=='not_applicable':return [],{'status':'not_applicable','reason_code':'source_text_not_applicable'}
 return _evidence(content,found['context']),{k:v for k,v in found.items() if k not in ('hits','context')}

def _evidence(content,context):
 evidence=[]
 for rows in context['groups'].values():
  for group in rows:
   doc=next(d for d in content['documents'] if d['document_version_id']==group['document_version_id']);text=group['body'];sid=stable_hash([content['bundle_id'],group['chunk_ids'],text])
   evidence.append({'source_id':sid,'text':text,'source':{**doc,'page_number':group['page_start']},'page_start':group['page_start'],'page_end':group['page_end'],'clause_id':group['clause_id'],'clause_title':group['clause_title'],'chunk_ids':group['chunk_ids'],'source_spans':group['source_spans'],'textual_clause_complete':group['textual_clause_complete']})
 return evidence
