"""Single-person profile, temporal facts and incremental conversation extraction.

Uses the existing store/model; no timer, vector database or new worker runtime.
"""
import json
import re
from decimal import Decimal, InvalidOperation
from insurance_rag.parsing.common import stable_hash
from insurance_rag.tools.contracts import result

FIELDS = ('age', 'sex', 'annual_budget', 'goal', 'liquidity_year', 'payment_years',
          'coverage_years', 'liquidity_preference', 'risk_preference', 'response_preference')
DYNAMIC = {'age', 'annual_budget', 'goal', 'liquidity_year', 'liquidity_preference', 'risk_preference'}
DDL = """
CREATE TABLE IF NOT EXISTS insurance_agent_profile_fact(
 fact_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, thread_id TEXT NOT NULL,
 field TEXT NOT NULL, value TEXT NOT NULL, scope TEXT NOT NULL,
 source_message_id TEXT NOT NULL, quote TEXT NOT NULL, asserted_at DOUBLE PRECISION NOT NULL,
 effective_at DOUBLE PRECISION NOT NULL, extracted_at DOUBLE PRECISION NOT NULL,
 status TEXT NOT NULL, supersedes TEXT);
CREATE TABLE IF NOT EXISTS insurance_agent_profile_turn(
 source_message_id TEXT PRIMARY KEY, owner_id TEXT NOT NULL, thread_id TEXT NOT NULL,
 user_text TEXT NOT NULL, assistant_text TEXT NOT NULL, created_at DOUBLE PRECISION NOT NULL,
 processed INTEGER NOT NULL);
CREATE TABLE IF NOT EXISTS insurance_agent_profile_forget(
 owner_id TEXT NOT NULL, field TEXT NOT NULL, before_time DOUBLE PRECISION NOT NULL,
 PRIMARY KEY(owner_id,field));
"""
EXTRACT_PROMPT = """从给定用户消息提取本人长期画像，不采信assistant_text中的推测、产品事实、假设、
示例或为他人咨询。仅返回JSON {"facts":[{"field":"允许字段","value":"值",
"source_message_id":"给定消息ID","quote":"连续用户原话","scope":"long_term"}]}。
没有明确本人事实返回空facts。用户说本次预算/假设方案的条件不是长期画像。
时间沿用来源，不把重新提取时间当事实更新。不要输出其他键。"""


def normalize(field, value, quote):
    if field not in FIELDS or not isinstance(quote, str) or not quote.strip():
        raise ValueError('invalid_profile_fact')
    from insurance_rag.ingestion.personal_inputs import chinese_number
    if field in ('age', 'liquidity_year', 'payment_years', 'coverage_years'):
        if isinstance(value, bool):
            raise ValueError('invalid_profile_value')
        parsed = chinese_number(re.sub(r'[岁年]$', '', str(value).strip().removeprefix('第').removesuffix('后')))
        if parsed != int(parsed):
            raise ValueError('invalid_profile_value')
        number = int(parsed)
        candidates = [chinese_number(x) for x in re.findall(r'[0-9零〇一二两三四五六七八九十百千万]+(?:\.[0-9]+)?', quote)]
        if not 0 <= number <= 150 or Decimal(number) not in candidates:
            raise ValueError('profile_value_requires_quote')
        return number
    if field == 'annual_budget':
        try:
            number = chinese_number(re.sub(r'元$', '', str(value).strip().removeprefix('人民币')))
        except (InvalidOperation, ValueError):
            raise ValueError('invalid_profile_budget') from None
        if not number.is_finite() or number < 0:
            raise ValueError('invalid_profile_budget')
        candidates = [chinese_number(x.replace(' ', '')) for x in
                      re.findall(r'[0-9零〇一二两三四五六七八九十百千万]+(?:\.[0-9]+)?(?:\s*万)?', quote)]
        if number not in candidates:
            raise ValueError('profile_value_requires_quote')
        return format(number, 'f')
    if field == 'sex':
        value={'男':'male','男性':'male','男士':'male','女':'female','女性':'female','女士':'female','不限性别':'unisex'}.get(value,value)
        if value not in ('male', 'female', 'unisex') or not any(x in quote for x in
                {'male': ('男',), 'female': ('女',), 'unisex': ('不限性别',)}[value]):
            raise ValueError('profile_value_requires_quote')
        return value
    if not isinstance(value,str) or not value.strip() or len(value)>1000:
        raise ValueError('profile_value_requires_quote')
    return value if value in quote else quote.strip()


