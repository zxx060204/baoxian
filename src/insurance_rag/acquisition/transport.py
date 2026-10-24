"""Bounded, auditable HTTP retries; normal TLS with urllib/curl fallback."""
from __future__ import annotations
import datetime, email.utils, hashlib, http.client, json, os, pathlib, shutil, socket, ssl, subprocess, tempfile, time, urllib.error, urllib.parse, urllib.request

def now(): return datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=8))).isoformat()
def digest(data): return hashlib.sha256(data).hexdigest()

class FetchError(Exception):
    def __init__(self, kind, message, status=None, headers=None, attempts=None, stop=False):
        super().__init__(message); self.kind=kind; self.status=status; self.headers=headers or {}; self.attempts=attempts or []; self.stop=stop

def error_kind(exc):
    if isinstance(exc,FetchError): return exc.kind
    if isinstance(exc,urllib.error.HTTPError):
        if exc.code in (401,403,429): return 'access_restriction'
        if exc.code==404: return 'not_found'
        if exc.code in (408,500,502,503,504): return 'server_transient'
        return 'http_permanent'
    reason=getattr(exc,'reason',exc)
    if isinstance(reason,ssl.SSLCertVerificationError): return 'tls_certificate'
    if isinstance(reason,(socket.timeout,TimeoutError,subprocess.TimeoutExpired)): return 'timeout'
    if isinstance(reason,ssl.SSLError): return 'tls_connection'
    if isinstance(reason,http.client.IncompleteRead): return 'incomplete_body'
    if isinstance(reason,(ConnectionError,OSError)): return 'network_transient'
    return 'unexpected_error'

def retry_after(headers):
    value=next((v for k,v in headers.items() if k.lower()=='retry-after'),None)
    if not value: return 0
    try: return max(0,float(value))
    except ValueError:
        try: return max(0,(email.utils.parsedate_to_datetime(value)-datetime.datetime.now(datetime.timezone.utc)).total_seconds())
        except Exception: return 0

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*a,**k): return None

