"""CLI sharing the native AgentEngine with API and the isolated worker."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.agent.loop_bootstrap import open_loop_application
from insurance_rag.agent.model_adapter import strict_json
p=argparse.ArgumentParser();p.add_argument('message',nargs='?',default='');p.add_argument('--owner',default='local-user');p.add_argument('--thread');p.add_argument('--run');p.add_argument('--response');p.add_argument('--product-version-id');p.add_argument('--conditions',default='{}');a=p.parse_args()
with open_loop_application() as app:
 if a.run:
  current=app.store.get(a.run,a.owner)
  if a.response:
   import uuid
   r=app.drive(a.run,a.owner,response=strict_json(a.response),resume_id=uuid.uuid4().hex,revision=current['revision'],interrupt_id=current['clarification']['interrupt_id'])
  else:r=app.drive(a.run,a.owner)
 else:
  thread=a.thread or app.new_thread(a.owner);created=app.submit(thread,a.owner,a.message,product_version_id=a.product_version_id,conditions=strict_json(a.conditions));r=app.drive(created['run_id'],a.owner)
 print(json.dumps(r,ensure_ascii=False,indent=2))
