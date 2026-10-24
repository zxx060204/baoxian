"""Current-source business tools and shared personal assistant operations."""
from typing import Any,Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from insurance_rag.tools.registry import ToolSpec,ToolRegistry
from insurance_rag.tools.contracts import result
from insurance_rag.parsing.common import stable_hash
from insurance_rag.agent.store import Forbidden
from insurance_rag.tools.adapters.capabilities import ProductScope,capability_view

class Input(BaseModel):model_config=ConfigDict(strict=True,extra='forbid')

class Resolve(Input):hint:str|None=Field(default=None,max_length=200,description='仅填写实际产品名称片段；列出全部或已发布产品时省略，不填写状态或查询指令。')

class Read(Input):data_ref:str=Field(min_length=1,max_length=200);evidence_id:str|None=None;offset:int=Field(default=0,ge=0);limit:int=Field(default=8000,ge=100,le=16000)

class Clarify(Input):question:str=Field(min_length=1,max_length=1000);missing_fields:list[str]=Field(min_length=1,max_length=10);choices:list[dict[str,Any]]=Field(default_factory=list,max_length=30)
Dimension=Literal['coverage_period','coverage','exclusions','waiting_period','renewal','eligibility','deductible','disclosure','premium_rate','cash_value']

class Memory(Input):
 action:Literal['search','remember','update','forget']
 query:str=Field(default='',max_length=200)
 content:str=Field(default='',max_length=2000)
 memory_id:str|None=Field(default=None,description='update/forget必填，使用memory快照或search中的ID。')
 expected_revision:int|None=Field(default=None,ge=1,description='update/forget必填，复制该memory记录的revision；勿自行猜测。')
 category:Literal['preference','interaction']='preference'
 @model_validator(mode='after')
 def action_fields(self):
  if self.action=='search':
   if len(self.query.strip())<2 or self.content or self.memory_id is not None or self.expected_revision is not None:raise ValueError('invalid_memory_search')
  elif self.action=='remember':
   if not self.content.strip() or self.query or self.memory_id is not None or self.expected_revision is not None:raise ValueError('invalid_memory_remember')
  elif not self.memory_id or self.expected_revision is None or self.query or self.action=='update' and not self.content.strip() or self.action=='forget' and self.content:raise ValueError('invalid_memory_change')
  return self

class Files(Input):path:str=Field(min_length=1,max_length=200);offset:int=Field(default=0,ge=0);limit:int=Field(default=8000,ge=1,le=16000)

class Write(Input):path:str;content:str=Field(max_length=200000);expected_version:str|None=None

class Patch(Input):path:str;old:str=Field(min_length=1,max_length=10000);new:str=Field(max_length=10000);expected_version:str

class Empty(Input):pass


def resolve(args,c):
 import re
 from insurance_rag.ingestion.library import discover
 products=discover(c,args['hint'])
 if args['hint'] and not products:
  catalog=discover(c)
  if catalog:return result('no_evidence',reason_code='product_name_filter_no_match',data={'name_filter':args['hint'],'available_product_count':len(catalog),'instruction':'名称筛选未匹配，但可访问目录并非空。浏览全部产品请省略 hint 重试；不要据此声称没有已发布产品。'})
 compact=lambda value:re.sub(r'[\s（）()]','',value)
 texts=[m['content'] for m in c['app'].journal.messages(c['run']) if m['role']=='user']
 if len(products)==1 and products[0].get('bundle_id'):
  c['state']['document_bundle_id']=products[0]['bundle_id']
  known=c['state'].setdefault('document_products',[])
  if not any(p['bundle_id']==products[0]['bundle_id'] for p in known):known.append({k:products[0][k] for k in ('bundle_id','product_version_id','product_name')})
 if len(products)==1 and args['hint'] and any(compact(args['hint']) in compact(text) for text in texts):
  resolved=c['state'].setdefault('resolved_products',[])
  if products[0]['product_version_id'] not in resolved:resolved.append(products[0]['product_version_id'])
  if not c['run']['input'].get('product_version_id'):c['state']['products']=[products[0]['product_version_id']]
 return result('ok',data={'products':products,'requires_user_selection':len(products)>1})

def capabilities(args,c):return capability_view(args,c)


