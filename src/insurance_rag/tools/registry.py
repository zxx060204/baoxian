"""Fixed, explicitly assembled business tool definitions; no plugin discovery."""
from dataclasses import dataclass
from typing import Callable
from pydantic import BaseModel
@dataclass(frozen=True)
class ToolSpec:
 name:str
 description:str
 input_model:type[BaseModel]
 handler:Callable
 read_only:bool=True
 timeout:float=10
 version:str='1'
 side_effect:str='none'
 cache_result:bool=True
 permissions:tuple=('business_read',)
 idempotency_policy:str='run_conditions_release'
 availability_check:Callable|None=None
 def schema(self):return {'type':'function','function':{'name':self.name,'description':self.description,'parameters':self.input_model.model_json_schema()}}
class ToolRegistry:
 def __init__(self,specs):
  self.specs={}
  for spec in specs:
   if spec.name in self.specs:raise ValueError('duplicate_tool')
   self.specs[spec.name]=spec
 def schemas(self,context):return [spec.schema() for spec in self.specs.values() if not spec.availability_check or spec.availability_check(context)]
 @property
 def versions(self):return {name:spec.version for name,spec in self.specs.items()}
