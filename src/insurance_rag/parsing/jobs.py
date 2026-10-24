"""Local job ledger. Finished jobs remain quarantined for human review."""
from __future__ import annotations

import json
import sqlite3
import uuid
import time
from datetime import datetime, timezone
from pathlib import Path

from .common import stable_hash


def utc_now():
    return datetime.now(timezone.utc).isoformat()


class JobLedger:
    def __init__(self, path):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path), timeout=30)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.executescript("""
            CREATE TABLE IF NOT EXISTS jobs (
              job_id TEXT PRIMARY KEY, payload TEXT NOT NULL,
              state TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
              owner TEXT, pid INTEGER, updated_at TEXT NOT NULL, result TEXT);
            CREATE TABLE IF NOT EXISTS events (
              event_id INTEGER PRIMARY KEY, job_id TEXT NOT NULL,
              occurred_at TEXT NOT NULL, state TEXT NOT NULL, detail TEXT NOT NULL);
        """)
        self.db.commit()

    def close(self):
        self.db.close()

    def _event(self, job_id, state, detail):
        self.db.execute("INSERT INTO events(job_id,occurred_at,state,detail) VALUES(?,?,?,?)",
                        (job_id, utc_now(), state, json.dumps(detail, ensure_ascii=False)))

    def enqueue(self, payload):
        job_id = stable_hash(payload)
        with self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO jobs(job_id,payload,state,updated_at) VALUES(?,?,?,?)",
                (job_id, json.dumps(payload, ensure_ascii=False), "pending", utc_now()))
            if cursor.rowcount:
                self._event(job_id, "pending", {})
        return job_id

    def claim(self):
        self.db.execute("BEGIN IMMEDIATE")
        try:
            row = self.db.execute(
                "SELECT * FROM jobs WHERE state='pending' ORDER BY rowid LIMIT 1").fetchone()
            if row is None:
                self.db.commit()
                return None
            owner = uuid.uuid4().hex
            self.db.execute("UPDATE jobs SET state='running',owner=?,pid=NULL,"
                            "attempts=attempts+1,updated_at=? WHERE job_id=?",
                            (owner, utc_now(), row["job_id"]))
            self._event(row["job_id"], "running", {"owner": owner})
            self.db.commit()
            return {"job_id": row["job_id"], "owner": owner,
                    "payload": json.loads(row["payload"])}
        except BaseException:
            self.db.rollback()
            raise

    def heartbeat(self, job, pid):
        with self.db:
            cursor = self.db.execute("UPDATE jobs SET pid=?,updated_at=? WHERE job_id=? "
                                    "AND owner=? AND state='running'",
                                    (pid, utc_now(), job["job_id"], job["owner"]))
            if cursor.rowcount != 1:
                raise RuntimeError("Job ownership was lost")

    def finish(self, job, state, result):
        if state not in {"needs_review", "failed", "unsupported_format"}:
            raise ValueError("Invalid terminal state")
        with self.db:
            cursor = self.db.execute("UPDATE jobs SET state=?,result=?,updated_at=? "
                                    "WHERE job_id=? AND owner=? AND state='running'",
                                    (state, json.dumps(result, ensure_ascii=False), utc_now(),
                                     job["job_id"], job["owner"]))
            if cursor.rowcount != 1:
                raise RuntimeError("Job ownership was lost")
            self._event(job["job_id"], state, result)

    def retry(self, job_id, max_attempts=3):
        if isinstance(max_attempts, bool) or max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        with self.db:
            cursor = self.db.execute("UPDATE jobs SET state='pending',owner=NULL,pid=NULL,"
                                    "updated_at=? WHERE job_id=? AND state='failed' AND attempts<?",
                                    (utc_now(), job_id, max_attempts))
            if cursor.rowcount:
                self._event(job_id, "pending", {"reason": "explicit_retry"})
            return bool(cursor.rowcount)

    def list_jobs(self):
        return [dict(row) for row in self.db.execute(
            "SELECT job_id,state,attempts,pid,updated_at,result FROM jobs ORDER BY rowid")]

    def recover_stale(self,is_alive,stale_seconds=300,max_attempts=3):
        """Requeue only an old heartbeat and a provably dead known worker."""
        if stale_seconds<=0 or max_attempts<1:
            raise ValueError('Invalid recovery limits')
        results=[]
        self.db.execute('BEGIN IMMEDIATE')
        try:
            for row in self.db.execute("SELECT * FROM jobs WHERE state='running'").fetchall():
                if time.time()-datetime.fromisoformat(row['updated_at']).timestamp()<stale_seconds:
                    continue
                if row['pid'] is None or is_alive(row['pid']) is not False:
                    results.append({'job_id':row['job_id'],'status':'skipped_worker_alive_or_unknown'});continue
                events=self.db.execute("SELECT occurred_at,detail FROM events WHERE job_id=? AND state='running' ORDER BY event_id DESC",(row['job_id'],)).fetchall()
                claim=next((e for e in events if json.loads(e['detail']).get('owner')==row['owner']),None)
                if claim is None:
                    results.append({'job_id':row['job_id'],'status':'skipped_claim_evidence_missing'});continue
                from .runner import _clean_worker_locks
                try:
                    removed=_clean_worker_locks(json.loads(row['payload']),row['pid'],datetime.fromisoformat(claim['occurred_at']))
                except Exception as exc:
                    results.append({'job_id':row['job_id'],'status':'skipped_lock_cleanup_failed','error':str(exc)});continue
                state='pending' if row['attempts']<max_attempts else 'failed'
                detail={'reason':'abandoned_worker_recovered','previous_owner':row['owner'],'dead_pid':row['pid'],'removed_worker_locks':removed}
                result=None if state=='pending' else json.dumps({'status':'failed','error':'recovery_attempt_limit'},ensure_ascii=False)
                self.db.execute('UPDATE jobs SET state=?,owner=NULL,pid=NULL,updated_at=?,result=? WHERE job_id=? AND owner=?',
                                (state,utc_now(),result,row['job_id'],row['owner']))
                self._event(row['job_id'],state,detail)
                results.append({'job_id':row['job_id'],'status':state,**detail})
            self.db.commit()
        except BaseException:
            self.db.rollback();raise
        return results
