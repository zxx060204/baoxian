"""Current-source records shared by upload, publication and analysis."""
import json,re,time
from insurance_rag.parsing.common import stable_hash
from insurance_rag.reliability.database import encode
from insurance_rag.agent.store import Conflict,Forbidden

ENTITIES=('source_execution_result', 'library_product', 'material_metadata', 'bundle_subscription', 'private_asset', 'preparation_job', 'product_bundle', 'bundle_analysis')
DDL='\n'.join('CREATE TABLE IF NOT EXISTS insurance_onboarding_'+name+' (id TEXT PRIMARY KEY,owner_id TEXT NOT NULL,product_version_id TEXT NOT NULL,revision INTEGER NOT NULL,state TEXT NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL,updated_at DOUBLE PRECISION NOT NULL);' for name in ENTITIES)+'''\nCREATE TABLE IF NOT EXISTS insurance_onboarding_event(event_id TEXT PRIMARY KEY,entity TEXT NOT NULL,entity_id TEXT NOT NULL,revision INTEGER NOT NULL,content TEXT NOT NULL,content_hash TEXT NOT NULL,created_at DOUBLE PRECISION NOT NULL);\n'''

class IntakeStore:
 def __init__(self,agent):
  self.agent=agent
  with agent.transaction():
   if agent.postgres:self.execute('CREATE SCHEMA IF NOT EXISTS insurance_onboarding')
   for s in DDL.split(';'):
    if s.strip():self.execute(s)

 def execute(self,sql,args=()):
  if self.agent.postgres:sql=re.sub(r'insurance_onboarding_([a-z_]+)',r'insurance_onboarding.\1',sql)
  return self.agent.execute(sql,args)

 def table(self,kind):
  if kind not in ENTITIES:raise ValueError('invalid_entity')
  return 'insurance_onboarding_'+kind

 def get(self,kind,id,owner=None):
  row=self.execute('SELECT owner_id,product_version_id,revision,state,content,content_hash,created_at,updated_at FROM '+self.table(kind)+' WHERE id=?',(id,)).fetchone()
  if not row or owner is not None and row[0]!=owner:raise Forbidden('record_unavailable')
  content=json.loads(row[4])
  if stable_hash(content)!=row[5]:raise Conflict('record_integrity_failed')
  return {'id':id,'owner_id':row[0],'product_version_id':row[1],'revision':row[2],'state':row[3],'content':content,'content_hash':row[5],'created_at':row[6],'updated_at':row[7]}

 def list(self,kind,*,owner=None,product=None,limit=100):
  clauses=[];args=[]
  for col,val in (('owner_id',owner),('product_version_id',product)):
   if val is not None:clauses.append(col+'=?');args.append(val)
  sql='SELECT id FROM '+self.table(kind)+(' WHERE '+' AND '.join(clauses) if clauses else '')+' ORDER BY created_at DESC LIMIT ?'
  return [self.get(kind,r[0],owner) for r in self.execute(sql,tuple(args+[min(max(limit,1),500)])).fetchall()]

 def create(self,kind,id,owner,product,content,state='pending'):
  now=time.time();digest=stable_hash(content)
  with self.agent.transaction():
   self.execute('INSERT INTO '+self.table(kind)+' VALUES (?,?,?,1,?,?,?,?,?) ON CONFLICT DO NOTHING',(id,owner,product,state,encode(content),digest,now,now))
   saved=self.get(kind,id)
   if saved['owner_id']!=owner or saved['product_version_id']!=product or saved['content_hash']!=digest:raise Conflict('idempotency_input_conflict')
  return saved

 def update(self,kind,id,expected,content,state,actor,reason):
  if not actor or not reason:raise ValueError('audit_actor_and_reason_required')
  with self.agent.transaction():
   prior=self.get(kind,id);digest=stable_hash(content);now=time.time()
   if prior['revision']==expected+1 and prior['content_hash']==digest and prior['state']==state:return prior
   c=self.execute('UPDATE '+self.table(kind)+' SET revision=revision+1,state=?,content=?,content_hash=?,updated_at=? WHERE id=? AND revision=?',(state,encode(content),digest,now,id,expected))
   if c.rowcount!=1:raise Conflict('record_revision_conflict')
   event={'actor':actor,'reason':reason,'old_hash':prior['content_hash'],'new_hash':digest,'old_state':prior['state'],'new_state':state}
   self.execute('INSERT INTO insurance_onboarding_event VALUES (?,?,?,?,?,?,?)',(stable_hash([kind,id,expected+1]),kind,id,expected+1,encode(event),stable_hash(event),now))
  return self.get(kind,id)

 def events(self,kind,id):
  return [{'revision':r[0],**json.loads(r[1])} for r in self.execute('SELECT revision,content FROM insurance_onboarding_event WHERE entity=? AND entity_id=? ORDER BY revision',(kind,id)).fetchall()]

