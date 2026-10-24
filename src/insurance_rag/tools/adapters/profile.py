"""Focused profile tools; business analysis never implicitly mutates memory."""
import json
import re
from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field
from insurance_rag.agent.profile import FIELDS
from insurance_rag.tools.registry import ToolSpec
from insurance_rag.tools.contracts import result


class Empty(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)


class Fact(Empty):
    field: Literal['age','sex','annual_budget','goal','liquidity_year','payment_years',
                   'coverage_years','liquidity_preference','risk_preference','response_preference']
    value: Any = Field(description='年龄/年数用整数，预算优先用元的数字字符串；支持2万元等明确金额。文本偏好使用quote中的原话片段。')
    quote: str = Field(min_length=1, max_length=1000)
    scope: Literal['task','long_term'] = 'task'


class Update(Empty):
    facts: list[Fact] = Field(min_length=1, max_length=12)


class Forget(Empty):
    field: str = Field(min_length=1, max_length=100)


def refresh(c):
    profile = c['app'].profile.snapshot(c['owner'], c['run']['thread_id'])
    c['state']['profile'] = profile
    return profile


def read_profile(args, c):
    return result('ok', data={'profile': refresh(c)})


def update_profile(args, c):
    app, run = c['app'], c['run']
    rows = app.store.execute('SELECT sequence,message,created_at FROM insurance_agent_message WHERE run_id=? ORDER BY sequence',
                             (run['run_id'],)).fetchall()
    users = [(seq, json.loads(encoded), created) for seq, encoded, created in rows if json.loads(encoded)['role'] == 'user']
    prepared = [];rejected=[]
    for f in args['facts']:
        source = next(((seq, m, created) for seq, m, created in reversed(users) if f['quote'] in m['content']), None)
        if not source:
            return result('conflict', reason_code='profile_requires_user_quote')
        seq, message, created = source
        if re.search(r'假设|例如|案例|假如|帮.*(?:家人|朋友)|我(?:的)?(?:妈妈|爸爸|父亲|母亲|家人|朋友|丈夫|妻子|孩子)', message['content']):
            return result('conflict', reason_code='profile_subject_or_hypothesis_not_self')
        if f['scope'] == 'long_term' and not re.search(r'记住|长期|一直|我(?:今年|是|的预算|偏好|希望)', message['content']):
            return result('conflict', reason_code='use_task_scope_unless_long_term_fact')
        from insurance_rag.agent.profile import normalize
        try:normalize(f['field'], f['value'], f['quote'])
        except ValueError:
            rejected.append({'field':f['field'],'reason':'value_not_supported_by_quote'});continue
        prepared.append((f, seq, message, created))
    with app.store.transaction():
        app.store.guard(run)
        for f, seq, message, created in prepared:
            app.profile.put(run, c['owner'], f['field'], f['value'], f['quote'],
                run['run_id'] + ':' + str(seq), created, scope=f['scope'],
                explicit_correction=bool(re.search(r'更正|改为|现在是|现在改', message['content'])))
    profile = refresh(c)
    # Only unambiguous current-task values enter numerical conditions.
    for key in ('age', 'sex'):
        if key in profile['values']:
            c['state']['conditions'][key] = profile['values'][key]
        elif any(x['field'] == key for x in profile['conflicts']):
            c['state']['conditions'].pop(key, None)
    if any(f['field'] in ('age','sex','annual_budget','payment_years','coverage_years') for f,_,_,_ in prepared):
        c['state']['conditions_revision'] += 1
        c['state'].pop('comparison_context', None)
    return result('partial' if rejected else 'ok', data={'profile': profile,'rejected_fields':rejected})


def forget_profile(args, c):
    texts = [m['content'] for m in c['app'].journal.messages(c['run']) if m['role'] == 'user']
    if not any('忘记' in t or '删除' in t for t in texts):
        return result('needs_clarification', reason_code='explicit_forget_request_required')
    if args['field'] not in FIELDS:
        return result('conflict', reason_code='unknown_profile_field')
    prior = c['app'].profile.rows(c['owner'], c['run']['thread_id'])
    c['app'].profile.forget(c['run'], c['owner'], args['field'])
    for f in prior:
        if f['field'] == args['field']:
            c['state'].setdefault('redacted_memory_contents', []).append(f['quote'])
    c['state']['conditions'].pop(args['field'], None)
    c['state']['conditions_revision'] += 1
    c['state'].pop('comparison_context', None)
    return result('ok', data={'profile': refresh(c), 'forgotten_field': args['field']})


def specs():
    return [
        ToolSpec('read_profile', '读取当前单人工作画像、长期事实、时间与冲突；冲突影响决策时追问，不默认挑选。', Empty, read_profile),
        ToolSpec('update_profile', '用用户连续原话更新当前单人画像；默认task仅本次方案，明确长期事实才long_term。预算不是保费，假设和他人信息不可写入。', Update, update_profile, read_only=False, side_effect='controlled_write'),
        ToolSpec('forget_profile', '用户明确要求时忘记指定画像字段并防止历史重新提取。', Forget, forget_profile, read_only=False, side_effect='controlled_write')]


def user_context_messages(c, *, include_scenarios=False):
    """Prior user statements and attributed facts, never assistant-generated guesses."""
    profile = c['app'].profile.snapshot(c['owner'], c['run']['thread_id']) if hasattr(c['app'], 'profile') else {'facts': [], 'conflicts': []}
    conflicted = {x['field'] for x in profile['conflicts']}
    texts = [f['quote'] for f in profile['facts'] if f['field'] not in conflicted]
    redacted={x['source_message_id'] for x in c['app'].profile.redactions(c['owner'])} if hasattr(c['app'],'profile') else set()
    redacted_runs={x.rsplit(':',1)[0] for x in redacted}
    texts += [x.get('user','') for x in c['state'].get('conversation_history', []) if x.get('run_id') not in redacted_runs]
    texts += [m['content'] for i,m in enumerate(c['app'].journal.messages(c['run'])) if m['role']=='user' and c['run']['run_id']+':'+str(i+1) not in redacted]
    if include_scenarios:return texts
    return [t for t in texts if not re.search(r'假设|例如|案例|假如|我(?:的)?(?:妈妈|爸爸|父亲|母亲|家人|朋友|丈夫|妻子|孩子)',t)]
