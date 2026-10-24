"""Thread authorization, fenced runs, immutable evidence and replayable events."""
import json,sqlite3,time,uuid,re,threading
from contextlib import contextmanager
from pathlib import Path
from insurance_rag.reliability.database import DatabaseStore,encode
from insurance_rag.parsing.common import stable_hash
DDL='''
CREATE TABLE IF NOT EXISTS insurance_agent_thread(thread_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,context TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS insurance_agent_run(run_id TEXT PRIMARY KEY,thread_id TEXT NOT NULL REFERENCES insurance_agent_thread,request_id TEXT NOT NULL,input TEXT NOT NULL,release TEXT NOT NULL,bundle TEXT NOT NULL,state TEXT NOT NULL,revision INTEGER NOT NULL,lease_token TEXT,lease_expires_at DOUBLE PRECISION,budget TEXT NOT NULL,result TEXT,clarification TEXT,error_code TEXT,created_at DOUBLE PRECISION NOT NULL,updated_at DOUBLE PRECISION NOT NULL,UNIQUE(thread_id,request_id));
CREATE UNIQUE INDEX IF NOT EXISTS agent_one_active ON insurance_agent_run(thread_id) WHERE state IN ('queued','running','waiting');
CREATE TABLE IF NOT EXISTS insurance_agent_tool_invocation(operation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES insurance_agent_run,kind TEXT NOT NULL,input_hash TEXT NOT NULL,state TEXT NOT NULL,result TEXT,created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS insurance_agent_evidence_package(package_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES insurance_agent_run,package TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL);
CREATE TABLE IF NOT EXISTS insurance_agent_run_event(run_id TEXT NOT NULL REFERENCES insurance_agent_run,sequence INTEGER NOT NULL,kind TEXT NOT NULL,payload TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL,PRIMARY KEY(run_id,sequence));
'''
class Conflict(ValueError):pass
class Forbidden(PermissionError):pass
class BudgetExceeded(RuntimeError):pass
class AgentStore(DatabaseStore):
 def __init__(self,db,*,postgres=False,clock=time.time):
  self.db=db;self.postgres=postgres;self.clock=clock;self.lock=threading.RLock()
  if postgres:
   if not db.autocommit:raise ValueError('agent_requires_autocommit')
   self.execute('CREATE SCHEMA IF NOT EXISTS insurance_agent')
  else:db.execute('PRAGMA journal_mode=WAL');db.execute('PRAGMA foreign_keys=ON')
  with self.transaction():
   for sql in DDL.split(';'):
    if sql.strip():self.execute(sql)
 @classmethod
 def sqlite(cls,path,**kw):
  Path(path).parent.mkdir(parents=True,exist_ok=True);return cls(sqlite3.connect(path,isolation_level=None,timeout=10,check_same_thread=False),**kw)
 @contextmanager
 def transaction(self):
  with self.lock:
   if not self.postgres and self.db.in_transaction:yield
   else:
    with super().transaction():yield
 def execute(self,sql,args=()):
  if self.postgres:sql=re.sub(r'insurance_agent_([a-z_]+)',r'insurance_agent.\1',sql)
  return super().execute(sql,args)
 def new_thread(self,owner):
  tid=uuid.uuid4().hex;self.execute('INSERT INTO insurance_agent_thread VALUES (?,?,?,?)',(tid,owner,encode({}),self.clock()));return tid
 def thread(self,tid,owner):
  r=self.execute('SELECT owner_id,context FROM insurance_agent_thread WHERE thread_id=?',(tid,)).fetchone()
  if not r or r[0]!=owner:raise Forbidden('thread_unavailable')
  return json.loads(r[1])
 def new_run(self,tid,owner,request_id,payload,release,bundle):
  self.thread(tid,owner)
  with self.transaction():
   if self.postgres:self.execute('SELECT pg_advisory_xact_lock(hashtext(?))',('agent-thread:'+tid,))
   r=self.execute('SELECT run_id,input FROM insurance_agent_run WHERE thread_id=? AND request_id=?',(tid,request_id)).fetchone()
   if r:
    if {k:v for k,v in json.loads(r[1]).items() if k!='context'}!={k:v for k,v in payload.items() if k!='context'}:raise Conflict('request_id_input_conflict')
    return r[0]
   active=self.execute("SELECT 1 FROM insurance_agent_run WHERE thread_id=? AND state IN ('queued','running','waiting')",(tid,)).fetchone()
   if active:raise Conflict('thread_has_active_run')
   rid=uuid.uuid4().hex;now=self.clock()
   self.execute("INSERT INTO insurance_agent_run VALUES (?,?,?,?,?,?,'queued',0,NULL,NULL,?,NULL,NULL,NULL,?,?)",(rid,tid,request_id,encode(payload),encode(release),encode(bundle),encode({'steps':0,'tool_calls':0,'llm_calls':0}),now,now));self.event(rid,'run_created',{'run_id':rid})
   return rid
 def get(self,rid,owner):
  keys=('run_id','thread_id','request_id','input','release','bundle','state','revision','lease_token','lease_expires_at','budget','result','clarification','error_code','created_at','updated_at')
  row=self.execute('SELECT '+','.join(keys)+' FROM insurance_agent_run WHERE run_id=?',(rid,)).fetchone()
  if not row:raise Forbidden('run_unavailable')
  r=dict(zip(keys,row));self.thread(r['thread_id'],owner)
  for key in ('input','release','bundle','budget','result','clarification'):
   if r[key] is not None:r[key]=json.loads(r[key])
  return r
 def claim_run(self,rid,owner,*,seconds=45,expected_revision=None,execution_token=None):
  with self.transaction():
   r=self.get(rid,owner)
   if expected_revision is not None and r['revision']!=expected_revision:raise Conflict('run_revision_conflict')
   if r['state'] in ('completed','failed','cancelled'):return None
   if r['state']=='running' and r['lease_expires_at']>self.clock():raise Conflict('run_already_running')
   token=execution_token or uuid.uuid4().hex
   c=self.execute("UPDATE insurance_agent_run SET state='running',lease_token=?,lease_expires_at=?,revision=revision+1,updated_at=? WHERE run_id=? AND revision=?",(token,self.clock()+seconds+10,self.clock(),rid,r['revision']))
   if c.rowcount!=1:raise Conflict('run_revision_conflict')
   return self.get(rid,owner)
 def guard(self,run):
  # A control connection must not execute/fetch concurrently across branches.
  with self.lock:
   r=self.execute('SELECT state,lease_token,lease_expires_at FROM insurance_agent_run WHERE run_id=?'+(' FOR UPDATE' if self.postgres else ''),(run['run_id'],)).fetchone()
   if not r or r[0]!='running' or r[1]!=run['lease_token'] or r[2]<=self.clock():raise Conflict('run_ownership_lost')
 def reserve(self,run,kind,limit):
  with self.transaction():
   self.guard(run);r=self.execute('SELECT budget FROM insurance_agent_run WHERE run_id=?',(run['run_id'],)).fetchone();b=json.loads(r[0])
   if b.get(kind,0)>=limit:raise BudgetExceeded('agent_budget_exceeded')
   b[kind]=b.get(kind,0)+1;self.execute('UPDATE insurance_agent_run SET budget=? WHERE run_id=?',(encode(b),run['run_id']))
 def add_usage(self,run,usage):
  with self.transaction():
   self.guard(run);b=json.loads(self.execute('SELECT budget FROM insurance_agent_run WHERE run_id=?',(run['run_id'],)).fetchone()[0])
   for k,v in usage.items():
    if type(v) is not int or v<0:raise ValueError('invalid_model_usage_delta')
    b[k]=b.get(k,0)+v
   self.execute('UPDATE insurance_agent_run SET budget=? WHERE run_id=?',(encode(b),run['run_id']))
 def event(self,rid,kind,payload):
  # Called inside short transactions when coupled to state changes.
  self.execute('INSERT INTO insurance_agent_run_event SELECT ?,COALESCE(MAX(sequence),0)+1,?,?,? FROM insurance_agent_run_event WHERE run_id=?',(rid,kind,encode(payload),self.clock(),rid))
 def settle(self,run,state,*,result=None,clarification=None,error=None):
  with self.transaction():
   self.guard(run)
   self.execute('UPDATE insurance_agent_run SET state=?,result=?,clarification=?,error_code=?,lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE run_id=?',(state,encode(result) if result is not None else None,encode(clarification) if clarification is not None else None,error,self.clock(),run['run_id']))
   if state=='completed' and result and result.get('status')=='answered':
    text=result.get('text','')
    for offset in range(0,len(text),200):self.event(run['run_id'],'answer_delta',{'text':text[offset:offset+200],'validated':True})
    self.event(run['run_id'],'answer_completed',{'validated':True,'evidence_package_id':result.get('evidence_package_id')})
   self.event(run['run_id'],'clarification_required' if state=='waiting' else 'run_'+state,clarification if state=='waiting' else (result or {'error_code':error}))
 def retry_failed(self,rid,owner):
  with self.transaction():
   r=self.get(rid,owner)
   if r['state']!='failed' or r['error_code'] not in ('service_unavailable','rate_limited','deadline_exceeded','worker_startup_failed','worker_exited_without_completion'):raise Conflict('run_not_retryable')
   self.execute("UPDATE insurance_agent_run SET state='queued',revision=revision+1,error_code=NULL WHERE run_id=?",(rid,));self.event(rid,'retry_requested',{})
 def cancel_run(self,rid,owner):
  with self.transaction():
   r=self.get(rid,owner)
   if r['state'] in ('completed','failed','cancelled'):return r
   self.execute("UPDATE insurance_agent_run SET state='cancelled',revision=revision+1,lease_token=NULL,lease_expires_at=NULL,updated_at=? WHERE run_id=?",(self.clock(),rid));self.event(rid,'run_cancelled',{})
  return self.get(rid,owner)
 def save_context(self,run,context):
  with self.transaction():
   self.guard(run);self.execute('UPDATE insurance_agent_thread SET context=? WHERE thread_id=?',(encode(context),run['thread_id']))
 def package(self,run,data):
  with self.transaction():
   self.guard(run);pid=stable_hash([run['run_id'],data]);self.execute('INSERT INTO insurance_agent_evidence_package VALUES (?,?,?,?) ON CONFLICT DO NOTHING',(pid,run['run_id'],encode(data),self.clock()));return pid
 def read_package(self,pid,owner):
  r=self.execute('SELECT run_id,package FROM insurance_agent_evidence_package WHERE package_id=?',(pid,)).fetchone()
  if not r:raise Forbidden('evidence_unavailable')
  self.get(r[0],owner);return json.loads(r[1])
 def cached(self,run,kind,args):
  op=stable_hash([run['run_id'],kind,args,run['release']['release_id'],run['bundle']]);row=self.execute('SELECT state,result FROM insurance_agent_tool_invocation WHERE operation_id=?',(op,)).fetchone()
  return op,json.loads(row[1]) if row and row[0]=='completed' else None
 def cache_result(self,run,op,kind,args,result):
  with self.transaction():
   self.guard(run)
   self.execute("INSERT INTO insurance_agent_tool_invocation VALUES (?,?,?,?,'completed',?,?) ON CONFLICT DO NOTHING",(op,run['run_id'],kind,stable_hash(args),encode(result),self.clock()))
 def events(self,rid,owner,after=0):
  self.get(rid,owner)
  return [{'sequence':n,'event':k,'data':json.loads(p)} for n,k,p in self.execute('SELECT sequence,kind,payload FROM insurance_agent_run_event WHERE run_id=? AND sequence>? ORDER BY sequence',(rid,after)).fetchall()]
