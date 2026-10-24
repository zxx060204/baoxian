"""Fenced message journal and pending native calls; no graph checkpoints."""
import json,uuid
from insurance_rag.agent.store import AgentStore,Conflict
from insurance_rag.reliability.database import encode
from insurance_rag.parsing.common import stable_hash
from insurance_rag.agent.local_content import stored_message,stored_file_result
DDL='''
CREATE TABLE IF NOT EXISTS insurance_agent_loop_state(run_id TEXT PRIMARY KEY REFERENCES insurance_agent_run,revision INTEGER NOT NULL,state TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS insurance_agent_message(run_id TEXT NOT NULL REFERENCES insurance_agent_run,sequence INTEGER NOT NULL,message TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL,PRIMARY KEY(run_id,sequence));
CREATE TABLE IF NOT EXISTS insurance_agent_tool_call(invocation_id TEXT PRIMARY KEY,run_id TEXT NOT NULL REFERENCES insurance_agent_run,provider_call_id TEXT NOT NULL,name TEXT NOT NULL,arguments TEXT NOT NULL,idempotency_key TEXT NOT NULL,state TEXT NOT NULL,result TEXT,attempt INTEGER NOT NULL,ordinal INTEGER NOT NULL,created_at DOUBLE PRECISION NOT NULL,UNIQUE(run_id,provider_call_id));
CREATE TABLE IF NOT EXISTS insurance_agent_context_checkpoint(run_id TEXT NOT NULL REFERENCES insurance_agent_run,revision INTEGER NOT NULL,checkpoint TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL,PRIMARY KEY(run_id,revision));
CREATE TABLE IF NOT EXISTS insurance_agent_scenario(thread_id TEXT NOT NULL REFERENCES insurance_agent_thread,revision INTEGER NOT NULL,scenario TEXT NOT NULL,PRIMARY KEY(thread_id,revision));
CREATE TABLE IF NOT EXISTS insurance_agent_memory(memory_id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,category TEXT NOT NULL,content TEXT NOT NULL,source_message_id TEXT NOT NULL,revision INTEGER NOT NULL,deleted INTEGER NOT NULL,created_at DOUBLE PRECISION NOT NULL,updated_at DOUBLE PRECISION NOT NULL);
'''
class LoopJournal:
 def __init__(self,store):
  self.store=store;self.live_messages={};self.live_calls={}
  with store.transaction():
   for statement in DDL.split(';'):
    if statement.strip():store.execute(statement)
 def state(self,run):
  row=self.store.execute('SELECT revision,state FROM insurance_agent_loop_state WHERE run_id=?',(run['run_id'],)).fetchone()
  return (row[0],json.loads(row[1])) if row else (0,{})
 def save(self,run,state,expected=None):
  with self.store.transaction():
   self.store.guard(run);revision,_=self.state(run)
   if expected is not None and expected!=revision:raise Conflict('transcript_revision_conflict')
   self.store.execute('INSERT INTO insurance_agent_loop_state VALUES (?,?,?) ON CONFLICT(run_id) DO UPDATE SET revision=excluded.revision,state=excluded.state',(run['run_id'],revision+1,encode(state)))
   return revision+1
 def append(self,run,message):
  self.store.guard(run)
  sequence=self.store.execute('SELECT COALESCE(MAX(sequence),0)+1 FROM insurance_agent_message WHERE run_id=?',(run['run_id'],)).fetchone()[0]
  self.store.execute('INSERT INTO insurance_agent_message VALUES (?,?,?,?)',(run['run_id'],sequence,encode(stored_message(message)),self.store.clock()))
  self.live_messages[(run['run_id'],sequence)]=message
  return str(run['run_id'])+':'+str(sequence)
 def messages(self,run):
  return [self.live_messages.get((run['run_id'],seq),json.loads(encoded)) for seq,encoded in self.store.execute('SELECT sequence,message FROM insurance_agent_message WHERE run_id=? ORDER BY sequence',(run['run_id'],)).fetchall()]
 def begin(self,run,state):
  with self.store.transaction():
   self.store.guard(run)
   if self.state(run)[1]:return
   self.append(run,{'role':'user','content':run['input']['message']});self.save(run,state)
 def assistant(self,run,message,conditions_revision,tool_versions):
  with self.store.transaction():
   self.store.guard(run);self.append(run,message)
   for call in message.get('tool_calls',[]):
    name=call['function']['name'];arguments=call['function']['arguments']
    self.live_calls[(run['run_id'],call['id'])]=arguments
    persisted=stored_message({'tool_calls':[call]})['tool_calls'][0]['function']['arguments']
    from insurance_rag.agent.model_adapter import strict_json
    try:canonical=strict_json(arguments)
    except (ValueError,TypeError):canonical=arguments
    key=stable_hash([run['run_id'],name,canonical,run['release']['release_id'],conditions_revision,tool_versions.get(name)])
    ordinal=self.store.execute('SELECT COUNT(*) FROM insurance_agent_tool_call WHERE run_id=?',(run['run_id'],)).fetchone()[0]
    self.store.execute('INSERT INTO insurance_agent_tool_call VALUES (?,?,?,?,?,?,?,NULL,0,?,?)',(uuid.uuid4().hex,run['run_id'],call['id'],name,persisted,key,'pending',ordinal,self.store.clock()))
 def pending(self,run):
  keys=('invocation_id','provider_call_id','name','arguments','idempotency_key','state','result','attempt')
  calls=[dict(zip(keys,row)) for row in self.store.execute("SELECT invocation_id,provider_call_id,name,arguments,idempotency_key,state,result,attempt FROM insurance_agent_tool_call WHERE run_id=? AND state!='completed' ORDER BY ordinal",(run['run_id'],)).fetchall()]
  for call in calls:call['arguments']=self.live_calls.get((run['run_id'],call['provider_call_id']),call['arguments'])
  return calls
 def mark_started(self,run,call):
  with self.store.transaction():
   self.store.guard(run);self.store.execute("UPDATE insurance_agent_tool_call SET state='uncertain',attempt=attempt+1 WHERE invocation_id=? AND state!='completed'",(call['invocation_id'],))
 def cached(self,run,call):
  row=self.store.execute("SELECT result FROM insurance_agent_tool_call WHERE run_id=? AND idempotency_key=? AND state='completed' ORDER BY created_at LIMIT 1",(run['run_id'],call['idempotency_key'])).fetchone()
  return json.loads(row[0]) if row else None
 def complete(self,run,call,result,wire_result,state):
  with self.store.transaction():
   self.store.guard(run)
   row=self.store.execute('SELECT state FROM insurance_agent_tool_call WHERE invocation_id=?',(call['invocation_id'],)).fetchone()
   if row[0]=='completed':return
   self.append(run,{'role':'tool','tool_call_id':call['provider_call_id'],'content':encode(wire_result)})
   self.store.execute("UPDATE insurance_agent_tool_call SET state='completed',result=? WHERE invocation_id=?",(encode(stored_file_result(result)),call['invocation_id']))
   self.save(run,state)
 def checkpoint(self,run,expected,checkpoint):
  with self.store.transaction():
   self.store.guard(run)
   if self.state(run)[0]!=expected:raise Conflict('compression_revision_conflict')
   self.store.execute('INSERT INTO insurance_agent_context_checkpoint VALUES (?,?,?,?) ON CONFLICT DO NOTHING',(run['run_id'],expected,encode(checkpoint),self.store.clock()))
 def latest_checkpoint(self,run):
  row=self.store.execute('SELECT checkpoint FROM insurance_agent_context_checkpoint WHERE run_id=? ORDER BY revision DESC LIMIT 1',(run['run_id'],)).fetchone()
  return json.loads(row[0]) if row else None
