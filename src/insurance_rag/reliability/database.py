"""Shared database connection and transaction primitives, without legacy release/task tables."""
import json,sqlite3,time
from contextlib import contextmanager
from pathlib import Path

def encode(data):return json.dumps(data,ensure_ascii=False,sort_keys=True,allow_nan=False)

class DatabaseStore:
 @classmethod
 def connect(cls,dsn,**kw):
  import psycopg
  return cls(psycopg.connect(dsn,autocommit=True,connect_timeout=5),postgres=True,**kw)

 def close(self):self.db.close()

 def execute(self,sql,args=()):
  if self.postgres:sql=sql.replace('?','%s')
  return self.db.execute(sql,args)

 @contextmanager
 def transaction(self):
  if self.postgres:
   with self.db.transaction():yield
  else:
   self.db.execute('BEGIN IMMEDIATE')
   try:yield;self.db.commit()
   except BaseException:self.db.rollback();raise

