"""OpenSearch REST adapter with strict schema, checked bulk and atomic alias swap."""
import base64,json,os,re
from urllib.request import Request,urlopen
from urllib.parse import urlparse
from urllib.error import HTTPError
from insurance_rag.parsing.common import stable_hash,file_hash,workspace_path
from .embedding import validate_vector
from insurance_rag.reliability.runtime import bounded_timeout,check_budget,DeadlineExceeded,span
from insurance_rag.retrieval.fusion import fuse,validate_fusion
from insurance_rag.retrieval.query import validate,filter_clauses,rrf,record_matches

def name(value):
    if not isinstance(value,str) or not re.fullmatch(r'[a-z][a-z0-9_-]{0,150}',value):raise ValueError('invalid_index_name')
    return value

class OpenSearchHTTP:
    def __init__(self,endpoint='http://127.0.0.1:9200',*,timeout=15,allow_remote=False):
        url=urlparse(endpoint)
        if url.scheme not in ('http','https') or url.username or url.password or url.query or url.fragment or url.path not in ('','/'):
            raise ValueError('invalid_opensearch_endpoint')
        if url.hostname not in ('127.0.0.1','localhost','::1'):
            if not allow_remote or url.scheme!='https':raise ValueError('remote_opensearch_requires_explicit_https')
        self.endpoint=endpoint.rstrip('/');self.timeout=timeout
    def request(self,method,path,body=None,*,ndjson=False):
        check_budget()
        headers={'Content-Type':'application/x-ndjson' if ndjson else 'application/json'}
        user=os.environ.get('INSURANCE_OPENSEARCH_USER');password=os.environ.get('INSURANCE_OPENSEARCH_PASSWORD')
        if user and password:headers['Authorization']='Basic '+base64.b64encode((user+':'+password).encode()).decode()
        payload=(body.encode('utf8') if ndjson else json.dumps(body,ensure_ascii=False,allow_nan=False).encode('utf8')) if body is not None else None
        try:
            with urlopen(Request(self.endpoint+path,data=payload,method=method,headers=headers),timeout=bounded_timeout(self.timeout)) as r:
                return json.load(r)
        except HTTPError as e:
            # Do not echo URLs, credentials or potentially sensitive document bodies.
            raise RuntimeError('opensearch_http_'+str(e.code)) from None

KEYWORDS=('record_id','scope','company_id','product_id','product_version_id','document_version_id',
          'document_version_ids','document_id','document_ids','chunk_id','section_id','clause_id','document_type','document_types','insurance_type','sales_status','coverage_tags','publication_status','config_hash','embedding_binding_hash')

def index_definition(scope,*,config_hash,embedding_binding=None):
    if scope not in ('product','chunk'):raise ValueError('invalid_scope')
    props={k:{'type':'keyword'} for k in KEYWORDS}
    for k in ('content',):props[k]={'type':'text','analyzer':'cjk'}
    for field in ('page_start','page_end','chunk_index'):props[field]={'type':'integer'}
    props['age_min']={'type':'integer'};props['age_max']={'type':'integer'}
    props['record']={'type':'object','enabled':False}
    settings={'number_of_shards':1,'number_of_replicas':0}
    meta={'schema_version':2,'scope':scope,'config_hash':config_hash,'embedding_binding':embedding_binding}
    if embedding_binding:
        dimension=embedding_binding['dimension']
        if isinstance(dimension,bool) or not isinstance(dimension,int) or dimension<1:raise ValueError('invalid_embedding_dimension')
        settings['index.knn']=True
        props['summary_embedding' if scope=='product' else 'embedding']={'type':'knn_vector','dimension':dimension,'method':{
            'name':'hnsw','engine':'lucene','space_type':'cosinesimil','parameters':{'ef_construction':128,'m':16}}}
    return {'settings':settings,'mappings':{'dynamic':'strict','_meta':meta,'properties':props}}

