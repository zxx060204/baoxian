"""Run-scoped file operations with reparse-point rejection and optimistic versions."""
import os,re,uuid,json
from pathlib import Path
from insurance_rag.parsing.common import stable_hash,file_hash
from insurance_rag.agent.store import Conflict
class Workspace:
 def __init__(self,root,owner,thread_id,store,run):
  self.store,self.run=store,run;self.project_root=Path(root)
  self.root=Path(root)/'data'/'agent_workspaces'/stable_hash(owner)/thread_id
  self.root.mkdir(parents=True,exist_ok=True)
  self._no_reparse(self.root)
 def _no_reparse(self,path):
  for node in [path,*path.parents]:
   if not node.exists() and not node.is_symlink():continue
   info=node.lstat()
   if node.is_symlink() or getattr(info,'st_file_attributes',0)&0x400:raise ValueError('workspace_reparse_point_denied')
 def path(self,name):
  if not isinstance(name,str) or not 1<=len(name)<=200 or '\\' in name or ':' in name or name.startswith('/') or any(x in ('','.', '..') or x.endswith((' ','.')) or re.fullmatch(r'(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\..*)?',x,re.I) for x in name.split('/')):raise ValueError('invalid_workspace_path')
  if name.split('/')[0] not in ('notes','drafts','reports'):raise ValueError('workspace_namespace_denied')
  path=self.root/name;self._no_reparse(path)
  if not path.resolve().is_relative_to(self.root.resolve()):raise ValueError('workspace_escape')
  return path
 def list(self):
  result=[]
  for path in self.root.rglob('*'):
   self._no_reparse(path)
   if path.is_file() and not path.name.endswith('.tmp'):result.append({'path':path.relative_to(self.root).as_posix(),'version':file_hash(path),'bytes':path.stat().st_size})
   if len(result)>100:raise ValueError('workspace_file_count_limit')
  return result
 def reference(self,path):
  return {'path':path.relative_to(self.root).as_posix(),'path_namespace':'session_workspace','artifact_path':path.relative_to(self.project_root).as_posix()}
 def read(self,name,offset=0,limit=8000):
  path=self.path(name)
  if path.stat().st_size>200000:raise ValueError('workspace_file_too_large')
  text=path.read_text(encoding='utf8')
  return {**self.reference(path),'version':file_hash(path),'content':text[offset:offset+limit],'offset':offset,'has_more':offset+limit<len(text)}
 def write(self,name,content,expected_version,operation):
  path=self.path(name);raw=content.encode('utf8')
  if len(raw)>200000:raise ValueError('workspace_file_too_large')
  digest=__import__('hashlib').sha256(raw).hexdigest();marker=stable_hash([self.run['run_id'],operation,name,content,expected_version]);args={'marker':marker};op,_=self.store.cached(self.run,'workspace_write',args)
  def lock():
   self.store.guard(self.run)
   if self.store.postgres:self.store.execute('SELECT pg_advisory_xact_lock(hashtext(?))',('workspace:'+str(self.root),))
  with self.store.transaction():
   lock();entries=self.list();total=sum(x['bytes'] for x in entries if x['path']!=name)
   if total+len(raw)>1000000 or not path.exists() and len(entries)>=100:raise ValueError('workspace_quota_exceeded')
   current=file_hash(path) if path.exists() else None
   row=self.store.execute('SELECT state,result FROM insurance_agent_tool_invocation WHERE operation_id=?',(op,)).fetchone()
   if row:
    saved=json.loads(row[1])
    if row[0]=='completed':
     if current!=saved['version']:raise Conflict('workspace_artifact_changed')
     return saved
    if saved!={'path':name,'version':digest,'expected_version':expected_version,'operation':operation}:raise Conflict('workspace_intent_conflict')
    if current==digest:
     result={**self.reference(path),'version':digest,'bytes':len(raw)};self.store.execute("UPDATE insurance_agent_tool_invocation SET state='completed',result=? WHERE operation_id=?",(json.dumps(result),op));return result
   if current!=expected_version:raise Conflict('workspace_version_conflict')
   self.store.execute("INSERT INTO insurance_agent_tool_invocation VALUES (?,?,?,?,'pending',?,?) ON CONFLICT DO NOTHING",(op,self.run['run_id'],'workspace_write',stable_hash(args),json.dumps({'path':name,'version':digest,'expected_version':expected_version,'operation':operation}),self.store.clock()))
  # Intent is committed before the external file effect; replay can reconcile its hash.
  path.parent.mkdir(parents=True,exist_ok=True);self._no_reparse(path.parent);temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
  try:
   temp.write_bytes(raw)
   with self.store.transaction():
    lock();self._no_reparse(path)
    current=file_hash(path) if path.exists() else None
    if current!=expected_version:raise Conflict('workspace_version_conflict')
    os.replace(temp,path);result={**self.reference(path),'version':digest,'bytes':len(raw)}
    self.store.execute("UPDATE insurance_agent_tool_invocation SET state='completed',result=? WHERE operation_id=?",(json.dumps(result),op))
    return result
  finally:
   if temp.exists():temp.unlink()


class LocalWorkspace(Workspace):
 """User-local Markdown/CSV directory, shared across this owner's conversations."""
 def __init__(self,root,owner,thread_id,store,run):
  self.store,self.run=store,run;self.project_root=Path(root)
  configured=os.environ.get('INSURANCE_LOCAL_FILES_ROOT')
  base=Path(configured).expanduser() if configured else Path(root)/'local_files'
  if not base.is_absolute():base=Path(root)/base
  self.root=base/stable_hash(owner)
  self.root.mkdir(parents=True,exist_ok=True);self._no_reparse(self.root)
 def path(self,name):
  path=super().path(name)
  if path.suffix.lower() not in ('.md','.csv'):raise ValueError('local_file_format_not_supported')
  return path
 def reference(self,path):
  return {'path':path.relative_to(self.root).as_posix(),'path_namespace':'user_local',
          'local_path':str(path.resolve()),'device_id':stable_hash([__import__('platform').node(),str(self.root.resolve())])[:24]}