class Transport:
    def __init__(self,domains,evidence,referer='',interval=3,max_attempts=3,timeout=45,max_bytes=128*1024**2,large_bytes=256*1024**2,prefer='urllib',sleeper=time.sleep):
        self.domains=domains; self.evidence=pathlib.Path(evidence); self.evidence.mkdir(parents=True,exist_ok=True)
        self.referer=referer; self.interval=interval; self.max_attempts=max_attempts; self.timeout=timeout
        self.max_bytes=max_bytes; self.large_bytes=large_bytes; self.prefer=prefer; self.sleep=sleeper; self.last=0
        self.curl=shutil.which('curl.exe') or shutil.which('curl'); self.sequence=0
    def allowed(self,url):
        p=urllib.parse.urlsplit(url); host=p.hostname or ''
        return p.scheme=='https' and not p.username and not p.password and (p.port in (None,443)) and any(host==d or host.endswith('.'+d) for d in self.domains)
    def pace(self):
        wait=self.interval-(time.monotonic()-self.last)
        if wait>0: self.sleep(wait)
        self.last=time.monotonic()
    def _urllib(self,url,data,headers,timeout,limit):
        try:
            with urllib.request.build_opener(NoRedirect).open(urllib.request.Request(url,data=data,headers=headers),timeout=timeout) as res:
                hdr=dict(res.headers)
                if int(res.headers.get('Content-Length') or 0)>limit: raise FetchError('size_limit','FILE_SIZE_LIMIT',res.status,hdr)
                body=res.read(limit+1)
                if len(body)>limit: raise FetchError('size_limit','FILE_SIZE_LIMIT',res.status,hdr)
                return body,res.status,hdr,res.url
        except urllib.error.HTTPError as e:
            if e.code in (301,302,303,307,308): return b'',e.code,dict(e.headers),url
            raise
    def _curl(self,url,data,headers,timeout,limit):
        if not self.curl: raise FetchError('transport_unavailable','SYSTEM_CURL_NOT_INSTALLED')
        with tempfile.TemporaryDirectory(dir=self.evidence) as tmp:
            tmp=pathlib.Path(tmp); bodypath=tmp/'body'; headpath=tmp/'headers'
            args=[self.curl,'--silent','--show-error','--proto','=https','--max-time',str(timeout),'--connect-timeout',str(min(timeout,30)),'--max-filesize',str(limit),'--dump-header',str(headpath),'--output',str(bodypath),'--write-out','%{http_code}']
            for key,value in headers.items(): args+=['--header',key+': '+value]
            if data is not None:
                payload=tmp/'payload'; payload.write_bytes(data); args+=['--data-binary','@'+str(payload)]
            args.append(url)
            result=subprocess.run(args,capture_output=True,timeout=timeout+10)
            status=int(result.stdout.decode('ascii',errors='ignore').strip() or 0)
            header_text=headpath.read_text(encoding='iso-8859-1') if headpath.exists() else ''
            blocks=header_text.strip().replace('\r\n','\n').split('\n\n'); hdr={}
            if blocks:
                for line in blocks[-1].splitlines()[1:]:
                    if ':' in line:
                        key,value=line.split(':',1); hdr[key.strip()]=value.strip()
            if status in (401,403,429): raise FetchError('access_restriction','HTTP_ACCESS_RESTRICTION',status,hdr,stop=True)
            if result.returncode:
                kind={28:'timeout',35:'tls_connection',60:'tls_certificate',18:'incomplete_body',56:'network_transient',63:'size_limit',6:'network_transient',7:'network_transient'}.get(result.returncode,'transport_error')
                raise FetchError(kind,'CURL_'+str(result.returncode)+': '+result.stderr.decode(errors='replace')[-700:],status,hdr)
            if status==404: raise FetchError('not_found','HTTP_404',status,hdr)
            if status in (408,500,502,503,504): raise FetchError('server_transient','HTTP_'+str(status),status,hdr)
            if status>=400: raise FetchError('http_permanent','HTTP_'+str(status),status,hdr)
            if bodypath.exists() and bodypath.stat().st_size>limit: raise FetchError('size_limit','FILE_SIZE_LIMIT',status,hdr)
            return bodypath.read_bytes() if bodypath.exists() else b'',status,hdr,url
    def _once(self,url,data,headers,backend,timeout,limit):
        chain=[]
        for _ in range(6):
            if not self.allowed(url): raise FetchError('unapproved_url','UNAPPROVED_HTTPS_HOST_OR_PORT: '+url)
            self.pace(); body,status,hdr,final=getattr(self,'_'+backend)(url,data,headers,timeout,limit)
            if status not in (301,302,303,307,308):
                if status!=200: raise FetchError('http_permanent','UNEXPECTED_HTTP_'+str(status),status,hdr)
                length=next((v for k,v in hdr.items() if k.lower()=='content-length'),None)
                if length and int(length)!=len(body): raise FetchError('incomplete_body','CONTENT_LENGTH_MISMATCH',status,hdr)
                return body,dict(http_status=status,final_url=final,headers=hdr,bytes=len(body),sha256=digest(body),content_length_check='pass' if length else 'unavailable',redirect_chain=chain)
            location=next((v for k,v in hdr.items() if k.lower()=='location'),None)
            if not location: raise FetchError('bad_redirect','REDIRECT_WITHOUT_LOCATION',status,hdr)
            nexturl=urllib.parse.urljoin(url,location)
            if not self.allowed(nexturl): raise FetchError('unapproved_redirect','UNAPPROVED_REDIRECT: '+nexturl)
            chain.append(dict(url=url,status=status,location=nexturl)); url=nexturl
            if status==303: data=None
        raise FetchError('bad_redirect','REDIRECT_LIMIT_EXCEEDED')
    def fetch(self,url,payload=None,form=None,headers=None,catalog=False):
        hdr={'User-Agent':'InsuranceResearchValidation/5.0 (authorized public documents)','Accept-Language':'zh-CN,zh;q=0.9'}
        if self.referer: hdr['Referer']=self.referer
        data=None
        if payload is not None: data=json.dumps(payload,ensure_ascii=False,separators=(',',':')).encode(); hdr['Content-Type']='application/json'
        if form is not None: data=urllib.parse.urlencode(form).encode(); hdr['Content-Type']='application/x-www-form-urlencoded'
        hdr.update(headers or {}); attempts=[]; backend=self.prefer; limit=self.max_bytes
        self.sequence+=1
        evidence=self.evidence/(f'request_{self.sequence:05d}_{time.time_ns()}')
        for index in range(self.max_attempts):
            attempt=dict(number=index+1,backend=backend,timeout_seconds=self.timeout*(2 if index else 1),max_bytes=limit,started_at=now(),url=url)
            try:
                body,meta=self._once(url,data,hdr,backend,attempt['timeout_seconds'],limit)
                attempt.update(status='success',http_status=meta['http_status'],bytes=len(body)); attempts.append(attempt)
                meta.update(url=url,requested_at=attempts[0]['started_at'],retrieved_at=now(),attempts=attempts,retry_count=len(attempts)-1,backend=backend)
                evidence.with_suffix('.json').write_text(json.dumps(dict(meta,payload=payload,form=form),ensure_ascii=False,indent=2),encoding='utf-8')
                if catalog: evidence.with_suffix('.bin').write_bytes(body)
                return body,meta
            except Exception as exc:
                kind=error_kind(exc); status=getattr(exc,'status',getattr(exc,'code',None)); h=getattr(exc,'headers',{}) or {}
                attempt.update(status='failed',failure_kind=kind,http_status=status,error=repr(exc)); attempts.append(attempt)
                delay=max(3*(2**index),retry_after(dict(h)))
                retriable=kind in ('tls_connection','timeout','network_transient','incomplete_body','server_transient')
                if kind=='size_limit' and limit<self.large_bytes and shutil.disk_usage(self.evidence).free>self.large_bytes*3+2*1024**3:
                    limit=self.large_bytes; retriable=True; attempt['recovery']='increase_file_limit_once'
                if kind=='tls_connection' and self.curl: backend='curl'; attempt['recovery']='switch_to_system_curl_normal_TLS'
                # Never disable certificate checks or retry access blocks/challenges.
                stop=kind=='access_restriction' or getattr(exc,'stop',False)
                evidence.with_suffix('.json').write_text(json.dumps(dict(url=url,payload=payload,form=form,attempts=attempts),ensure_ascii=False,indent=2),encoding='utf-8')
                if not retriable or stop or index+1>=self.max_attempts or delay>60:
                    raise FetchError(kind,str(exc),status,dict(h),attempts,stop) from exc
                attempt['retry_delay_seconds']=delay; self.sleep(delay)
        raise AssertionError('unreachable')