def index_record(record,*,vector=None,binding=None):
    body={k:record[k] for k in KEYWORDS+('content','page_start','page_end','chunk_index') if k in record}
    if record.get('scope')=='chunk':
        from .schema import validate_chunk
        validate_chunk(record)
    if record.get('age_range'):
        body['age_min']=record['age_range']['min'];body['age_max']=record['age_range']['max']
    body['record']=record
    if vector is not None:
        if not binding:raise ValueError('vector_binding_missing')
        body['summary_embedding' if record['scope']=='product' else 'embedding']=validate_vector(vector,binding['dimension']);body['embedding_binding_hash']=stable_hash(binding)
    return body

def checked_bulk(client,index,records,*,vectors=None,binding=None,batch_size=100):
    name(index)
    if isinstance(batch_size,bool) or not isinstance(batch_size,int) or batch_size<1:raise ValueError('invalid_batch_size')
    if vectors is not None and len(vectors)!=len(records):raise ValueError('vector_record_count_mismatch')
    if len({r['record_id'] for r in records})!=len(records):raise ValueError('duplicate_record_id')
    for start in range(0,len(records),batch_size):
        lines=[];batch=records[start:start+batch_size]
        for j,r in enumerate(batch,start):
            lines.append(json.dumps({'index':{'_index':index,'_id':r['record_id']}}))
            lines.append(json.dumps(index_record(r,vector=vectors[j] if vectors is not None else None,binding=binding),ensure_ascii=False,allow_nan=False))
        response=client.request('POST','/_bulk','\n'.join(lines)+'\n',ndjson=True)
        items=response.get('items',[])
        if response.get('errors') or len(items)!=len(batch) or any(not 200<=x.get('index',{}).get('status',0)<300 for x in items):
            raise RuntimeError('bulk_partial_failure_no_alias_switch')
    client.request('POST','/'+index+'/_refresh')

def switch_aliases(client,targets):
    actions=[]
    for alias,index in targets.items():
        name(alias);name(index)
        actions.extend([{'remove':{'index':'*','alias':alias,'must_exist':False}}, {'add':{'index':index,'alias':alias}}])
    return client.request('POST','/_aliases',{'actions':actions})

