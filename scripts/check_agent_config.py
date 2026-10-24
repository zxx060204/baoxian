"""Offline configuration validation. Never calls a model or prints credentials."""
import argparse,json,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'src'))
from insurance_rag.runtime_env import load_env
from insurance_rag.parsing.common import workspace_path,stable_hash
from insurance_rag.services.config import ChatSettings,AgentSettings
from insurance_rag.agent.loop_config import LoopSettings

def main():
 p=argparse.ArgumentParser(description=__doc__);p.add_argument('--env-file',default='.env');a=p.parse_args();root=Path(__file__).resolve().parents[1]
 try:
  load_env(workspace_path(a.env_file,root,must_exist=True));chat=ChatSettings.from_environment();agent=AgentSettings.from_environment();binding={'chat':chat.binding,'agent':agent.binding,'loop':LoopSettings.from_environment().binding}
  result={'status':'configuration_valid','model':chat.model,'provider':chat.provider,'agent_environment':agent.environment,'binding':binding,'config_hash':stable_hash(binding),'remote_requests':0,'model_connection_tested':False}
 except ValueError as exc:
  allowed={'missing_or_invalid_DEEPSEEK_API_KEY','unsupported_chat_provider','unsupported_deepseek_flash_model'}
  code=str(exc)
  result={'status':'needs_configuration','error_code':code if code in allowed or code.startswith('invalid_DEEPSEEK_') or code.startswith(('invalid_INSURANCE_AGENT_','invalid_INSURANCE_LOOP_')) else 'invalid_configuration','remote_requests':0,'model_connection_tested':False}
 print(json.dumps(result,ensure_ascii=False,indent=2))
 if result['status']!='configuration_valid':raise SystemExit(1)
if __name__=='__main__':main()
