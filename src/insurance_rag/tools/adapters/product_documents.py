"""Document intake/status and source questions; no illustration actions."""
from typing import Literal
from pydantic import BaseModel,ConfigDict,Field,model_validator
from types import SimpleNamespace
from insurance_rag.tools.contracts import result
from insurance_rag.tools.registry import ToolSpec
from insurance_rag.api.product_ingestion import AssetInput
from insurance_rag.ingestion.store import create_job
from insurance_rag.ingestion.service import launch,analyze
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import stable_hash
from insurance_rag.agent.store import Forbidden
class ProductDocuments(BaseModel):
 model_config=ConfigDict(extra='forbid',strict=True)
 action:Literal['prepare','status','analyze']
 assets:list[AssetInput]=Field(default_factory=list,max_length=12)
 product_name:str=Field(default='',max_length=300,description='仅填写已从原件核验的完整名称；不确定留空，禁止泛称或占位名称。')
 company:str=Field(default='',max_length=300,description='仅填写原件核验的公司名称；不确定留空。')
 version_label:str=Field(default='',max_length=300)
 job_id:str|None=None
 bundle_id:str|None=None
 question:str=Field(default='',max_length=2000)
 @model_validator(mode='after')
 def valid(self):
  if self.action=='prepare' and (not self.assets or self.job_id or self.bundle_id or self.question):raise ValueError('prepare_assets_required')
  if self.action=='status' and (not self.job_id or self.assets or self.bundle_id or self.question):raise ValueError('status_job_required')
  if self.action=='analyze' and (not self.bundle_id or not self.question.strip() or self.assets or self.job_id):raise ValueError('analysis_bundle_required')
  return self
class ChargedModel:
 def __init__(self,engine,run):self.engine=engine;self.run=run;self.binding=engine.model.binding
 @property
 def usage(self):return self.engine.model.usage
 def structured(self,prompt,payload):return self.engine.structured(self.run,prompt,payload)

from insurance_rag.ingestion.readiness import task_summary

def product_documents(args,c):
 args=ProductDocuments.model_validate(args).model_dump()
 engine=c['app'];store=IntakeStore(engine.store);owner=c['owner'];run=c['run'];app=SimpleNamespace(root=engine.root,store=engine.store,release_factory=getattr(engine,'release_factory',None),tools_factory=getattr(engine,'tools_factory',None))
 if args['action']=='prepare':
  allowed={e['asset_id'] for e in run['input'].get('attachments',[])}
  if any(e['asset_id'] not in allowed for e in args['assets']):raise Forbidden('attachment_not_in_current_request')
  row=create_job(app,owner,'agent-'+run['run_id']+'-'+c['call_id'][-20:],args['assets'],product_name=args['product_name'],company=args['company'],version_label=args['version_label']);job=task_summary(row)
  if row['state'] not in ('ready','partial','failed'):
   store.create('bundle_subscription','subscription:'+stable_hash([run['run_id'],row['id']]),owner,'',{'job_id':row['id'],'run_id':run['run_id']},'pending')
  if row['state']=='queued':launch(engine.root,owner,row['id'])
  ready=row['state'] in ('ready','partial')
  if not ready and row['state']!='failed':c['state']['pending_document_job']=row['id']
  elif ready and job['bundle_id']:c['state']['document_bundle_id']=job['bundle_id']
  return result('unavailable' if row['state']=='failed' else 'partial' if row['state']=='partial' or not ready else 'ok',reason_code=job['error'] or '',data={'product_preparation':job})
 if args['action']=='status':
  row=store.get('preparation_job',args['job_id'],owner)
  summary=task_summary(row);ready=row['state'] in ('ready','partial')
  if ready and summary['bundle_id']:c['state']['document_bundle_id']=summary['bundle_id']
  elif not ready and row['state']!='failed':c['state']['pending_document_job']=row['id']
  return result('unavailable' if row['state']=='failed' else 'partial' if row['state']=='partial' or not ready else 'ok',reason_code=summary['error'] or '',data={'product_preparation':summary})
 app.model=ChargedModel(engine,run)
 row=analyze(app,owner,args['bundle_id'],args['question']);value=row['content'];c['state']['document_bundle_id']=args['bundle_id']
 known=c['state'].setdefault('document_products',[])
 if not any(p['bundle_id']==args['bundle_id'] for p in known):known.append({'bundle_id':args['bundle_id'],'product_version_id':value.get('product_version_id'),'product_name':value['product_name']})
 return result('unavailable' if value['status']=='not_prepared' else 'ok' if value['claims'] else 'no_evidence',reason_code='source_index_not_prepared' if value['status']=='not_prepared' else '',data={'document_analysis':{'analysis_id':row['id'],'content_hash':row['content_hash'],'bundle_id':args['bundle_id'],'product_name':value['product_name'],'text':value['text'],'status':value['status'],'claims':[{'quote':q['quote'],'document_version_id':q['source']['document_version_id'],'page_start':q['page_start'],'page_end':q['page_end']} for q in value['claims']]}})

def spec():return ToolSpec('product_documents','资料接入与原文咨询。prepare接入当前附件并自动完成解析、结构映射、原文表格入库、索引、案例、规则、执行绑定和本人资料包登记；共享发布单独操作。status读取实际阶段、bundle_id和各能力缺口。任务完成或部分完成不表示所有指标可算。analyze使用已登记索引回答原文问题。个人测算先get_product_capabilities/get_product_table_rules，再显式查表、计算和组表；本工具不接收个人测算参数，不计算。名称未核验留空。',ProductDocuments,product_documents,read_only=False,side_effect='controlled_write',timeout=120,version='13')
