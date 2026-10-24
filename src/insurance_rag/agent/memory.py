"""Owner-scoped, explicit memory and Chinese substring history retrieval."""
import json,uuid
from insurance_rag.agent.store import Forbidden,Conflict
class MemoryStore:
 def __init__(self,store,max_bytes=6000):self.store=store;self.max_bytes=max_bytes
 def active(self,owner):
  rows=self.store.execute('SELECT memory_id,category,content,source_message_id,revision,updated_at FROM insurance_agent_memory WHERE owner_id=? AND deleted=0 ORDER BY created_at',(owner,)).fetchall()
  return [dict(zip(('memory_id','category','content','source_message_id','revision','asserted_at'),row)) for row in rows]
 def deleted(self,owner):return [{'memory_id':r[0],'content':r[1],'source_message_id':r[2],'forgotten_at':r[3]} for r in self.store.execute('SELECT memory_id,content,source_message_id,updated_at FROM insurance_agent_memory WHERE owner_id=? AND deleted=1',(owner,)).fetchall()]
 def change(self,run,owner,action,content='',category='preference',memory_id=None,expected_revision=None,operation_id=None):
  with self.store.transaction():
   self.store.guard(run);now=self.store.clock()
   if action=='remember':
    total=sum(len(x['content'].encode('utf8')) for x in self.active(owner))
    if total+len(content.encode('utf8'))>self.max_bytes:raise ValueError('memory_capacity_requires_user_choice')
    from insurance_rag.parsing.common import stable_hash
    mid=stable_hash([run['run_id'],operation_id,'remember']) if operation_id else uuid.uuid4().hex
    existing=self.store.execute('SELECT owner_id,content,deleted FROM insurance_agent_memory WHERE memory_id=?',(mid,)).fetchone()
    if existing:
     if existing[0]!=owner or existing[1]!=content or existing[2]:raise Conflict('memory_operation_conflict')
     return mid
    self.store.execute('INSERT INTO insurance_agent_memory VALUES (?,?,?,?,?,1,0,?,?)',(mid,owner,category,content,run['run_id']+':1',now,now));return mid
   row=self.store.execute('SELECT owner_id,revision,deleted,content,source_message_id FROM insurance_agent_memory WHERE memory_id=?',(memory_id,)).fetchone()
   if not row or row[0]!=owner:raise Forbidden('memory_unavailable')
   if operation_id and expected_revision is not None and row[1]==expected_revision+1 and row[4]==run['run_id']+':1' and (action=='forget' and row[2] or action=='update' and not row[2] and row[3]==content):return memory_id
   if row[2]:raise Forbidden('memory_unavailable')
   if expected_revision!=row[1]:raise Conflict('memory_revision_conflict')
   if action=='update' and sum(len(x['content'].encode('utf8')) for x in self.active(owner) if x['memory_id']!=memory_id)+len(content.encode('utf8'))>self.max_bytes:raise ValueError('memory_capacity_requires_user_choice')
   self.store.execute('UPDATE insurance_agent_memory SET content=?,revision=revision+1,deleted=?,updated_at=?,source_message_id=? WHERE memory_id=?',(content if action=='update' else row[3],int(action=='forget'),now,run['run_id']+':1',memory_id));return memory_id
 def search(self,owner,query):return [x for x in self.active(owner) if query in x['content']][:8]
 def history(self,owner,query):
  if len(query)<2:return []
  pattern='%'+query.replace('!','!!').replace('%','!%').replace('_','!_')+'%'
  rows=self.store.execute("SELECT m.run_id,m.sequence,m.message FROM insurance_agent_message m JOIN insurance_agent_run r ON r.run_id=m.run_id JOIN insurance_agent_thread t ON t.thread_id=r.thread_id WHERE t.owner_id=? AND m.message LIKE ? ESCAPE '!' ORDER BY r.created_at DESC,m.sequence DESC LIMIT 100",(owner,pattern)).fetchall()
  deleted=self.deleted(owner);results=[]
  profile_runs={x['source_message_id'].rsplit(':',1)[0] for x in self.profile.redactions(owner)} if hasattr(self,'profile') else set()
  for rid,sequence,encoded in rows:
   if rid in profile_runs:continue
   message=json.loads(encoded);text=message.get('content','')
   if not isinstance(text,str) or query not in text:continue
   if any(x['content'] in text or x['source_message_id']==str(rid)+':'+str(sequence) for x in deleted):continue
   results.append({'source_message_id':str(rid)+':'+str(sequence),'role':message['role'],'excerpt':text[max(0,text.find(query)-60):text.find(query)+240],'status':'historical_discussion_not_product_fact'})
   if len(results)>=8:break
  return results
