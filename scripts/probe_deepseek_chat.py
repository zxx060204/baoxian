"""Minimal configured-model probe, without insurance documents or secrets."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.runtime_env import load_env
from insurance_rag.services.config import ChatSettings
from insurance_rag.agent.models import DeepSeekChat
from insurance_rag.parsing.common import write_json,workspace_path
from insurance_rag.reliability.runtime import request_budget,error_code
p=argparse.ArgumentParser();p.add_argument('--output',required=True);a=p.parse_args();root=Path(__file__).resolve().parents[1];load_env(root/'.env');provider=DeepSeekChat(ChatSettings.from_environment())
try:
 with request_budget(40):r=provider.structured('Return JSON {"status":"ok"}.',{'message':'接口连通性测试，无业务资料。'})
 report={'status':'passed' if r=={'status':'ok'} else 'failed','binding':provider.binding,'usage':provider.usage,'insurance_material_sent':False}
except Exception as exc:report={'status':'failed','error_code':error_code(exc),'binding':provider.binding,'usage':provider.usage}
write_json(workspace_path(a.output,root),report);print(json.dumps(report,ensure_ascii=False))
if report['status']!='passed':raise SystemExit(1)
