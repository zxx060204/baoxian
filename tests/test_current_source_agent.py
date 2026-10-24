"""Current packages through actual Agent/API contracts; external services are offline doubles."""
import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from contextlib import contextmanager
from unittest.mock import Mock,patch
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from insurance_rag.agent.store import AgentStore,Forbidden
from insurance_rag.onboarding.store import IntakeStore
from insurance_rag.parsing.common import file_hash,stable_hash,write_json
from insurance_rag.ingestion.store import asset_folder,register_asset,owner_key
from insurance_rag.ingestion.mapping import prepare_mapping
from insurance_rag.ingestion.source_publication import prepare_source_bundle,publish_source
from insurance_rag.ingestion.source_examples import prepare_examples
from insurance_rag.ingestion.source_rules import prepare_rules
from insurance_rag.ingestion.source_index import build_source_index
from insurance_rag.ingestion.source_package import load_package
from test_generic_source_mapping import layout,table,cell
from test_native_loop import Scripted,tool,answer

class CurrentSourceAgentTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
  self.store=AgentStore.sqlite(self.root/'state.db');self.owner='alice'
  self.addCleanup(self.temp.cleanup);self.addCleanup(self.store.close)
  self.app=SimpleNamespace(root=self.root,store=self.store,model=Mock(binding={'provider':'offline'}))
  self.app.model.structured.side_effect=lambda prompt,payload:{'source_ids':[payload['sources'][0]['source_id']]}
  folder=asset_folder(self.root,'alice');folder.mkdir(parents=True);pdf=folder/'source.pdf';pdf.write_bytes(b'%PDF-1.7 fixture')
  self.asset=register_asset(self.app,'alice',pdf);self.ref=self.make_package('current-v','新来源产品')
  self.embedding=SimpleNamespace(binding={'provider':'offline','dimension':2})
  self.providers=patch('insurance_rag.ingestion.source_index.providers',return_value=(Mock(),self.embedding,Mock()))
  self.providers.start();self.addCleanup(self.providers.stop)
  @contextmanager
  def application():yield self.app
  api=FastAPI()
  @api.exception_handler(Forbidden)
  async def forbidden(request,exc):return JSONResponse({'detail':str(exc)},status_code=404)
  @api.exception_handler(ValueError)
  async def invalid(request,exc):return JSONResponse({'detail':str(exc)},status_code=422)
  from insurance_rag.api.product_ingestion import install
  install(api,application,lambda:self.owner,start_workers=False,library_admin=True)
  self.client=TestClient(api)
 def tearDown(self):self.client.close()
 def make_package(self,version,name):
  t=table([cell(0,0,'保单年度'),cell(0,1,'年初保费')]+[c for i in range(1,26) for c in (cell(i,0,str(i)),cell(i,1,'100'))],26,2)
  data=layout([t],'brochure');data['document_version_id']='doc-'+version
  data['metadata'].update(product_id='p',product_version_id=version,product_name=name,company_id='c')
  data['blocks']=[{'block_id':'text','block_type':'paragraph','raw_text':'保险期间为终身。具体保障应以合同约定为准。','page_number':1,'reading_order':0,'bounding_box':[0,300,200,330]}]
  lp=self.root/(version+'.json');write_json(lp,data)
  doc={'document_type':'brochure','document_version_id':'doc-'+version,'company_id':'c',
       'asset_id':self.asset['id'],'source_file':self.asset['content']['path'],'source_sha256':self.asset['content']['sha256'],
       'layout_file':lp.name,'layout_sha256':file_hash(lp),'pages':1}
  return prepare_mapping(self.app,'alice',{'identity':{'product_id':'p','product_version_id':version,
      'product_name':name,'space_id':owner_key('alice')},'documents':[doc]},self.root)['package_ref']
 def indexed_bundle(self,ref=None):
  ref=ref or self.ref
  def build(client,embedding,folder,chunks,scope):
   config=stable_hash(['source-package-index-v1',scope,embedding.binding])
   return {'index':'insurance-source-'+config[:48],'config_hash':config,'embedding_binding':embedding.binding,
           'chunk_count':len(chunks),'integrity':'matched'}
  with patch('insurance_rag.ingestion.source_index.build_scoped_index',side_effect=build):idx=build_source_index(self.app,ref)
  self.chunks=json.loads((self.root/idx['chunks_ref']['path']).read_text('utf8'))
  examples=prepare_examples(self.root,ref);rules=prepare_rules(self.app,ref,index_ref=idx['result_ref'])
  parent=prepare_source_bundle(self.app,'alice',ref,index_ref=idx['result_ref'],rules_ref=rules['result_ref'],examples_ref=examples['result_ref'])
  return parent,idx
 @contextmanager
 def search(self):
  found={'hits':[{'record':self.chunks[0],'score':1}], 'routes':['bm25','vector'],
         'candidate_counts':{'bm25':1,'vector':1},'degraded':False}
  with patch('insurance_rag.indexing.opensearch.SearchService') as service,patch('insurance_rag.retrieval.reranking.rerank',side_effect=lambda query,hits,provider,**kw:hits):
   service.return_value.search.return_value=found;yield service
 def engine(self,steps):
  from insurance_rag.agent.engine import AgentEngine
  from support_fixture_tools import FixtureTools
  from insurance_rag.services.config import AgentSettings
  tools=FixtureTools();model=Scripted(steps)
  model.structured=lambda prompt,payload,**kw:{'source_ids':[payload['sources'][0]['source_id']]}
  return AgentEngine(self.store,AgentSettings(),model,lambda:tools.release,lambda r:tools,root=self.root)
 def drive(self,engine,message='查看原文资料'):
  thread=engine.new_thread('alice');r=engine.submit(thread,'alice',message)
  return engine.drive(r['run_id'],'alice')
 def test_current_index_answers_through_api_and_agent_and_history(self):
  parent,idx=self.indexed_bundle();bid=parent['id'];before=parent['content_hash']
  with self.search() as service:
   response=self.client.post('/v1/product-ingestion/bundles/'+bid+'/analyze',json={'question':'保险期间是什么？'})
   self.assertEqual(response.status_code,200,response.text);value=response.json()
   self.assertEqual(value['status'],'source_answer');self.assertEqual(value['claims'][0]['page_start'],1)
   self.assertIn('保险期间为终身',value['text']);self.assertEqual(service.call_args.args[1]['chunk'],idx['index'])
   engine=self.engine([tool('product_documents',{'action':'analyze','bundle_id':bid,'question':'说明保险期间'}),answer])
   result=self.drive(engine);self.assertEqual(result['state'],'completed',result)
   self.assertIn('保险期间为终身',result['result']['document_analysis']['text'])
   history_engine=self.engine([tool('read_analysis',{'analysis_id':result['result']['analysis_id']}),answer])
   history=self.drive(history_engine,'继续历史分析');self.assertEqual(history['state'],'completed',history)
  self.assertEqual(IntakeStore(self.store).get('product_bundle',bid)['content_hash'],before)
 def test_status_agrees_across_agent_and_api_without_personal_execution(self):
  parent,_=self.indexed_bundle();bid=parent['id']
  detail=self.client.get('/v1/product-ingestion/bundles/'+bid).json()
  c=detail['capabilities'];self.assertEqual(c['document_qa']['status'],'available')
  self.assertEqual(c['rule_extraction']['rule_count'],1);self.assertEqual(c['source_examples']['case_count'],1)
  self.assertEqual(c['illustration']['status'],'not_prepared')
  engine=self.engine([tool('get_product_capabilities',{'bundle_id':bid}),answer]);result=self.drive(engine)
  self.assertEqual(result['state'],'completed',result)
  entries={e['capability']:e for e in result['result']['capability_view']['capabilities']}
  self.assertEqual(entries['document_qa']['status'],'ready');self.assertEqual(entries['source_examples']['details']['case_count'],1)
  self.assertEqual(entries['personal_illustration']['details']['reason_code'],'current_source_execution_not_prepared')
  self.assertEqual(entries['personal_illustration']['status'],'needs_implementation')
  from insurance_rag.agent.context import ContextEngine
  wire=ContextEngine().wire({'status':'ok','data':{'capability_view':result['result']['capability_view']}},'ref')
  compact={e['capability']:e for e in wire['data']['capability_view']['capabilities']}
  self.assertEqual(compact['personal_illustration']['reason_code'],'current_source_execution_not_prepared')
  self.assertEqual(compact['rule_extraction']['failed_count'],0)
  for route in ('illustration-profile','benefits','coverage'):
   r=self.client.get('/v1/product-ingestion/bundles/'+bid+'/'+route);self.assertEqual(r.status_code,404,r.text)
  r=self.client.post('/v1/product-ingestion/bundles/'+bid+'/illustrate',json={'inputs':{}})
  self.assertEqual(r.status_code,422,r.text);self.assertEqual(r.json()['detail'],'current_source_execution_not_prepared')
 def test_retired_bundle_never_starts_current_retrieval(self):
  from insurance_rag.ingestion.retrieval import retrieve
  with patch('insurance_rag.ingestion.source_index.retrieve_source',side_effect=AssertionError('retired source executed')):
   with self.assertRaisesRegex(ValueError,'retired_bundle_requires_migration'):
    retrieve(self.app,{'retrieval':{'index':'insurance-private-old'}},'保险期间？')
 def test_missing_index_never_calls_model_or_falls_back_to_old_index(self):
  parent=prepare_source_bundle(self.app,'alice',self.ref);bid=parent['id']
  with patch('insurance_rag.ingestion.source_index.providers',side_effect=AssertionError('unexpected retrieval without index')):
   response=self.client.post('/v1/product-ingestion/bundles/'+bid+'/analyze',json={'question':'保险期间？'})
  self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['status'],'not_prepared')
  self.app.model.structured.assert_not_called()
 def test_personal_tools_return_explicit_not_prepared_without_old_profile(self):
  from insurance_rag.tools.adapters.product_table_steps import query_product_values,calculate_product_values,build_benefit_table
  parent=prepare_source_bundle(self.app,'alice',self.ref)
  ctx={'app':self.app,'owner':'alice'}
  with patch('insurance_rag.tools.adapters.product_table_steps.profile',create=True,side_effect=AssertionError('old profile')):
   for fn in (query_product_values,calculate_product_values,build_benefit_table):
    r=fn({'bundle_id':parent['id']},ctx);self.assertEqual(r['reason_code'],'current_source_execution_not_prepared')
 def test_private_source_isolation_and_explicit_publication(self):
  parent,_=self.indexed_bundle();bid=parent['id'];self.owner='bob'
  self.assertEqual(self.client.post('/v1/product-ingestion/bundles/'+bid+'/analyze',json={'question':'保险期间？'}).status_code,404)
  publish_source(self.app,'alice',bid)
  with self.search():r=self.client.post('/v1/product-ingestion/bundles/'+bid+'/analyze',json={'question':'保险期间？'})
  self.assertEqual(r.status_code,200,r.text);self.assertEqual(r.json()['status'],'source_answer')
 def test_case_window_and_qualitative_recommendation_do_not_require_old_profile(self):
  parent,_=self.indexed_bundle();bid=parent['id']
  directory=self.client.get('/v1/product-ingestion/bundles/'+bid+'/illustration-examples').json()
  case=directory['cases'][0];r=self.client.get('/v1/product-ingestion/bundles/'+bid+'/illustration-examples',params={'case_id':case['case_id'],'row_count':2})
  self.assertTrue(r.json()['case']['has_more_rows']);self.assertEqual(r.json()['case']['window']['row_count'],2)
  def recommend(messages):
   prior=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
   return tool('recommend_products',{'priority_quote':'我希望长期保障','candidates':[{'bundle_id':bid,'reason':'依据原文，作为长期保障候选；资格未完整核验','result_refs':[prior['data_ref']]}]},'recommend')
  engine=self.engine([tool('update_profile',{'facts':[{'field':'goal','value':'长期保障','quote':'我希望长期保障'}]}),
      tool('product_documents',{'action':'analyze','bundle_id':bid,'question':'说明保险期间'},'analysis'),recommend,answer])
  with self.search(),patch('insurance_rag.tools.adapters.product_table_rules.profile',create=True,side_effect=AssertionError('old profile')):
   result=self.drive(engine,'我希望长期保障')
  self.assertEqual(result['state'],'completed',result);self.assertEqual(result['result']['recommendation']['candidates'][0]['rank'],1)

 def test_original_table_windows_survive_final_answer_without_becoming_quotes(self):
  from insurance_rag.ingestion.source_tables import prepare_tables
  from insurance_rag.ingestion.source_package import reference
  from insurance_rag.ingestion.source_examples import read_example
  tables=prepare_tables(self.root,self.ref);package=load_package(self.root,self.ref)
  imported={'scope':'literal_source_tables_import','state':'imported','integrity':'matched',
      'source_package_ref':self.ref,'source_package_id':package['package_id'],
      'table_batch_id':'offline-literal','prepared_ref':tables['result_ref'],
      **{k:tables[k] for k in ('table_count','cell_count','record_count')}}
  path=self.root/'import.json';write_json(path,imported)
  parent=prepare_source_bundle(self.app,'alice',self.ref,table_ref=reference(self.root,path));bid=parent['id']
  examples=prepare_examples(self.root,self.ref);case=examples['cases'][0]
  def read(db,batch,document,table,**kw):
   value=read_example(self.root,self.ref,examples['result_ref'],case['case_id'],table_id=table,**kw)
   return {'status':'matched','table':value['tables'][0],**{k:value[k] for k in ('cells','contexts','window','has_more_rows','has_more_columns')}}
  args={'bundle_id':bid,'action':'read','document_id':'doc-current-v','table_id':'t','row_count':2}
  def final(messages):
   refs=[json.loads(m['content'])['data_ref'] for m in messages if m['role']=='tool']
   return {'role':'assistant','content':json.dumps({'action':'answer','claims':[],'result_refs':refs})}
  engine=self.engine([tool('query_product_source_values',args,'first'),tool('query_product_source_values',{**args,'row_start':2},'second'),final])
  with patch('insurance_rag.ingestion.source_table_store.read_table',side_effect=read):result=self.drive(engine)
  self.assertEqual(result['state'],'completed',result);windows=result['result']['source_table_windows']
  self.assertEqual(len(windows),2);self.assertEqual([w['window']['row_start'] for w in windows],[0,2])
  self.assertTrue(windows[0]['has_more_rows']);self.assertEqual(result['result']['claims'],[])

 def test_new_source_general_comparison_uses_analyses_without_legacy_profiles(self):
  first,_=self.indexed_bundle();second_ref=self.make_package('other-v','另一新产品');second,_=self.indexed_bundle(second_ref)
  original_chunks=self.chunks
  def results(messages):
   refs=[json.loads(m['content'])['data_ref'] for m in messages if m['role']=='tool']
   return tool('compare_analysis_results',{'result_refs':refs,'objective_quote':'比较保险期间'},'compare')
  engine=self.engine([tool('product_documents',{'action':'analyze','bundle_id':first['id'],'question':'比较保险期间'},'a'),
      tool('product_documents',{'action':'analyze','bundle_id':second['id'],'question':'比较保险期间'},'b'),results,answer])
  from insurance_rag.ingestion.source_index import source_chunks
  one=source_chunks(self.root,load_package(self.root,self.ref));two=source_chunks(self.root,load_package(self.root,second_ref))
  with patch('insurance_rag.indexing.opensearch.SearchService') as service,patch('insurance_rag.retrieval.reranking.rerank',side_effect=lambda query,hits,provider,**kw:hits):
   service.return_value.search.side_effect=[{'hits':[{'record':chunks[0],'score':1}],'routes':['bm25'],'candidate_counts':{'bm25':1},'degraded':False} for chunks in (one,two)]
   result=self.drive(engine,'比较保险期间')
  self.assertEqual(result['state'],'completed',result);self.assertEqual(result['result']['product_comparison']['scope'],'general_facts')
  self.assertEqual(len(result['result']['product_comparison']['products']),2)

 def test_task_goal_update_keeps_acquired_product_reference_usable(self):
  parent,_=self.indexed_bundle();goal='我希望优先了解合同保障范围'
  def recommend(messages):
   ref=json.loads(next(m['content'] for m in messages if m['role']=='tool'))['data_ref']
   return tool('recommend_products',{'priority_quote':goal,'candidates':[{'bundle_id':parent['id'],'reason':'依据已取得的合同说明，资格仍待核验','result_refs':[ref],'disposition':'insufficient_information'}]},'recommend')
  engine=self.engine([tool('product_documents',{'action':'analyze','bundle_id':parent['id'],'question':'保险期间'},'a'),tool('update_profile',{'facts':[{'field':'goal','value':goal,'quote':goal,'scope':'task'}]},'goal'),recommend,answer])
  with self.search():result=self.drive(engine,goal+'，请取得资料后给出候选建议；仅本次临时意向，不保存长期画像，不写文件。')
  self.assertEqual(result['state'],'completed',result);self.assertEqual(result['result']['recommendation']['candidates'][0]['bundle_id'],parent['id'])
  self.assertTrue(all(f['scope']=='task' for f in engine.profile.rows('alice',result['thread_id'])))

 def test_maintained_current_bundle_has_priority_over_public_snapshot_capabilities(self):
  from insurance_rag.tools.adapters.capabilities import capability_view
  parent,_=self.indexed_bundle();publish_source(self.app,'alice',parent['id'])
  tools=SimpleNamespace(versions={'current-v':{'product_name':'old snapshot'}})
  r=capability_view({'product_version_id':'current-v'},{'app':self.app,'owner':'alice','state':{},'tools':tools})
  self.assertEqual(r['data']['capability_view']['source_scope'],'current_source_business')
  self.assertEqual(r['data']['capability_view']['product']['product_name'],'新来源产品')

 def test_failed_and_partial_source_jobs_do_not_claim_qa_or_trigger_wait(self):
  from insurance_rag.tools.adapters.product_documents import product_documents
  from insurance_rag.ingestion.store import public_job
  from insurance_rag.ingestion.source_package import PREPARATION_POLICY
  intake=IntakeStore(self.store)
  for state in ('failed','partial'):
   row=intake.create('preparation_job','job-'+state,'alice','current-v',{'stage':'failed' if state=='failed' else 'completed',
      'preparation_policy':PREPARATION_POLICY,'completed_steps':{},'attempts':1,'error_code':'parse_failed' if state=='failed' else None,
      'result':None if state=='failed' else {'scope':'parser_and_generic_mapper','identity':{},'mapping':{'structure_status':'partial'}}},state)
   ctx={'app':self.app,'owner':'alice','run':{},'state':{}}
   response=product_documents({'action':'status','job_id':row['id']},ctx)
   self.assertEqual(response['status'],'unavailable' if state=='failed' else 'partial')
   summary=response['data']['product_preparation'];self.assertFalse(summary['qa_ready'])
   self.assertFalse(summary['personal_calculation_ready']);self.assertNotIn('pending_document_job',ctx['state'])
   self.assertEqual(public_job(row)['qa_ready'],summary['qa_ready'])

 def test_automatic_workflow_registers_private_bundle_with_independent_failures(self):
  from insurance_rag.ingestion.source_workflow import complete_source
  from insurance_rag.ingestion.readiness import current_capabilities
  seen={}
  def step(name,fn):
   if name not in seen:seen[name]=fn()
   return seen[name]
  with patch('insurance_rag.ingestion.source_workflow.build_source_index',side_effect=RuntimeError('offline')):
   prepared=complete_source(self.app,'alice',self.ref,step)
  self.assertIn('search_index',prepared['errors']);self.assertEqual(prepared['execution_state'],'partial')
  parent=IntakeStore(self.store).get('product_bundle',prepared['bundle_id'],'alice')
  self.assertEqual(parent['content']['scope'],'current_source_business');self.assertIn('execution',parent['content']['result_refs'])
  self.assertEqual(current_capabilities(self.app,parent['content'])['document_qa']['status'],'not_prepared')
  self.assertEqual(IntakeStore(self.store).list('library_product'),[])
  self.assertIsNone(self.store.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='insurance_intake_illustration_profile'").fetchone())
  count=len(seen)
  with patch('insurance_rag.ingestion.source_workflow.build_source_index',side_effect=RuntimeError('offline')):
   again=complete_source(self.app,'alice',self.ref,step)
  self.assertEqual(again['bundle_id'],prepared['bundle_id']);self.assertEqual(len(seen),count)

 def test_new_formula_result_through_agent_and_form_without_old_profile(self):
  from insurance_rag.ingestion.source_execution import POLICY,PROMPT
  from insurance_rag.ingestion.source_package import reference
  from insurance_rag.ingestion.source_rule_reading import rule_packet
  parent,_=self.indexed_bundle();content=parent['content'];target={'id':'plan','name':'测试原文公式','method':'formula','unit':'CNY','state':'ready','required_inputs':['annual_premium_input'],'dependencies':[],'issues':[],
    'bindings':[{'name':'premium','kind':'input','key':'annual_premium_input'}],'formula':{'variables':[{'name':'premium','unit':'CNY'}],'expression_tree':{'type':'parameter','name':'premium'}}}
  execution={'scope':'current_source_execution','source_package_ref':self.ref,'source_package_id':content['source_package_id'],'rules_ref':content['result_refs']['rules'],'bindings_ref':None,'versions':{'policy':POLICY,'prompt':stable_hash(PROMPT)},'targets':[target],'state':'ready'}
  path=self.root/'execution.json';write_json(path,execution)
  current=prepare_source_bundle(self.app,'alice',self.ref,index_ref=content['result_refs']['index'],rules_ref=content['result_refs']['rules'],execution_ref=reference(self.root,path));bid=current['id']
  inputs={'entry_age':35,'payment_years':3,'annual_premium_input':'10000','end_year':3,'scenario':'normal_annual_payment_no_changes_no_loans'}
  def build(messages):
   ref=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))['data_ref']
   return tool('build_benefit_table',{'bundle_id':bid,'value_refs':[ref]},'table')
  engine=self.engine([tool('calculate_product_values',{'bundle_id':bid,'rule_id':'plan','inputs':inputs}),build,answer])
  with patch('insurance_rag.tools.adapters.product_table_steps.profile',create=True,side_effect=AssertionError('old profile')):
   result=self.drive(engine,'假设投保年龄35岁，把年交保费改为10000元，交3年，展示3年，正常年交、无变更、无贷款，仅本次试算。')
  self.assertEqual(result['state'],'completed',result);self.assertEqual(result['result']['benefit_table']['rows'][0]['values']['plan'],'10000')
  self.assertEqual(result['result']['benefit_table']['_audit']['scope'],'current_source_execution')
  saved=engine.workflow_store.read_result(result['result']['workflow_result_id'],'alice')['content']
  engine.validate_calculation_result(saved,engine.store.get(result['run_id'],'alice')['release'])
  import copy
  earlier=copy.deepcopy(saved);earlier['benefit_table'].pop('_audit');earlier['benefit_tables']=[]
  engine.validate_calculation_result(earlier,engine.store.get(result['run_id'],'alice')['release'])
  earlier['benefit_table']['rows'][0]['values']['plan']='999'
  from insurance_rag.agent.store import Conflict
  with self.assertRaisesRegex(Conflict,'historical_source_result_unavailable'):engine.validate_calculation_result(earlier,engine.store.get(result['run_id'],'alice')['release'])
  response=self.client.post('/v1/product-ingestion/bundles/'+bid+'/illustrate',json={'inputs':inputs,'metrics':['plan']})
  self.assertEqual(response.status_code,200,response.text);self.assertEqual(response.json()['status'],'ok')
  self.assertEqual(len(IntakeStore(self.store).list('source_execution_result')),1)


 def test_empty_name_filter_does_not_claim_empty_library(self):
  self.indexed_bundle()
  def retry(messages):
   packet=json.loads(next(m['content'] for m in reversed(messages) if m['role']=='tool'))
   self.assertEqual(packet['status'],'no_evidence')
   self.assertEqual(packet['reason_code'],'product_name_filter_no_match')
   self.assertEqual(packet['data']['available_product_count'],1)
   return tool('resolve_products',{},'all')
  engine=self.engine([tool('resolve_products',{'hint':'已发布产品'}),retry,answer])
  out=self.drive(engine,'列出全部产品')
  self.assertEqual(out['state'],'completed',out)
  self.assertEqual(len(out['result']['products']),1)

if __name__=='__main__':unittest.main()
