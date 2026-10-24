"""Shared providers and content-bound physical indexes, independent of old workflows."""
import os
from .opensearch import OpenSearchHTTP,index_definition,checked_bulk
from .bailian import BailianEmbedding
from .embedding import EmbeddingCache,validate_vector
from insurance_rag.retrieval.bailian_reranking import BailianReranker
from insurance_rag.parsing.common import stable_hash


def providers(app):
 return (getattr(app,'ingestion_search_client',None) or OpenSearchHTTP(os.environ.get('INSURANCE_OPENSEARCH_URL','http://127.0.0.1:9200')),
         getattr(app,'ingestion_embedding',None) or BailianEmbedding.from_environment(),
         getattr(app,'ingestion_reranker',None) or BailianReranker.from_environment())


def verify_index(client,index,chunks,config,binding):
 mappings=client.request('GET','/'+index+'/_mapping')
 if len(mappings)!=1:raise ValueError('current_index_mapping_ambiguous')
 meta=next(iter(mappings.values()))['mappings']['_meta']
 if meta.get('config_hash')!=config or meta.get('embedding_binding')!=binding:raise ValueError('current_index_binding_changed')
 if client.request('GET','/'+index+'/_count')['count']!=len(chunks):raise ValueError('current_index_count_changed')
 for start in range(0,len(chunks),50):
  batch=chunks[start:start+50];rows=client.request('POST','/'+index+'/_mget',{'ids':[r['record_id'] for r in batch]})['docs']
  if len(rows)!=len(batch):raise ValueError('current_index_record_missing')
  for row,expected in zip(rows,batch):
   body=row.get('_source',{})
   if not row.get('found') or row['_id']!=expected['record_id'] or stable_hash(body.get('record'))!=stable_hash(expected) or body.get('embedding_binding_hash')!=stable_hash(binding):raise ValueError('current_index_record_changed')
   validate_vector(body.get('embedding'),binding['dimension'])
 return {'records':len(chunks),'vectors':len(chunks),'integrity':'matched'}


def build_scoped_index(client,embedding,folder,chunks,scope):
 config=stable_hash(['source-package-index-v1',scope,embedding.binding]);index='insurance-source-'+config[:48]
 cache=EmbeddingCache(folder/'embeddings.sqlite3')
 try:vectors,usage=cache.encode([c['content'] for c in chunks],embedding)
 finally:cache.close()
 try:existing=client.request('GET','/'+index+'/_mapping')
 except RuntimeError as exc:
  if str(exc)!='opensearch_http_404':raise
  client.request('PUT','/'+index,index_definition('chunk',config_hash=config,embedding_binding=embedding.binding));existing=None
 if existing:
  meta=next(iter(existing.values()))['mappings']['_meta']
  if meta.get('config_hash')!=config or meta.get('embedding_binding')!=embedding.binding:raise ValueError('current_index_binding_conflict')
 checked_bulk(client,index,chunks,vectors=vectors,binding=embedding.binding)
 integrity=verify_index(client,index,chunks,config,embedding.binding)
 return {'index':index,'config_hash':config,'embedding_binding':embedding.binding,'chunk_count':len(chunks),'embedding_usage':usage,**integrity}
