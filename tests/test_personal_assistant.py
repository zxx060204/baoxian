import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from insurance_rag.agent.engine import AgentEngine
from insurance_rag.agent.store import AgentStore, Forbidden
from insurance_rag.agent.loop_config import LoopSettings
from insurance_rag.services.config import AgentSettings
from support_fixture_tools import FixtureTools
from test_native_loop import Scripted, tool, answer


def final(message='可以继续，请告诉我更关心保障还是流动性。'):
    return {'role':'assistant','content':json.dumps({'action':'answer','message':message,'claims':[],'result_refs':[]},ensure_ascii=False)}


class ProfileModel(Scripted):
    def __init__(self,steps):
        super().__init__(steps)
        self.extractions=[]
    def structured(self,prompt,payload,*,reserve=None):
        if 'profile_messages' not in payload:
            return super().structured(prompt,payload,reserve=reserve)
        if reserve:reserve()
        self.extractions.append(payload)
        p=payload['profile_messages'][0]
        return {'facts':[{'field':'age','value':35,'scope':'long_term',
                         'source_message_id':p['source_message_id'],'quote':'我今年35岁'}]
                if '我今年35岁' in p['user_text'] else []}


class PersonalAssistantTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=AgentStore.sqlite(self.root/'state.db')
        self.tools=FixtureTools();self.thread=self.store.new_thread('alice')
    def tearDown(self):
        self.store.close();self.temp.cleanup()
    def app(self,steps):
        model=steps if isinstance(steps,Scripted) else Scripted(steps)
        return AgentEngine(self.store,AgentSettings(),model,lambda:self.tools.release,
                           lambda r:self.tools,root=self.root,limits=LoopSettings())
    def drive(self,app,message,thread=None):
        r=app.submit(thread or self.thread,'alice',message)
        r=app.drive(r['run_id'],'alice')
        self.assertEqual(r['state'],'completed',str(r)+' events='+str(self.store.events(r['run_id'],'alice'))+' calls='+str(self.store.execute('SELECT name,result FROM insurance_agent_tool_call WHERE run_id=?',(r['run_id'],)).fetchall()))
        return r
    def test_natural_language_without_product_evidence(self):
        r=self.drive(self.app([final('可以帮你解释保险概念，也可以比较已有产品。')]),'你能做什么？')
        self.assertIn('保险概念',r['result']['text'])
    def test_ordinary_turns_wait_for_ten_round_budget(self):
        model=ProfileModel([final('收到。') for _ in range(10)])
        app=self.app(model)
        for i in range(9):
            self.drive(app,'我今年35岁' if i==0 else '继续讨论')
        self.assertEqual(model.extractions,[])
        self.assertEqual(len(app.profile.pending('alice')),9)
        self.drive(app,'继续讨论')
        self.assertEqual(len(model.extractions),1)
        self.assertEqual(app.profile.pending('alice'),[])
        self.assertEqual(app.profile.snapshot('alice',self.thread)['values']['age'],35)
    def test_character_budget_can_trigger_before_ten_rounds(self):
        model=ProfileModel([final('收到。') for _ in range(4)]);app=self.app(model)
        for i in range(4):self.drive(app,('我今年35岁。' if i==0 else '')+'补充说明'*850)
        self.assertEqual(len(model.extractions),1)
    def test_new_conversation_flushes_pending_short_session(self):
        model=ProfileModel([final(),final()]);app=self.app(model)
        self.drive(app,'我今年35岁')
        self.assertEqual(len(model.extractions),0)
        other=app.new_thread('alice')
        self.drive(app,'继续咨询',other)
        self.assertEqual(len(model.extractions),1)
        self.assertEqual(app.profile.snapshot('alice',other)['values']['age'],35)
    def test_explicit_session_end_flushes_short_session(self):
        model=ProfileModel([final(),final()]);app=self.app(model)
        self.drive(app,'我今年35岁')
        self.drive(app,'今天先到这里，结束会话')
        self.assertEqual(len(model.extractions),1)
    def test_task_profile_is_used_in_next_turn_without_becoming_long_term(self):
        app=self.app([tool('update_profile',{'facts':[{'field':'annual_budget','value':'2万元','quote':'本次预算2万元'}]}),answer,
                      tool('read_profile',{}),answer])
        self.drive(app,'本次预算2万元')
        r=self.drive(app,'接着看适合我的候选')
        self.assertEqual(r['result']['profile']['values']['annual_budget'],'20000')
        other=app.new_thread('alice')
        self.assertNotIn('annual_budget',app.profile.snapshot('alice',other)['values'])
    def test_hypothetical_family_information_not_saved_as_self(self):
        app=self.app([tool('update_profile',{'facts':[{'field':'age','value':60,'quote':'妈妈60岁'}]}),final()])
        self.drive(app,'我妈妈60岁，帮她看看')
        self.assertEqual(app.profile.snapshot('alice',self.thread)['values'],{})
    def test_temporal_conflict_correction_and_forget(self):
        app=self.app([]);submitted=app.submit(self.thread,'alice','测试时间规则')
        run=self.store.claim_run(submitted['run_id'],'alice',seconds=100)
        now=self.store.clock()
        for value,days in [('10000',40),('20000',0)]:
            app.profile.put(run,'alice','annual_budget',value,value+'元','s'+value,now-days*86400,scope='long_term')
        snap=app.profile.snapshot('alice',self.thread)
        self.assertEqual(snap['values']['annual_budget'],'20000')
        app.profile.put(run,'alice','annual_budget','30000','30000元','near',now+1,scope='long_term')
        self.assertEqual(app.profile.snapshot('alice',self.thread)['conflicts'][0]['field'],'annual_budget')
        app.profile.put(run,'alice','annual_budget','40000','40000元','correction',now+2,scope='long_term',explicit_correction=True)
        self.assertEqual(app.profile.snapshot('alice',self.thread)['values']['annual_budget'],'40000')
        app.profile.put(run,'alice','annual_budget','5000','5000元','older-backlog',now-80*86400,scope='long_term')
        self.assertEqual(app.profile.snapshot('alice',self.thread)['values']['annual_budget'],'40000')
        app.profile.forget(run,'alice','annual_budget')
        self.assertIsNone(app.profile.put(run,'alice','annual_budget','10000','10000元','old-reextract',now-40*86400,scope='long_term'))
        self.assertEqual(app.profile.snapshot('alice',self.thread)['values'],{})
        self.assertEqual(app.profile.snapshot('bob',self.thread)['values'],{})
    def test_stable_identity_conflict_is_not_overwritten_by_age_of_memory(self):
        app=self.app([]);r=self.store.claim_run(app.submit(self.thread,'alice','测试')['run_id'],'alice')
        now=self.store.clock()
        app.profile.put(r,'alice','sex','male','我是男士','a',now-60*86400,scope='long_term')
        app.profile.put(r,'alice','sex','female','我是女士','b',now,scope='long_term')
        self.assertTrue(app.profile.snapshot('alice',self.thread)['conflicts'])
    def test_failed_extraction_keeps_pending_watermark(self):
        model=ProfileModel([final()]);app=self.app(model)
        with patch.object(model,'structured',side_effect=RuntimeError('offline')):
            self.drive(app,'我今年35岁，结束会话')
        self.assertEqual(len(app.profile.pending('alice')),1)
    def test_local_write_and_cross_thread_read_do_not_persist_body(self):
        secret='本地文件正文专用标记，不应出现在服务端日志。'*30
        app=self.app([tool('write_file',{'path':'notes/result.md','content':secret}),answer,
                      tool('read_file',{'path':'notes/result.md'}),answer])
        r=self.drive(app,'请保存本地分析草稿')
        self.assertEqual(r['result']['files'][0]['path_namespace'],'user_local')
        other=app.new_thread('alice');r=self.drive(app,'读取本地 notes/result.md',other)
        self.assertNotIn('content',r['result']['files'][0])
        self.assertTrue(any(secret in json.dumps(x,ensure_ascii=False) for x in app.model.seen))
        for table,column in [('insurance_agent_message','message'),('insurance_agent_tool_call','arguments'),
                             ('insurance_agent_tool_call','result'),('insurance_agent_evidence_package','package'),
                             ('insurance_agent_run','result'),('insurance_agent_loop_state','state')]:
            for row in self.store.execute('SELECT '+column+' FROM '+table).fetchall():
                self.assertNotIn(secret,row[0] or '',table)
        local=Path(r['result']['files'][0]['local_path'])
        self.assertEqual(local.read_text(encoding='utf8'),secret)
    def test_analyses_are_listed_and_authorized(self):
        app=self.app([final('这是一份可接续的分析。')])
        r=self.drive(app,'咨询')
        rows=app.workflow_store.analyses('alice')
        self.assertEqual(rows[0]['analysis_id'],r['result']['analysis_id'])
        self.assertEqual(app.workflow_store.analyses('bob'),[])
        with self.assertRaises(Forbidden):app.workflow_store.read_result(rows[0]['analysis_id'],'bob')

    def test_api_history_and_reopen_are_usable(self):
        from contextlib import contextmanager
        from fastapi.testclient import TestClient
        from insurance_rag.api.app import create_app
        app=self.app([final()])
        r=self.drive(app,'初次咨询')
        @contextmanager
        def factory():yield app
        client=TestClient(create_app(factory,authenticate=lambda token:'alice' if token=='alice' else 'bob'))
        rows=client.get('/v1/analyses',headers={'Authorization':'alice'})
        self.assertEqual(rows.status_code,200)
        aid=rows.json()['analyses'][0]['analysis_id']
        self.assertEqual(client.get('/v1/analyses/'+aid,headers={'Authorization':'alice'}).status_code,200)
        self.assertEqual(client.get('/v1/analyses/'+aid,headers={'Authorization':'bob'}).status_code,404)
        self.assertEqual(client.post('/v1/threads/'+self.thread+'/open',headers={'Authorization':'alice'}).status_code,200)
        self.assertTrue(self.store.thread(self.thread,'alice')['memory_flush_requested'])

    def test_backend_material_dates_are_visible_without_changing_rules(self):
        from insurance_rag.services.material_metadata import maintain
        app=self.app([])
        dates=maintain(app,'alice','fixture-version',{'acquired_at':'2026-10-01T08:00:00+08:00',
              'version_date':'2026-09-01','last_verified_at':'2026-10-20T08:00:00+08:00','verification_note':'后台核对原件版本'})
        self.assertEqual(dates['version_date'],'2026-09-01')
        self.assertEqual(dates['verification_note'],'后台核对原件版本')
    def test_old_age_is_reconfirmed_instead_of_blindly_reused(self):
        app=self.app([]);r=self.store.claim_run(app.submit(self.thread,'alice','测试')['run_id'],'alice')
        app.profile.put(r,'alice','age',35,'我今年35岁','old-age',self.store.clock()-400*86400,scope='long_term')
        snapshot=app.profile.snapshot('alice',self.thread)
        self.assertNotIn('age',snapshot['values'])
        self.assertEqual(snapshot['conflicts'][0]['reason'],'age_requires_current_confirmation')
    def test_explicit_file_save_cannot_finish_with_only_a_promise(self):
        app=self.app([final('已经保存。'),tool('write_file',{'path':'notes/result.md','content':'已实际保存。'}),answer])
        r=self.drive(app,'请保存分析草稿到 notes/result.md')
        self.assertTrue(r['result']['files'])
        self.assertTrue(any(e['event']=='validation_failed' for e in self.store.events(r['run_id'],'alice')))

    def test_forgotten_profile_is_not_restored_from_recent_history(self):
        app=self.app([tool('update_profile',{'facts':[{'field':'age','value':35,'quote':'我今年35岁','scope':'long_term'}]}),answer,
                      tool('forget_profile',{'field':'age'}),answer,tool('read_profile',{}),answer])
        self.drive(app,'请记住我的年龄：我今年35岁')
        self.drive(app,'请忘记我的年龄')
        r=self.drive(app,'现在画像里还有什么？')
        self.assertNotIn('age',r['result']['profile']['values'])
        self.assertEqual(app.memory.history('alice','35岁'),[])
        self.assertNotIn('35岁',app.model.seen[-1][1]['content'])

    def test_source_wait_ignores_retired_qa_result_and_finishes_only_at_task_end(self):
        from insurance_rag.onboarding.store import IntakeStore
        app=self.app([]);thread=app.new_thread('alice')
        submitted=app.submit(thread,'alice','查询上传资料')
        run=self.store.claim_run(submitted['run_id'],'alice')
        app.journal.begin(run,app._initialize(run,'alice'))
        intake=IntakeStore(self.store)
        job=intake.create('preparation_job','source-wait','alice','v',
            {'stage':'parsing','qa_result':{'bundle_id':'qa-bundle:retired'}},'running')
        self.store.settle(run,'waiting',clarification={'kind':'document_processing','job_id':job['id'],'interrupt_id':'i'})
        app.model.steps=[tool('product_documents',{'action':'status','job_id':job['id']}),answer]
        self.assertEqual(app.drive(run['run_id'],'alice')['state'],'waiting')
        intake.update('preparation_job',job['id'],job['revision'],{'stage':'completed','result':{
            'scope':'parser_and_generic_mapper','mapping':{'structure_status':'partial'},'identity':{}}},'partial','test','done')
        output=app.drive(run['run_id'],'alice')
        self.assertEqual(output['state'],'completed',output)
        summary=output['result']['product_preparation']
        self.assertFalse(summary['source_ready']);self.assertFalse(summary['qa_ready'])
        self.assertFalse(summary['personal_calculation_ready']);self.assertIsNone(summary['bundle_id'])

    def test_library_publication_does_not_share_unpublished_private_uploads(self):
        from insurance_rag.onboarding.store import IntakeStore
        from insurance_rag.ingestion.library import publish,candidates
        from insurance_rag.ingestion.store import bundle
        app=self.app([])
        store=IntakeStore(self.store)
        content={'documents':[],'identity':{'product_name':'后台产品','product_version_id':'library-v','company_id':'company'}}
        store.create('product_bundle','product-bundle:lib','admin','library-v',content,'ready')
        with self.assertRaises(Forbidden):bundle(app,'alice','product-bundle:lib')
        publish(app,'admin','product-bundle:lib')
        self.assertEqual(bundle(app,'alice','product-bundle:lib')['owner_id'],'admin')
        self.assertEqual(candidates(app)[0]['product_name'],'后台产品')
        store.create('product_bundle','product-bundle:private','other-user','private-v',
                     {'documents':[],'identity':{'product_name':'私有产品','product_version_id':'private-v'}},'ready')
        with self.assertRaises(Forbidden):bundle(app,'alice','product-bundle:private')
        publish(app,'admin','product-bundle:lib',False)
        with self.assertRaises(Forbidden):bundle(app,'alice','product-bundle:lib')

    def test_result_comparison_does_not_rank_different_personal_inputs(self):
        from insurance_rag.tools.adapters.result_comparison import compare_results
        from types import SimpleNamespace
        def table(name,premium):
            return {'status':'ok','data':{'benefit_table':{'bundle_id':name,'product':name,
                'inputs':{'entry_age':35,'annual_premium_input':premium},'columns':[{'id':'cash_value','name':'现金价值','unit':'CNY'}],
                'rows':[{'year':1,'values':{'cash_value':'100'}}],'issues':[]}}}
        packets={'a':table('a','10000'),'b':table('b','20000')}
        fake=SimpleNamespace(journal=SimpleNamespace(messages=lambda r:[{'role':'user','content':'比较流动性'}]),
                             packet=lambda ref,c:packets[ref])
        c={'app':fake,'run':{}}
        r=compare_results({'result_refs':['a','b'],'objective_quote':'比较流动性'},c)
        self.assertFalse(r['data']['product_comparison']['numeric_comparable'])
        self.assertFalse(any(x['comparable'] for x in r['data']['product_comparison']['cells']))
        packets['b']=table('b','10000')
        r=compare_results({'result_refs':['a','b'],'objective_quote':'比较流动性'},c)
        self.assertTrue(r['data']['product_comparison']['numeric_comparable'])
        self.assertFalse(r['data']['product_comparison']['ranking_performed'])

    def test_profile_keeps_valid_fields_when_one_amount_is_invalid(self):
        app=self.app([tool('update_profile',{'facts':[
            {'field':'age','value':35,'quote':'我今年35岁'},
            {'field':'annual_budget','value':'999','quote':'预算2万元'},
            {'field':'goal','value':'定性比较并给出候选取舍','quote':'请结合两款产品做定性比较，并给出候选取舍'}]}),answer])
        r=self.drive(app,'我今年35岁，预算2万元。请结合两款产品做定性比较，并给出候选取舍')
        values=r['result']['profile']['values']
        self.assertEqual(values['age'],35)
        self.assertNotIn('annual_budget',values)
        self.assertEqual(values['goal'],'请结合两款产品做定性比较，并给出候选取舍')


if __name__=='__main__':unittest.main()
