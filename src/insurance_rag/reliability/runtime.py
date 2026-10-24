"""Request deadlines, sanitized errors and bounded dependency admission."""
import contextvars, math, random, threading, time
from contextlib import contextmanager
from email.utils import parsedate_to_datetime

_deadline=contextvars.ContextVar('insurance_deadline',default=None)
class DeadlineExceeded(TimeoutError): pass
class DependencyUnavailable(RuntimeError):
    def __init__(self,code,retry_after=1):
        super().__init__(code);self.retry_after=retry_after
class StageFailure(RuntimeError):
    def __init__(self,code,retry_after=1):
        super().__init__('stage_failed');self.code=code;self.retry_after=retry_after

def positive(value,label):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<=0:
        raise ValueError('invalid_'+label)
    return value

class Deadline:
    def __init__(self,seconds,clock=time.monotonic):
        self.clock=clock;self.ends=clock()+positive(seconds,'deadline')
    def remaining(self):
        value=self.ends-self.clock()
        if value<=0:raise DeadlineExceeded('request_deadline_exceeded')
        return value
    def timeout(self,maximum):return min(positive(maximum,'timeout'),self.remaining())
    def sleep(self,seconds,sleeper=time.sleep):
        if seconds<0 or not math.isfinite(seconds):raise ValueError('invalid_delay')
        if seconds>=self.remaining():raise DeadlineExceeded('retry_exceeds_request_budget')
        sleeper(seconds);self.remaining()

@contextmanager
def request_budget(seconds):
    old=_deadline.get();current=Deadline(seconds)
    if old:current.ends=min(current.ends,old.ends)
    token=_deadline.set(current)
    try:yield current
    finally:_deadline.reset(token)

def check_budget():
    d=_deadline.get()
    if d:d.remaining()

def bounded_timeout(maximum):
    d=_deadline.get();return d.timeout(maximum) if d else maximum

def retry_sleep(seconds,sleeper=time.sleep):
    d=_deadline.get()
    if d:d.sleep(seconds,sleeper)
    else:sleeper(seconds)

def error_code(exc):
    # Only enumerated codes enter reports. Never expose exception text/URLs/SQL.
    from insurance_rag.agent.runtime_errors import RuntimeSourceChanged,RuntimeBindingMismatch
    if isinstance(exc,RuntimeSourceChanged):return 'runtime_source_changed_restart_required'
    if isinstance(exc,RuntimeBindingMismatch):return 'runtime_binding_mismatch'
    if isinstance(exc,StageFailure):return exc.code if exc.code in ('service_unavailable','rate_limited','dependency_unavailable','authentication_failed','contract_or_quality_violation','internal_error') else 'internal_error'
    if isinstance(exc,DeadlineExceeded):return 'deadline_exceeded'
    if isinstance(exc,DependencyUnavailable):return 'dependency_unavailable'
    if isinstance(exc,ValueError):return 'contract_or_quality_violation'
    if type(exc).__module__.startswith('psycopg'):
        state=getattr(exc,'sqlstate',None) or ''
        if state.startswith('28'):return 'authentication_failed'
        if state=='57014':return 'deadline_exceeded'
        if state.startswith(('23','42')):return 'contract_or_quality_violation'
        return 'service_unavailable'
    text=str(exc)
    for provider in ('bailian','opensearch','deepseek'):
        prefix=provider+'_http_'
        if text.startswith(prefix) and text[len(prefix):].isdigit():
            code=int(text[len(prefix):]);return 'authentication_failed' if code in (401,403) else 'rate_limited' if code==429 else 'service_unavailable'
    if isinstance(exc,(TimeoutError,OSError,RuntimeError)):return 'service_unavailable'
    return 'internal_error'

def retryable(exc):return error_code(exc) in ('rate_limited','service_unavailable','dependency_unavailable')

def retry_delay(attempt,retry_after=None,*,wall_clock=time.time,random_fn=random.random):
    delay=min(8,2**attempt)*(0.5+random_fn()*0.5)
    if retry_after:
        try:required=float(retry_after)
        except (ValueError,TypeError):
            try:required=parsedate_to_datetime(retry_after).timestamp()-wall_clock()
            except (ValueError,TypeError,OverflowError):required=0
        if math.isfinite(required):delay=max(delay,required)
    return max(0,delay)

class DependencyPolicy:
    """Per-process bulkhead, minimum call interval, and one half-open probe."""
    def __init__(self,concurrency=2,interval=0,failures=3,cooldown=30,clock=time.monotonic):
        if type(concurrency) is not int or concurrency<1 or type(failures) is not int or failures<1:raise ValueError('invalid_dependency_policy')
        if interval<0 or not math.isfinite(interval):raise ValueError('invalid_dependency_interval')
        positive(cooldown,'cooldown');self.sem=threading.BoundedSemaphore(concurrency);self.lock=threading.Lock()
        self.clock=clock;self.interval=interval;self.threshold=failures;self.cooldown=cooldown
        self.failed=0;self.open_until=0;self.probing=False;self.next_call=0
    @contextmanager
    def call(self):
        if not self.sem.acquire(blocking=False):raise DependencyUnavailable('dependency_concurrency_limit')
        probe=False;entered=False
        try:
            with self.lock:
                now=self.clock()
                if self.failed>=self.threshold:
                    if now<self.open_until or self.probing:raise DependencyUnavailable('dependency_circuit_open')
                    self.probing=True;probe=True
                if now<self.next_call:
                    if probe:self.probing=False
                    raise DependencyUnavailable('dependency_rate_limit')
                self.next_call=now+self.interval;entered=True
            check_budget()
            yield
        except Exception as exc:
            if entered:
                with self.lock:
                    if retryable(exc):
                        self.failed+=1
                        if self.failed>=self.threshold:self.open_until=self.clock()+self.cooldown
                    elif probe:self.failed=0
                    self.probing=False
            raise
        else:
            with self.lock:self.failed=0;self.open_until=0;self.probing=False
        finally:self.sem.release()

_POLICIES={};_policy_lock=threading.Lock()
def provider_policy(key):
    with _policy_lock:return _POLICIES.setdefault(key,DependencyPolicy())


_traces=contextvars.ContextVar('insurance_stage_traces',default=None)
@contextmanager
def trace_stages(target):
 token=_traces.set(target)
 try:yield
 finally:_traces.reset(token)
@contextmanager
def span(label):
 began=time.monotonic()
 try:yield
 finally:
  target=_traces.get()
  if target is not None:target[label]=target.get(label,0)+time.monotonic()-began