class SearchService:
    def __init__(self,client,aliases,*,development=False,embedding=None,config_hash=None,workspace_root=None):
        self.workspace_root=workspace_root
        self.client=client;self.aliases=aliases;self.development=development;self.embedding=embedding;self.config_hash=config_hash
        for v in aliases.values():name(v)
    def _meta(self,scope):
        response=self.client.request('GET','/'+self.aliases[scope]+'/_mapping')
        metas=[v['mappings'].get('_meta',{}) for v in response.values()]
        if len(metas)!=1 or metas[0].get('scope')!=scope:raise ValueError('ambiguous_or_invalid_index_binding')
        meta=metas[0]
        if meta.get('schema_version')!=2:raise ValueError('index_schema_requires_rebuild')
        if self.config_hash and meta.get('config_hash')!=self.config_hash:raise ValueError('index_config_mismatch')
        return meta
    def search(self,query,scope='chunk',filters=None,top_k=10,mode='bm25',candidates=50,*,restrict_versions=None,rrf_k=60,dense_weight=0.5,lexical_query=None):
        query,filters=validate(query,scope,filters,top_k)
        lexical_query=validate(lexical_query,scope,filters,top_k)[0] if lexical_query is not None else query
        validate_fusion('weighted' if mode=='weighted' else 'rrf',rrf_k,dense_weight)
        if mode not in ('bm25','dense','rrf','weighted'):raise ValueError('invalid_search_mode')
        if isinstance(candidates,bool) or not isinstance(candidates,int) or not top_k<=candidates<=1000:raise ValueError('invalid_candidate_count')
        if restrict_versions is not None:
            if not isinstance(restrict_versions,list) or len(restrict_versions)>5000 or any(not isinstance(v,str) or not v.strip() for v in restrict_versions):
                raise ValueError('invalid_business_candidate_versions')
            if not restrict_versions:return {'hits':[],'routes':{'business_filter':'empty'},'mode':mode,'development_only':self.development,'degraded':False}
        with span('index_mapping'):meta=self._meta(scope)
        clauses=filter_clauses(filters,scope,development=self.development)
        if restrict_versions is not None:clauses.append({'terms':{'product_version_id':list(dict.fromkeys(restrict_versions))}})
        routes={};states={};vector=None;dense_failed=False;source_hashes={}
        def actual_hash(relative):
            if relative not in source_hashes:
                try:source_hashes[relative]=file_hash(workspace_path(relative,self.workspace_root,must_exist=True))
                except OSError:raise ValueError('result_source_or_layout_unavailable') from None
            return source_hashes[relative]
        if mode in ('dense','rrf','weighted'):
            if self.embedding is None:raise ValueError('embedding_provider_required')
            if meta.get('embedding_binding')!=self.embedding.binding:raise ValueError('query_embedding_model_mismatch')
            try:
                with span('query_embedding'):encoded=self.embedding.encode([query])
                if len(encoded)!=1:raise ValueError('query_embedding_count_mismatch')
                vector=validate_vector(encoded[0],self.embedding.binding['dimension'])
            except DeadlineExceeded:raise
            except (RuntimeError,OSError):
                if mode not in ('rrf','weighted'):raise
                dense_failed=True;states['dense']='failed';routes['dense']=[]
        for route in ('bm25','dense'):
            if mode!=route and mode not in ('rrf','weighted'):continue
            if route=='dense' and dense_failed:continue
            q=({'bool':{'must':[{'multi_match':{'query':lexical_query,'fields':['content']}}], 'filter':clauses}}
               if route=='bm25' else {'knn':{('summary_embedding' if scope=='product' else 'embedding'):{'vector':vector,'k':candidates,'filter':{'bool':{'filter':clauses}}}}})
            try:
                with span(route):
                    response=self.client.request('POST','/'+self.aliases[scope]+'/_search',
                        {'size':candidates,'query':q,'_source':['record']})
                if response.get('timed_out') or response.get('_shards',{}).get('failed',0):raise RuntimeError('search_incomplete')
                hits=[]
                for h in response['hits']['hits']:
                    r=h['_source']['record']
                    if r['scope']!=scope or (not self.development and r['publication_status']!='eligible'):
                        raise ValueError('index_publication_isolation_violation')
                    if restrict_versions is not None and r.get('product_version_id') not in restrict_versions:raise ValueError('business_candidate_isolation_violation')
                    if not record_matches(r,filters,scope):raise ValueError('index_filter_isolation_violation')
                    if not self.development and self.workspace_root is None:
                        raise ValueError('workspace_root_required_for_published_results')
                    if self.workspace_root is not None:
                        sources=r.get('sources',[]) if scope=='product' else [r]
                        if not sources:raise ValueError('result_source_missing')
                        for source in sources:
                            if actual_hash(source['source_file'])!=source['source_sha256']:
                                raise ValueError('result_source_changed')
                        if scope=='chunk' and actual_hash(r['layout_file'])!=r['layout_sha256']:
                            raise ValueError('result_layout_changed')
                    hits.append({'record':r,'score':h['_score'],'ranks':{}})
                routes[route]=hits;states[route]='ok' if hits else 'empty'
            except (RuntimeError,OSError):
                if mode not in ('rrf','weighted'):raise
                states[route]='failed';routes[route]=[]
        if all(s=='failed' for s in states.values()):raise RuntimeError('all_search_routes_failed')
        fusion=fuse({r:h for r,h in routes.items() if states[r]!='failed'},method=mode,rrf_k=rrf_k,dense_weight=dense_weight,top_k=top_k) if mode in ('rrf','weighted') else {}
        return {**fusion,'hits':fusion['hits'] if fusion else next(iter(routes.values()))[:top_k],
                'candidate_counts':{**{r:len(h) for r,h in routes.items()},'unique_candidates':len({h['record']['record_id'] for rows in routes.values() for h in rows}),'fusion_output':len(fusion['hits']) if fusion else 0},'routes':states,'mode':mode,'development_only':self.development,'degraded':any(s=='failed' for s in states.values())}