def read(args,c):
 packet=c['app'].packet(args['data_ref'],c)
 if args.get('evidence_id'):
  evidence=next((e for e in packet.get('evidence',[]) if e['evidence_id']==args['evidence_id']),None)
  if not evidence:raise Forbidden('evidence_unavailable')
  c['tools'].validate_source(evidence);text=evidence['text'];start=args['offset'];end=min(len(text),start+min(args['limit'],8000))
  return result('ok',data={'original_data_ref':args['data_ref'],'evidence_id':evidence['evidence_id'],'text':text[start:end],'offset':start,'has_more':end<len(text),'textual_clause_complete':start==0 and end==len(text) and evidence.get('textual_clause_complete',False)},evidence=[],warnings=['excerpt_requires_full_source_for_conditions'] if start or end<len(text) else [])
 if packet.get('evidence'):
  return result('ok',data={'original_data_ref':args['data_ref'],'evidence_catalog':[{'evidence_id':e['evidence_id'],'product_version_id':e['product_version_id'],'characters':len(e['text']),'preview':e['text'][:150],'clause_title':e.get('clause_title'),'page_start':e['page_start'],'page_end':e['page_end']} for e in packet['evidence']],'next_action':'read_evidence_with_evidence_id'})
 import json
 def safe(value):
  if isinstance(value,dict):return {k:safe(v) for k,v in value.items() if k not in ('file_path','source_file','layout_file','_audit')}
  if isinstance(value,list):return [safe(v) for v in value]
  return value
 text=json.dumps(safe(packet),ensure_ascii=False);start=args['offset'];end=min(len(text),start+min(args['limit'],8000))
 return result('ok',data={'original_data_ref':args['data_ref'],'content':text[start:end],'offset':start,'has_more':end<len(text)})


def clarification(args,c):
 c['state']['clarification']={**args,'interrupt_id':stable_hash([c['run']['run_id'],args,c['state'].get('clarifications',0)])}
 return result('needs_clarification',reason_code='user_input_required',data=args,missing_fields=args['missing_fields'])

def memory(args,c):return c['app'].memory_tool(args,c)

def workspace(c):
 from insurance_rag.workspace.files import LocalWorkspace
 return LocalWorkspace(c['app'].root,c['owner'],c['run']['thread_id'],c['app'].store,c['run'])

def list_files(args,c):return result('ok',data={'files':workspace(c).list()})

def read_file(args,c):return result('ok',data=workspace(c).read(args['path'],args['offset'],args['limit']))

def write_file(args,c):return result('ok',data=workspace(c).write(args['path'],args['content'],args['expected_version'],c['call_id']))

def patch_file(args,c):
 w=workspace(c);prior=w.read(args['path'],0,200000)
 if prior['version']!=args['expected_version']:
  import json
  intents=c['app'].store.execute("SELECT result FROM insurance_agent_tool_invocation WHERE run_id=? AND kind='workspace_write' AND state='pending'",(c['run']['run_id'],)).fetchall()
  for row in intents:
   intent=json.loads(row[0])
   if intent.get('operation')==c['call_id'] and intent.get('path')==args['path'] and intent.get('expected_version')==args['expected_version'] and intent.get('version')==prior['version']:return result('ok',data=w.write(args['path'],prior['content'],args['expected_version'],c['call_id']))
  return result('conflict',reason_code='workspace_version_conflict')
 if prior['content'].count(args['old'])!=1:return result('conflict',reason_code='patch_requires_unique_match')
 return result('ok',data=w.write(args['path'],prior['content'].replace(args['old'],args['new'],1),args['expected_version'],c['call_id']))



def registry():
 entries=[('resolve_products','定位本人或共享资料包；歧义时询问用户，不猜ID。',Resolve,resolve),
 ('get_product_capabilities','只读逐产品/目标的资料和执行能力；输入bundle_id或product_version_id二选一，conditions仅预览，不保存画像。',ProductScope,capabilities),
 ('read_evidence','分页读取本次已取得的工具结果或原文证据。',Read,read),
 ('request_clarification','追问必要条件并持久等待。',Clarify,clarification),
 ('memory','搜索偏好和历史。remember传content；search传query。update/forget必须传memory_id及expected_revision（取快照或search的revision）；forget不传content/query。',Memory,memory),
 ('list_files','列出授权本地目录内Markdown/CSV文件。',Empty,list_files),
 ('read_file','分页读取授权目录内Markdown/CSV，全文只用于当前分析。',Files,read_file),
 ('write_file','创建或按预期版本替换本地Markdown/CSV。',Write,write_file),
 ('patch_file','按预期版本唯一替换本地Markdown/CSV片段。',Patch,patch_file)]
 from .product_documents import spec as document_spec
 from .product_table_rules import spec as rules_spec
 from .product_source_values import spec as source_spec
 from .product_table_steps import specs as value_specs
 from .profile import specs as profile_specs
 from .analysis import specs as analysis_specs
 from .result_comparison import spec as comparison_spec
 return ToolRegistry([document_spec(),rules_spec(),source_spec(),*value_specs(),*profile_specs(),*analysis_specs(),comparison_spec()]+[ToolSpec(name,description,model,handler,version='3',read_only=name not in ('request_clarification','memory','write_file','patch_file'),cache_result=name not in ('read_file','list_files'),side_effect='controlled_write' if name in ('memory','write_file','patch_file') else 'none') for name,description,model,handler in entries])
