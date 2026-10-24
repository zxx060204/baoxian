"""Strict boundaries for model proposals and tool evidence."""
from typing import Literal,Any
from pydantic import BaseModel,ConfigDict,Field
class StrictModel(BaseModel):model_config=ConfigDict(extra='forbid',strict=True)
class Claim(StrictModel):
 evidence_id:str
 quote:str=Field(min_length=1,max_length=16000)
class AnswerDraft(StrictModel):claims:list[Claim]=Field(max_length=3)
class ToolResult(StrictModel):
 status:Literal['ok','partial','needs_clarification','no_evidence','conflict','unavailable']
 reason_code:str=''
 data:dict[str,Any]=Field(default_factory=dict)
 evidence:list[dict[str,Any]]=Field(default_factory=list)
 missing_fields:list[str]=Field(default_factory=list)
 warnings:list[str]=Field(default_factory=list)
 retryable:bool=False
 tool_version:str='insurance-tools-v1'
 release_id:str=''

def result(status,**kw):return ToolResult(status=status,**kw).model_dump()
