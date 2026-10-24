"""Versioned execution limits for the native Agent loop."""
from dataclasses import dataclass
from insurance_rag.services.config import _integer,_seconds
import os
@dataclass(frozen=True)
class LoopSettings:
 max_steps:int=12
 max_llm_calls:int=12
 max_tool_calls:int=24
 max_repairs:int=2
 max_clarifications:int=3
 timeout_seconds:float=180
 context_bytes:int=96000
 max_total_tokens:int=500000
 @classmethod
 def from_environment(cls,env=None):
  env=os.environ if env is None else env
  return cls(_integer(env,'INSURANCE_LOOP_MAX_STEPS',12,1,30),_integer(env,'INSURANCE_LOOP_MAX_MODEL_CALLS',12,1,40),_integer(env,'INSURANCE_LOOP_MAX_TOOL_CALLS',24,1,60),_integer(env,'INSURANCE_LOOP_MAX_REPAIRS',2,0,4),_integer(env,'INSURANCE_LOOP_MAX_CLARIFICATIONS',3,1,8),_seconds(env,'INSURANCE_LOOP_TIMEOUT_SECONDS',180,300),_integer(env,'INSURANCE_LOOP_CONTEXT_BYTES',96000,8000,200000),_integer(env,'INSURANCE_LOOP_MAX_TOTAL_TOKENS',500000,1000,1000000))
 @property
 def binding(self):return dict(self.__dict__,limits_version=2)
