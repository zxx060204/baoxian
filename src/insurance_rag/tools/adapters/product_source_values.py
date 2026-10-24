"""Read current-source literal table windows and exact source values."""
from pydantic import BaseModel,ConfigDict,Field,model_validator
from typing import Literal
from insurance_rag.tools.contracts import result
from insurance_rag.tools.registry import ToolSpec
from insurance_rag.parsing.common import stable_hash

class SourceRequest(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 bundle_id:str=Field(min_length=1,max_length=200)
 action:Literal['tables','read']='tables'
 document_id:str|None=Field(default=None,max_length=200)
 table_id:str|None=Field(default=None,max_length=200)
 text:str=Field(default='',max_length=1000)
 offset:int=Field(default=0,ge=0)
 limit:int=Field(default=20,ge=1,le=100)
 row_start:int=Field(default=0,ge=0)
 row_count:int=Field(default=20,ge=1,le=200)
 column_start:int=Field(default=0,ge=0)
 column_count:int=Field(default=20,ge=1,le=200)
 @model_validator(mode='after')
 def query_scope(self):
  if self.action=='read' and (not self.document_id or not self.table_id):raise ValueError('document_and_table_required')
  if self.row_count*self.column_count>4000:raise ValueError('table_window_too_large')
  return self

def query_source(args,c):
 from insurance_rag.ingestion.store import bundle
 from insurance_rag.ingestion.source_table_store import list_tables,read_table
 parent=bundle(c['app'],c['owner'],args['bundle_id'])
 if parent['content'].get('scope')!='current_source_business':raise ValueError('retired_bundle_requires_migration')
 binding=parent['content'].get('tables')
 if not binding:return result('unavailable',reason_code='literal_tables_not_imported')
 bid=binding['table_batch_id']
 if args['action']=='tables':
  data=list_tables(c['app'].store.db,bid,text=args.get('text',''),offset=args.get('offset',0),limit=args.get('limit',20));return result('ok' if data['status']=='matched' else 'no_evidence',data={'source_tables':data,'next_action':'read'})
 data=read_table(c['app'].store.db,bid,args['document_id'],args['table_id'],**{k:args.get(k,default) for k,default in [('row_start',0),('row_count',20),('column_start',0),('column_count',20)]})
 return result('ok' if data['status']=='matched' else 'no_evidence',data={'source_table':data})

def spec():return ToolSpec('query_product_source_values','读取新来源原文表格。先action=tables取得表和页码，再action=read提供document_id、table_id和从0开始的行列窗口；按has_more继续。个人精确查表使用query_product_values，不把原文窗口当个人报价。',SourceRequest,query_source,timeout=120,version='3')
