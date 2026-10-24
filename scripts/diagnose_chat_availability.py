import sys,json
from pathlib import Path
from urllib.request import Request,build_opener
from urllib.error import HTTPError,URLError
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.runtime_env import load_env
from insurance_rag.services.config import ChatSettings
load_env(Path(__file__).resolve().parents[1]/'.env');cfg=ChatSettings.from_environment()
try:
 with build_opener().open(Request(cfg.base_url+'/models',headers={'Authorization':'Bearer '+cfg.api_key}),timeout=12) as response:
  data=json.loads(response.read(100000));ids=[x.get('id') for x in data.get('data',[])]
  print(json.dumps({'status':response.status,'configured_model_present':cfg.model in ids,'model_ids':ids}))
except HTTPError as exc:print(json.dumps({'status':exc.code,'error_type':'HTTPError'}))
except Exception as exc:print(json.dumps({'error_type':type(exc).__name__,'reason_type':type(getattr(exc,'reason',None)).__name__}))
