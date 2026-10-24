"""Bailian OpenAI-compatible dense embeddings; credentials never enter bindings/reports."""
import base64,json,math,os,struct,time
from urllib.request import Request,build_opener,HTTPRedirectHandler
from urllib.parse import urlparse
from urllib.error import HTTPError,URLError
from insurance_rag.parsing.common import stable_hash
from .embedding import validate_vector
from insurance_rag.reliability.runtime import bounded_timeout,check_budget,retry_sleep,retry_delay,provider_policy,DependencyUnavailable

class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

class BailianEmbedding:
    def __init__(self,base_url,api_key,model,dimension=1024,*,timeout=30,retries=2,
                 max_input_bytes=8192,max_batch_bytes=8192,batch_size=10,opener=None,sleep=time.sleep):
        url=urlparse(base_url)
        if (url.scheme!='https' or not url.hostname or not url.hostname.endswith('.aliyuncs.com') or
            url.username or url.password or url.port not in (None,443) or url.query or url.fragment or
            url.path.rstrip('/')!='/compatible-mode/v1'):
            raise ValueError('invalid_bailian_base_url')
        if not isinstance(api_key,str) or not api_key.strip() or any(c.isspace() for c in api_key):raise ValueError('bailian_api_key_missing_or_invalid')
        if not isinstance(model,str) or not model.strip() or len(model)>200:raise ValueError('invalid_embedding_model')
        for value in (dimension,max_input_bytes,max_batch_bytes,batch_size):
            if isinstance(value,bool) or not isinstance(value,int) or value<1:raise ValueError('invalid_embedding_limits')
        if not 1<=batch_size<=10 or max_batch_bytes<max_input_bytes:raise ValueError('invalid_embedding_batch_limit')
        if isinstance(retries,bool) or not isinstance(retries,int) or not 0<=retries<=5:raise ValueError('invalid_embedding_retries')
        if isinstance(timeout,bool) or not isinstance(timeout,(int,float)) or not math.isfinite(timeout) or not 0<timeout<=120:raise ValueError('invalid_embedding_timeout')
        self.endpoint=base_url.rstrip('/')+'/embeddings';self._api_key=api_key;self.model=model;self.dimension=dimension
        self.timeout=timeout;self.retries=retries;self.max_input_bytes=max_input_bytes;self.max_batch_bytes=max_batch_bytes;self.batch_size=batch_size
        self.opener=opener or build_opener(NoRedirect());self.sleep=sleep
        self.policy=provider_policy(stable_hash([base_url,model]))
        self.binding={'provider':'aliyun_bailian_openai_compatible','model':model,'revision':'configured_model_alias',
            'endpoint_hash':stable_hash(base_url.rstrip('/')),'dimension':dimension,'normalize':True,
            'prompt_policy':'provider_default_symmetric_v1','input_guard':'utf8_bytes_conservative_v1',
            'max_input_bytes':max_input_bytes,'adapter_version':1}
        self.stats={'requests':0,'retries':0,'input_texts':0,'prompt_tokens':0,'usage_responses':0,'elapsed_seconds':0.0}
    @classmethod
    def from_environment(cls):
        try:dimension=int(os.environ.get('DASHSCOPE_EMBEDDING_DIMENSION','1024'))
        except ValueError:raise ValueError('invalid_embedding_dimension') from None
        provider=cls(os.environ.get('DASHSCOPE_BASE_URL',''),os.environ.get('DASHSCOPE_API_KEY',''),
                   os.environ.get('DASHSCOPE_EMBEDDING_MODEL','text-embedding-v4'),dimension)
        from insurance_rag.reliability.shared_policy import SharedDependencyPolicy
        from pathlib import Path
        provider.policy=SharedDependencyPolicy(Path(__file__).resolve().parents[3]/'data/ops/dependencies.sqlite3',stable_hash([provider.endpoint,provider.model]))
        return provider
    def _request(self,texts):
        return self._request_payload({'model':self.model,'input':texts,'dimensions':self.dimension,'encoding_format':'float'})
    def _request_payload(self,body):
        with self.policy.call():return self._request_payload_unlimited(body)
    def _request_payload_unlimited(self,body):
        payload=json.dumps(body,ensure_ascii=False).encode('utf8')
        for attempt in range(self.retries+1):
            check_budget()
            request=Request(self.endpoint,data=payload,headers={'Content-Type':'application/json','Authorization':'Bearer '+self._api_key},method='POST')
            self.stats['requests']+=1
            try:
                with self.opener.open(request,timeout=bounded_timeout(self.timeout)) as response:
                    raw=response.read(16*1024*1024+1)
                if len(raw)>16*1024*1024:raise ValueError('embedding_response_too_large')
                try:return json.loads(raw)
                except (ValueError,UnicodeError):raise ValueError('embedding_response_invalid_json') from None
            except HTTPError as error:
                code=error.code
                retry_after=error.headers.get('Retry-After') if error.headers else None
                error.close()
                if code not in (408,429,500,502,503,504) or attempt==self.retries:
                    raise RuntimeError('bailian_http_'+str(code)) from None
                delay=retry_delay(attempt,retry_after)
                if delay>30:raise DependencyUnavailable('retry_after_requires_deferred_task',retry_after=delay) from None
            except (URLError,TimeoutError,OSError):
                if attempt==self.retries:raise RuntimeError('bailian_connection_failed') from None
                delay=retry_delay(attempt)
            self.stats['retries']+=1;retry_sleep(delay,self.sleep)
    def _vectors(self,response,count):
        if not isinstance(response,dict) or not isinstance(response.get('data'),list) or len(response['data'])!=count:
            raise ValueError('embedding_response_count_mismatch')
        if response.get('model') and response['model']!=self.model:raise ValueError('embedding_response_model_mismatch')
        ordered=[None]*count
        for row in response['data']:
            if not isinstance(row,dict):raise ValueError('embedding_response_invalid_item')
            index=row.get('index')
            if isinstance(index,bool) or not isinstance(index,int) or not 0<=index<count or ordered[index] is not None:
                raise ValueError('embedding_response_index_mismatch')
            vector=row.get('embedding')
            if isinstance(vector,str):
                try:
                    raw=base64.b64decode(vector,validate=True)
                    if len(raw)!=4*self.dimension:raise ValueError()
                    vector=list(struct.unpack('<'+str(self.dimension)+'f',raw))
                except (ValueError,struct.error):raise ValueError('embedding_base64_invalid') from None
            vector=validate_vector(vector,self.dimension)
            norm=math.hypot(*vector)
            if not math.isfinite(norm) or norm<=0:raise ValueError('embedding_invalid_norm')
            ordered[index]=validate_vector([v/norm for v in vector],self.dimension)
        return ordered
    def encode(self,texts):
        if not isinstance(texts,(list,tuple)) or any(not isinstance(t,str) or not t.strip() for t in texts):raise ValueError('embedding_input_requires_nonempty_texts')
        sizes=[len(t.encode('utf8')) for t in texts]
        if any(n>self.max_input_bytes for n in sizes):raise ValueError('embedding_input_exceeds_conservative_limit')
        result=[];start=0;began=time.monotonic()
        try:
            while start<len(texts):
                end=start;size=0
                while end<len(texts) and end-start<self.batch_size and size+sizes[end]<=self.max_batch_bytes:
                    size+=sizes[end];end+=1
                response=self._request(list(texts[start:end]));result.extend(self._vectors(response,end-start))
                self.stats['input_texts']+=end-start
                usage=response.get('usage',{})
                tokens=usage.get('prompt_tokens',usage.get('total_tokens')) if isinstance(usage,dict) else None
                if isinstance(tokens,int) and not isinstance(tokens,bool) and tokens>=0:
                    self.stats['prompt_tokens']+=tokens;self.stats['usage_responses']+=1
                start=end
        finally:self.stats['elapsed_seconds']+=round(time.monotonic()-began,6)
        return result
