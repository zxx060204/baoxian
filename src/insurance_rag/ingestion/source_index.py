"""Text-only current-package adapter over existing clause chunks and search components."""
from pathlib import Path
from .source_package import load_package,read_bound,reference
from insurance_rag.parsing.common import read_json,write_json,stable_hash
from insurance_rag.indexing.chunks import build_clause_chunks,ChunkPolicy
from insurance_rag.indexing.tokenization import ByteCounter
from insurance_rag.indexing.scoped_index import providers,build_scoped_index,verify_index

POLICY='current-package-clause-index-v1'
CHUNK_POLICY=ChunkPolicy(target_tokens=1200,max_body_tokens=1600,max_input_tokens=8192)


def source_chunks(root,package):
 docs={d['document_version_id']:d for d in package['source_documents']};chunks=[]
 for entry in package['documents']:
  if entry['document_type'] not in ('terms','brochure'):continue
  doc=docs[entry['document_version_id']];text=read_json(read_bound(root,entry['text_ref']));layout=read_json(read_bound(root,{'path':doc['layout_file'],'sha256':doc['layout_sha256']}))
  source_blocks={b['block_id']:b for b in layout['blocks']}
  if any(source_blocks.get(b['block_id'])!=b or b['block_type'] in ('table','image') for b in text['blocks']):raise ValueError('current_index_text_provenance_changed')
  # Content comes exclusively from text_ref; same-package layout supplies locator metadata.
  view={**layout,'blocks':text['blocks'],'tables':[]}
  rows=build_clause_chunks(view,doc['layout_sha256'],counter=ByteCounter(),policy=CHUNK_POLICY)
  for row in rows:row.update(source_file=doc['source_file'],source_sha256=doc['source_sha256'],layout_file=doc['layout_file'],company_id=doc['company_id'],document_type=doc['document_type'],source_package_id=package['package_id'],text_ref=entry['text_ref'])
  chunks.extend(rows)
 if any(len(c['content'].encode('utf8'))>8192 for c in chunks):raise ValueError('source_sentence_exceeds_embedding_limit')
 return chunks


def build_source_index(app,package_ref):
 package=load_package(app.root,package_ref);chunks=source_chunks(app.root,package);client,embedding,_=providers(app)
 scope={'source_package_id':package['package_id'],'source_package_ref':package_ref,'policy':POLICY,'chunk_policy':CHUNK_POLICY.__dict__,'chunks_hash':stable_hash(chunks)}
 folder=Path(app.root)/'data/current_source_indexes'/stable_hash([scope,embedding.binding]);folder.mkdir(parents=True,exist_ok=True)
 cp=folder/'chunks.json';write_json(cp,chunks)
 if chunks:result=build_scoped_index(client,embedding,folder,chunks,scope)
 else:result={'chunk_count':0,'state':'not_applicable','integrity':'matched'}
 result={'scope':'current_source_index','state':'ready' if chunks else 'not_applicable',**result,**scope,'chunks_ref':reference(app.root,cp)}
 path=folder/'result.json';write_json(path,result);return {**result,'result_ref':reference(app.root,path)}


def validate_index_result(root,package_ref,result_ref,*,client=None):
 package=load_package(root,package_ref);result=read_json(read_bound(root,result_ref))
 if result.get('scope')!='current_source_index' or result.get('source_package_ref')!=package_ref or result.get('source_package_id')!=package['package_id'] or result.get('policy')!=POLICY or result.get('chunk_policy')!=CHUNK_POLICY.__dict__:raise ValueError('current_index_result_source_mismatch')
 chunks=read_json(read_bound(root,result['chunks_ref']))
 if len(chunks)!=result['chunk_count'] or stable_hash(chunks)!=result['chunks_hash'] or chunks!=source_chunks(root,package):raise ValueError('current_index_chunks_changed')
 if chunks:
  scope={k:result[k] for k in ('source_package_id','source_package_ref','policy','chunk_policy','chunks_hash')}
  config=stable_hash(['source-package-index-v1',scope,result['embedding_binding']])
  if result['config_hash']!=config or result['index']!='insurance-source-'+config[:48]:raise ValueError('current_index_identity_changed')
  if client:verify_index(client,result['index'],chunks,config,result['embedding_binding'])
 return result,chunks


def retrieve_source(app,package_ref,result_ref,query,*,top_k=6):
 from insurance_rag.indexing.opensearch import SearchService
 from insurance_rag.retrieval.reranking import rerank
 from insurance_rag.retrieval.context import construct_context
 from insurance_rag.reliability.diagnostics import stage
 with stage(app.root,'source_retrieval_providers'):client,embedding,reranker=providers(app)
 with stage(app.root,'source_index_validation'):result,chunks=validate_index_result(app.root,package_ref,result_ref)
 if result['state']=='not_applicable':return {'status':'not_applicable','hits':[]}
 service=SearchService(client,{'chunk':result['index']},development=True,embedding=embedding,config_hash=result['config_hash'],workspace_root=app.root)
 package=load_package(app.root,package_ref)
 with stage(app.root,'source_search'):found=service.search(query,filters={'product_version_id':package['identity']['product_version_id']},mode='rrf',top_k=20,candidates=30)
 with stage(app.root,'source_rerank'):ranked=rerank(query,found['hits'],reranker,top_k=top_k)
 with stage(app.root,'source_context'):context=construct_context(ranked,chunks,counter=ByteCounter(),max_tokens=22000,development=True)
 return {'status':'matched' if ranked else 'not_found','hits':ranked,'context':context,'routes':found['routes'],'candidate_counts':found['candidate_counts'],'rerank_output':len(ranked),'degraded':found['degraded'],'omitted':context['omitted'],'fallbacks':context['fallbacks'],'source_package_id':package['package_id']}
