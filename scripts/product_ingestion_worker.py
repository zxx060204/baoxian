"""Run the existing durable private-product preparation workflow."""
import json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))

def main():
 spec=json.loads(sys.stdin.buffer.read(16385))
 if not isinstance(spec,dict) or set(spec)!={'owner','job_id'} or not all(isinstance(v,str) and v for v in spec.values()):raise ValueError('invalid_preparation_spec')
 from insurance_rag.services.bootstrap import open_services
 from insurance_rag.ingestion.service import run_job
 with open_services() as app:
  try:run_job(app,spec['owner'],spec['job_id'])
  finally:
   from insurance_rag.ingestion.subscriptions import wake_preparation_subscribers
   wake_preparation_subscribers(app,spec['owner'],spec['job_id'])
if __name__=='__main__':main()
