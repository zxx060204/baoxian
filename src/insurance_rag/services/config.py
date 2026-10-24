"""Service settings; secrets excluded from repr, bindings and diagnostics."""
from dataclasses import dataclass,field
import math,os,re
from urllib.parse import urlparse
from insurance_rag.parsing.common import stable_hash

def _integer(env,name,default,minimum,maximum):
 try:value=int(env.get(name,str(default)))
 except (TypeError,ValueError):raise ValueError('invalid_'+name) from None
 if not minimum<=value<=maximum:raise ValueError('invalid_'+name)
 return value

def _seconds(env,name,default,maximum):
 try:value=float(env.get(name,str(default)))
 except (TypeError,ValueError):raise ValueError('invalid_'+name) from None
 if not math.isfinite(value) or not 0<value<=maximum:raise ValueError('invalid_'+name)
 return value

@dataclass(frozen=True)
class ChatSettings:
 provider:str
 base_url:str
 model:str
 api_key:str=field(repr=False)
 thinking:bool=False
 timeout_seconds:float=30
 max_output_tokens:int=2048
 retries:int=1
 @classmethod
 def from_environment(cls,env=None):
  env=os.environ if env is None else env
  provider=env.get('INSURANCE_CHAT_PROVIDER','deepseek')
  if provider!='deepseek':raise ValueError('unsupported_chat_provider')
  base=env.get('DEEPSEEK_BASE_URL','https://api.deepseek.com').rstrip('/')
  try:
   u=urlparse(base)
   valid=u.scheme=='https' and u.hostname=='api.deepseek.com' and u.port in (None,443) and not u.username and not u.password and not u.query and not u.fragment and u.path in ('','/v1')
  except ValueError:valid=False
  if not valid:raise ValueError('invalid_DEEPSEEK_BASE_URL')
  model=env.get('DEEPSEEK_CHAT_MODEL','deepseek-flash')
  if model!='deepseek-flash':raise ValueError('unsupported_deepseek_flash_model')
  thinking=env.get('DEEPSEEK_THINKING_ENABLED','false').lower()
  if thinking not in ('true','false'):raise ValueError('invalid_DEEPSEEK_THINKING_ENABLED')
  key=env.get('DEEPSEEK_API_KEY','')
  if not isinstance(key,str) or not key.strip() or any(c.isspace() for c in key):raise ValueError('missing_or_invalid_DEEPSEEK_API_KEY')
  return cls(provider,base,model,key,thinking=='true',_seconds(env,'DEEPSEEK_CHAT_TIMEOUT_SECONDS',30,120),_integer(env,'DEEPSEEK_CHAT_MAX_OUTPUT_TOKENS',2048,1,8192),_integer(env,'DEEPSEEK_CHAT_RETRIES',1,0,3))
 @property
 def binding(self):
  return {'provider':self.provider,'model':self.model,'endpoint_hash':stable_hash(self.base_url),'thinking':self.thinking,'timeout_seconds':self.timeout_seconds,'max_output_tokens':self.max_output_tokens,'retries':self.retries,'config_version':2}

@dataclass(frozen=True)
class AgentSettings:
 environment:str='dev-reliability'
 @classmethod
 def from_environment(cls,env=None):
  env=os.environ if env is None else env
  environment=env.get('INSURANCE_AGENT_ENVIRONMENT','dev-reliability')
  if not re.fullmatch(r'[a-zA-Z0-9_-]{1,160}',environment):raise ValueError('invalid_INSURANCE_AGENT_ENVIRONMENT')
  return cls(environment)
 @property
 def binding(self):return {'environment':self.environment,'config_version':2}