class ProfileStore:
    def __init__(self, store):
        self.store = store
        with store.transaction():
            for statement in DDL.split(';'):
                if statement.strip():
                    store.execute(statement)

    def rows(self, owner, thread):
        keys = ('fact_id', 'field', 'value', 'scope', 'source_message_id', 'quote',
                'asserted_at', 'effective_at', 'extracted_at', 'status', 'supersedes')
        rows = self.store.execute(
            "SELECT " + ','.join(keys) + " FROM insurance_agent_profile_fact "
            "WHERE owner_id=? AND (scope='long_term' OR thread_id=?) AND status='active' ORDER BY asserted_at,fact_id",
            (owner, thread)).fetchall()
        return [{**dict(zip(keys, row)), 'value': json.loads(row[2])} for row in rows]

    def snapshot(self, owner, thread):
        grouped = {}
        for fact in self.rows(owner, thread):
            days = max(0, self.store.clock() - fact['effective_at']) / 86400
            fact['retrieval_weight'] = round(0.5 ** (days / (30 if fact['field'] in DYNAMIC else 3650)), 4)
            if fact['field']=='age' and days>365:fact['requires_confirmation']=True
            grouped.setdefault(fact['field'], []).append(fact)
        values, conflicts = {}, []
        for field, facts in grouped.items():
            # Current task conditions override long-term preferences in this task only.
            task = [f for f in facts if f['scope'] == 'task']
            applicable = task or facts
            if all(f.get('requires_confirmation') for f in applicable):
                conflicts.append({'field':field,'facts':applicable,'reason':'age_requires_current_confirmation','next_step':'ask_if_relevant'})
            elif len({json.dumps(f['value'], ensure_ascii=False) for f in applicable}) == 1:
                values[field] = applicable[-1]['value']
            else:
                conflicts.append({'field': field, 'facts': applicable, 'next_step': 'ask_if_relevant'})
        return {'values': values, 'conflicts': conflicts, 'facts': [f for facts in grouped.values() for f in facts]}

    def put(self, run, owner, field, value, quote, source, asserted_at, *,
            scope='task', explicit_correction=False, effective_at=None):
        if scope not in ('task', 'long_term'):
            raise ValueError('invalid_profile_scope')
        value = normalize(field, value, quote)
        if re.search(r'假设|例如|举例|案例|假如|我(?:的)?(?:妈妈|爸爸|父亲|母亲|家人|朋友|丈夫|妻子|孩子)', quote):
            raise ValueError('profile_subject_or_hypothesis_not_self')
        fid = stable_hash([owner, source, field, value, scope])
        with self.store.transaction():
            self.store.guard(run)
            tomb = self.store.execute('SELECT before_time FROM insurance_agent_profile_forget WHERE owner_id=? AND field=?',
                                      (owner, field)).fetchone()
            if tomb and asserted_at <= tomb[0]:
                return None
            if self.store.execute('SELECT 1 FROM insurance_agent_profile_fact WHERE fact_id=?', (fid,)).fetchone():
                return fid
            prior = [f for f in self.rows(owner, run['thread_id']) if f['field'] == field and f['scope'] == scope]
            newer=[f for f in prior if f['asserted_at']>asserted_at and (scope=='task' or field in DYNAMIC and f['asserted_at']-asserted_at>=30*86400)]
            if newer:return max(newer,key=lambda f:f['asserted_at'])['fact_id']
            same = [f for f in prior if f['value'] == value]
            # Repeated statements do not make an old fact newer.
            if same and asserted_at-same[-1]['asserted_at']<30*86400:
                return same[-1]['fact_id']
            replaced = []
            for fact in same:
                self.store.execute("UPDATE insurance_agent_profile_fact SET status='superseded' WHERE fact_id=?",(fact['fact_id'],))
                replaced.append(fact['fact_id'])
            for fact in prior:
                if asserted_at < fact['asserted_at']:
                    continue
                if explicit_correction or scope == 'task' or field in DYNAMIC and asserted_at - fact['asserted_at'] >= 30 * 86400:
                    self.store.execute("UPDATE insurance_agent_profile_fact SET status='superseded' WHERE fact_id=?", (fact['fact_id'],))
                    replaced.append(fact['fact_id'])
            self.store.execute('INSERT INTO insurance_agent_profile_fact VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (fid, owner, run['thread_id'], field, json.dumps(value, ensure_ascii=False), scope,
                 source, quote, asserted_at, effective_at if effective_at is not None else asserted_at,
                 self.store.clock(), 'active', json.dumps(replaced)))
        return fid

    def forget(self, run, owner, field):
        if field not in FIELDS:
            raise ValueError('invalid_profile_field')
        with self.store.transaction():
            self.store.guard(run)
            self.store.execute("UPDATE insurance_agent_profile_fact SET status='forgotten' WHERE owner_id=? AND field=?", (owner, field))
            self.store.execute('INSERT INTO insurance_agent_profile_forget VALUES (?,?,?) ON CONFLICT(owner_id,field) DO UPDATE SET before_time=excluded.before_time',
                               (owner, field, self.store.clock()))

    def record_turn(self, run, owner, assistant_text):
        rows = self.store.execute('SELECT sequence,message,created_at FROM insurance_agent_message WHERE run_id=? ORDER BY sequence',
                                  (run['run_id'],)).fetchall()
        with self.store.transaction():
            self.store.guard(run)
            for seq, encoded, created in rows:
                message = json.loads(encoded)
                if message['role'] != 'user':
                    continue
                self.store.execute('INSERT INTO insurance_agent_profile_turn VALUES (?,?,?,?,?,?,0) ON CONFLICT DO NOTHING',
                    (run['run_id'] + ':' + str(seq), owner, run['thread_id'], message['content'], assistant_text, created))

    def pending(self, owner, thread=None):
        sql = 'SELECT source_message_id,user_text,assistant_text,created_at,thread_id FROM insurance_agent_profile_turn WHERE owner_id=? AND processed=0'
        args = [owner]
        if thread:
            sql += ' AND thread_id=?'
            args.append(thread)
        return [dict(zip(('source_message_id', 'user_text', 'assistant_text', 'asserted_at', 'thread_id'), r))
                for r in self.store.execute(sql + ' ORDER BY created_at,source_message_id', tuple(args)).fetchall()]

    def extract(self, app, run, owner, *, force=False):
        pending = self.pending(owner) if force else self.pending(owner, run['thread_id'])
        if not pending or not force and len(pending) < 10 and sum(len(p['user_text']) + len(p['assistant_text']) for p in pending) < 12000:
            return False
        # One bounded batch per boundary. Remainder stays pending for the next boundary.
        batch, size = [], 0
        for p in pending:
            if size + len(p['user_text']) > 16000 and batch:
                break
            batch.append(p)
            size += len(p['user_text'])
        from insurance_rag.reliability.runtime import request_budget
        before = dict(app.model.usage)
        try:
            with request_budget(20):
                proposal = app.model.structured(EXTRACT_PROMPT, {'allowed_fields': FIELDS,
                    'profile_messages': [{k: p[k] for k in ('source_message_id', 'user_text')} for p in batch]},
                    reserve=lambda: app.store.reserve(run, 'memory_llm_calls', 2))
            if not isinstance(proposal, dict) or not isinstance(proposal.get('facts'), list) or len(proposal['facts']) > 40:
                raise ValueError('invalid_profile_extraction')
            by_id = {p['source_message_id']: p for p in batch}
            with self.store.transaction():
                self.store.guard(run)
                for f in proposal['facts']:
                    p = by_id.get(f.get('source_message_id')) if isinstance(f, dict) else None
                    if not p or f.get('scope') != 'long_term' or not f.get('quote') or f['quote'] not in p['user_text']:
                        continue
                    if re.search(r'假设|例如|案例|假如|帮.*(?:家人|朋友)|我(?:的)?(?:妈妈|爸爸|父亲|母亲|家人|朋友|丈夫|妻子|孩子)', p['user_text']):
                        continue
                    if re.search(r'本次|这次|本轮|假设', f['quote']):
                        continue
                    deleted=app.memory.deleted(owner)
                    if any(d['content'] in p['user_text'] and p['asserted_at']<=d.get('forgotten_at',0) for d in deleted):
                        continue
                    try:
                        self.put(run, owner, f['field'], f['value'], f['quote'], p['source_message_id'],
                                 p['asserted_at'], scope='long_term',
                                 explicit_correction=bool(re.search(r'更正|改为|现在是|现在改', p['user_text'])))
                    except (ValueError, KeyError):
                        continue
                self.store.guard(run)
                for p in batch:
                    self.store.execute('UPDATE insurance_agent_profile_turn SET processed=1 WHERE source_message_id=?',
                                       (p['source_message_id'],))
            return True
        except Exception as exc:
            from insurance_rag.agent.store import Conflict, Forbidden
            if isinstance(exc, (Conflict, Forbidden)):
                raise
            app.store.event(run['run_id'], 'memory_extraction_deferred', {'reason_code': 'memory_extraction_failed_retry_next_boundary'})
            return False
        finally:
            delta = {k: app.model.usage.get(k, 0) - before.get(k, 0)
                     for k in ('prompt_tokens', 'completion_tokens', 'unknown_usage_requests')}
            app.store.add_usage(run, {'memory_' + k: v for k, v in delta.items()})

    def redactions(self, owner):
        rows=self.store.execute(
            "SELECT source_message_id,quote FROM insurance_agent_profile_fact WHERE owner_id=? AND status='forgotten'",
            (owner,)).fetchall()
        return [{'source_message_id':r[0],'quote':r[1]} for r in rows]
