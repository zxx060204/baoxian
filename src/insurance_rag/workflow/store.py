"""Immutable current-source analysis results and owner-scoped history."""
import json,re,time
from insurance_rag.parsing.common import stable_hash
from insurance_rag.reliability.database import encode
from insurance_rag.agent.store import Conflict,Forbidden
DDL="CREATE TABLE IF NOT EXISTS insurance_workflow_result(result_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES insurance_agent_run,kind TEXT NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL);"
class WorkflowStore:
 def __init__(self,agent_store):
  self.agent=agent_store
  with agent_store.transaction():
   if agent_store.postgres:self.execute('CREATE SCHEMA IF NOT EXISTS insurance_workflow')
   for s in DDL.split(';'):
    if s.strip():self.execute(s)

 def execute(self,sql,args=()):
  if self.agent.postgres:sql=re.sub(r'insurance_workflow_([a-z_]+)',r'insurance_workflow.\1',sql)
  return self.agent.execute(sql,args)

 def save_result(self,run,kind,content):
  digest=stable_hash(content);rid=stable_hash([run['run_id'],kind,digest])
  with self.agent.transaction():
   self.agent.guard(run);self.execute('INSERT INTO insurance_workflow_result VALUES (?,?,?,?,?,?) ON CONFLICT DO NOTHING',(rid,run['run_id'],kind,encode(content),digest,time.time()))
  return rid

 def read_result(self,rid,owner):
  row=self.execute('SELECT run_id,kind,content,content_hash FROM insurance_workflow_result WHERE result_id=?',(rid,)).fetchone()
  if not row:raise Forbidden('workflow_result_unavailable')
  self.agent.get(row[0],owner);data=json.loads(row[2])
  if stable_hash(data)!=row[3]:raise Conflict('workflow_result_changed')
  return {'result_id':rid,'run_id':row[0],'kind':row[1],'content':data}

 def analyses(self,owner,thread_id=None):
  args=[owner]
  query="SELECT w.result_id,w.run_id,w.kind,w.created_at,r.thread_id,w.content FROM insurance_workflow_result w JOIN insurance_agent_run r ON r.run_id=w.run_id JOIN insurance_agent_thread t ON t.thread_id=r.thread_id WHERE t.owner_id=? AND w.kind IN ('loop_answer','loop_partial')"
  if thread_id:
   self.agent.thread(thread_id,owner);query+=' AND r.thread_id=?';args.append(thread_id)
  rows=self.execute(query+' ORDER BY w.created_at DESC LIMIT 50',tuple(args)).fetchall()
  return [{'analysis_id':r[0],'run_id':r[1],'kind':r[2],'created_at':r[3],'thread_id':r[4],
           'preview':json.loads(r[5]).get('text','')[:300]} for r in rows]

