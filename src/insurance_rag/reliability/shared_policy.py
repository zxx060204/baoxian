"""Host-shared API admission and circuit state; no credentials or payloads stored."""
import sqlite3,time,uuid
from contextlib import contextmanager,closing
from pathlib import Path
from .runtime import DependencyUnavailable,check_budget,retryable,positive
class SharedDependencyPolicy:
 def __init__(self,path,key,*,concurrency=2,interval=0,failures=3,cooldown=30,lease_seconds=120,clock=time.time):
  if type(concurrency) is not int or concurrency<1 or type(failures) is not int or failures<1:raise ValueError('invalid_shared_dependency_limits')
  positive(lease_seconds,'dependency_lease');positive(cooldown,'cooldown')
  if interval<0:raise ValueError('invalid_dependency_interval')
  self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.key=key;self.clock=clock;self.concurrency=concurrency;self.interval=interval;self.threshold=failures;self.cooldown=cooldown;self.ttl=lease_seconds
  with closing(self.db()) as db:
   db.execute('CREATE TABLE IF NOT EXISTS dependency_state(key TEXT PRIMARY KEY,failed INTEGER NOT NULL,open_until REAL NOT NULL,next_call REAL NOT NULL,probe_owner TEXT)')
   db.execute('CREATE TABLE IF NOT EXISTS dependency_lease(key TEXT NOT NULL,owner TEXT PRIMARY KEY,expires_at REAL NOT NULL)')
   db.execute('INSERT OR IGNORE INTO dependency_state VALUES (?,0,0,0,NULL)',(key,))
 def db(self):
  db=sqlite3.connect(self.path,timeout=1,isolation_level=None);db.execute('PRAGMA journal_mode=WAL');return db
 def claim(self):
  check_budget();owner=uuid.uuid4().hex;now=self.clock();db=self.db()
  try:
   db.execute('BEGIN IMMEDIATE');db.execute('DELETE FROM dependency_lease WHERE expires_at<=?',(now,))
   failed,opened,next_call,probe=db.execute('SELECT failed,open_until,next_call,probe_owner FROM dependency_state WHERE key=?',(self.key,)).fetchone()
   if probe and not db.execute('SELECT 1 FROM dependency_lease WHERE owner=?',(probe,)).fetchone():probe=None
   if failed>=self.threshold and (opened>now or probe):raise DependencyUnavailable('shared_circuit_open',retry_after=max(1,opened-now))
   count=db.execute('SELECT COUNT(*) FROM dependency_lease WHERE key=?',(self.key,)).fetchone()[0]
   if count>=self.concurrency:raise DependencyUnavailable('shared_concurrency_limit')
   if next_call>now:raise DependencyUnavailable('shared_rate_limit',retry_after=next_call-now)
   db.execute('INSERT INTO dependency_lease VALUES (?,?,?)',(self.key,owner,now+self.ttl))
   db.execute('UPDATE dependency_state SET next_call=?,probe_owner=? WHERE key=?',(now+self.interval,owner if failed>=self.threshold else probe,self.key));db.commit();return owner
  except BaseException:db.rollback();raise
  finally:db.close()
 def complete(self,owner,exc):
  db=self.db();now=self.clock()
  try:
   db.execute('BEGIN IMMEDIATE');row=db.execute('SELECT expires_at FROM dependency_lease WHERE owner=? AND key=?',(owner,self.key)).fetchone()
   if not row or row[0]<=now:db.rollback();return
   failed,opened,probe=db.execute('SELECT failed,open_until,probe_owner FROM dependency_state WHERE key=?',(self.key,)).fetchone()
   db.execute('DELETE FROM dependency_lease WHERE owner=?',(owner,))
   if exc is None:failed=0;opened=0
   elif retryable(exc):
    failed+=1
    if failed>=self.threshold:opened=now+self.cooldown
   db.execute('UPDATE dependency_state SET failed=?,open_until=?,probe_owner=? WHERE key=?',(failed,opened,None if probe==owner else probe,self.key));db.commit()
  except BaseException:db.rollback();raise
  finally:db.close()
 @contextmanager
 def call(self):
  owner=self.claim()
  try:yield
  except BaseException as exc:self.complete(owner,exc);raise
  else:self.complete(owner,None)
