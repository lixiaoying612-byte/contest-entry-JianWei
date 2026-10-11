"""One plan's atomic call and CNY reservations over the existing runtime database."""
import copy
import json
import sqlite3
from time import monotonic
from contextlib import closing
from datetime import datetime, timezone
from decimal import Decimal, ROUND_CEILING
from pathlib import Path

from event_workbench import trial_transport
from event_workbench.deepseek_credentials import read_credential
from event_workbench.document_pipeline import digest

MAX_REQUEST_BYTES = 96000  # Two related articles + full rules measured 74KB after deduplication.
PLAN = 'eng065-real-loop-v1'
COMPETITION_PLAN = 'competition-20261005-v1'
COMPETITION_PHASES = {'core':20, 'cases':25, 'evaluation':15, 'rehearsal':10, 'reserve':30}
SCHEMA = '''
CREATE TABLE IF NOT EXISTS news_model_plans(id TEXT PRIMARY KEY, total_micros INTEGER, pricing TEXT);
CREATE TABLE IF NOT EXISTS news_model_batches(plan TEXT,batch TEXT,max_calls INTEGER,PRIMARY KEY(plan,batch));
CREATE TABLE IF NOT EXISTS news_model_calls(id TEXT PRIMARY KEY,plan TEXT,batch TEXT,stage TEXT,request_sha TEXT,
 status TEXT,reserved_micros INTEGER,charged_micros INTEGER,folder TEXT,provider TEXT,provider_sha TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS news_model_failures(id TEXT PRIMARY KEY,plan TEXT,batch TEXT,stage TEXT,request_sha TEXT,
 status TEXT,folder TEXT,reason TEXT,created TEXT);
CREATE TABLE IF NOT EXISTS news_model_allocations(id TEXT PRIMARY KEY,plan TEXT,config TEXT);
CREATE TABLE IF NOT EXISTS news_model_allocated_calls(call_id TEXT PRIMARY KEY,allocation TEXT,purpose TEXT);
CREATE TABLE IF NOT EXISTS news_model_settlements(call_id TEXT PRIMARY KEY,status TEXT,charged_micros INTEGER,created TEXT);
CREATE TABLE IF NOT EXISTS news_model_phases(call_id TEXT PRIMARY KEY,phase TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_model_attempt_calls(plan TEXT,attempt TEXT,call_id TEXT PRIMARY KEY);
'''


def wire(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, allow_nan=False, separators=(',', ':')).encode('utf-8')


def immutable(path, data):
    if path.exists():
        if path.read_bytes() != data:
            raise ValueError('immutable_run_artifact_conflict')
        return
    with path.open('xb') as stream:
        stream.write(data)


def now():
    return datetime.now(timezone.utc).isoformat()


class ModelBudget:
    def __init__(self, db, total_rmb=50, batch_id='development-01', max_calls=49,
                 send_model=False, *, plan_id=PLAN, pricing=None, run_id=None, allocation=None, purpose='business', phase=None):
        if type(max_calls) is not int or not 1 <= max_calls <= 49:
            raise ValueError('batch_limit_must_be_1_to_49')
        amount = Decimal(str(total_rmb))
        maximum=100 if plan_id==COMPETITION_PLAN else 50
        if not amount.is_finite() or not 0 < amount <= maximum or not batch_id or not plan_id:
            raise ValueError('invalid_plan_budget')
        if plan_id==COMPETITION_PLAN:
            if phase not in COMPETITION_PHASES or allocation is not None or not run_id:
                raise ValueError('competition_phase_and_run_required')
        elif phase is not None:raise ValueError('phase_only_for_competition_plan')
        self.phase=phase
        self.path, self.plan_id, self.batch_id = Path(db), plan_id, batch_id
        if run_id is not None and (not isinstance(run_id,str) or not run_id.strip()):
            raise ValueError('invalid_explicit_run_id')
        self.run_id=run_id
        self.attempt_id = None
        self.attempt_limit = 2
        self.deadline = None
        self.allocation, self.purpose = allocation, purpose
        if allocation is not None:
            if (set(allocation) != {'id','batches','total_micros','engineering_micros','calls'} or
                not isinstance(allocation['id'],str) or not allocation['id'] or
                len(allocation['batches']) != 3 or len(set(allocation['batches'])) != 3 or
                any(not isinstance(b,str) or not b for b in allocation['batches']) or
                allocation['total_micros'] != 10000000 or allocation['engineering_micros'] != 9000000 or
                allocation['calls'] != {'business':87,'regression':48,'ui_trial':12} or
                purpose not in allocation['calls'] or not run_id or max_calls != 49):
                raise ValueError('invalid_mvp_allocation')
        self.send_model, self.max_calls = bool(send_model), max_calls
        self.total_micros = int((amount * 1000000).to_integral_value(rounding=ROUND_CEILING))
        if pricing is None:
            path = Path(__file__).resolve().parents[2] / 'artifacts/eng065/command/pricing.json'
            pricing = json.loads(path.read_text('utf-8')) if path.exists() else None
        self.pricing = pricing
        with closing(self.connect()) as connection, connection:
            connection.executescript(SCHEMA)
            connection.execute('BEGIN IMMEDIATE')
            old = connection.execute('SELECT * FROM news_model_plans WHERE id=?', (plan_id,)).fetchone()
            encoded = wire(pricing).decode()
            if old and (old['total_micros'] != self.total_micros or old['pricing'] != encoded):
                raise ValueError('plan_budget_or_pricing_changed')
            connection.execute('INSERT OR IGNORE INTO news_model_plans VALUES(?,?,?)', (plan_id, self.total_micros, encoded))
            old = connection.execute('SELECT max_calls FROM news_model_batches WHERE plan=? AND batch=?', (plan_id, batch_id)).fetchone()
            if old and old[0] != max_calls:
                raise ValueError('batch_call_limit_changed')
            connection.execute('INSERT OR IGNORE INTO news_model_batches VALUES(?,?,?)', (plan_id, batch_id, max_calls))
            if allocation:
                config=wire(allocation).decode()
                saved=connection.execute('SELECT plan,config FROM news_model_allocations WHERE id=?',(allocation['id'],)).fetchone()
                if saved and (saved['plan']!=plan_id or saved['config']!=config):raise ValueError('allocation_changed')
                connection.execute('INSERT OR IGNORE INTO news_model_allocations VALUES(?,?,?)',(allocation['id'],plan_id,config))
                for batch in allocation['batches']:
                    prior=connection.execute('SELECT max_calls FROM news_model_batches WHERE plan=? AND batch=?',(plan_id,batch)).fetchone()
                    if prior and prior[0]!=49:raise ValueError('allocation_batch_changed')
                    if not saved and connection.execute('SELECT 1 FROM news_model_calls WHERE plan=? AND batch=?',(plan_id,batch)).fetchone():raise ValueError('allocation_requires_unused_batches')
                    connection.execute('INSERT OR IGNORE INTO news_model_batches VALUES(?,?,49)',(plan_id,batch))

    def connect(self):
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        from event_workbench.news_deadline import bind
        return bind(connection,30000)

    def for_task(self,task_id,*,max_calls=None,deadline=None):
        """Every explicit attempt has a durable two-send cap, including failures."""
        if not isinstance(task_id,str) or not task_id:raise ValueError('explicit_task_identity_required')
        limit = min(2, self.max_calls if max_calls is None else max_calls)
        if type(limit) is not int or limit < 1:raise ValueError('invalid_attempt_call_limit')
        if self.phase:
            child = ModelBudget(self.path,total_rmb=Decimal(self.total_micros)/1000000,
                batch_id=self.phase+'-task-'+digest(task_id)[:24],max_calls=limit,
                send_model=self.send_model,plan_id=self.plan_id,pricing=self.pricing,
                run_id=self.run_id,purpose=self.purpose,phase=self.phase)
        else:
            child = copy.copy(self)
        child.attempt_id, child.attempt_limit = task_id, limit
        child.run_id = digest([self.run_id, task_id])
        child.deadline = deadline if deadline is not None else monotonic() + 90
        return child

    def remaining(self):
        remaining = 90 if self.deadline is None else self.deadline - monotonic()
        if remaining <= 0:raise TimeoutError('analysis_deadline_exceeded')
        return remaining

    def _rates(self):
        rates = self.pricing
        if not isinstance(rates, dict) or rates.get('model') != 'deepseek-flash' or rates.get('currency') != 'CNY':
            raise ValueError('pricing_not_verified')
        if not rates.get('checked_date') or rates.get('source') != 'https://api-docs.deepseek.com/zh-cn/quick_start/pricing/':
            raise ValueError('pricing_source_or_date_missing')
        values = [Decimal(str(rates[key])) for key in ('input_per_million', 'output_per_million')]
        if any(not value.is_finite() or value <= 0 for value in values):
            raise ValueError('invalid_verified_prices')
        return values

    def _failure(self, folder, stage, request_sha, reason, status='preflight_failed_not_sent'):
        record = dict(status=status, stage=stage, request_sha=request_sha, reason=reason, created=now())
        path = folder / 'failure.json'
        if path.exists():
            return
        immutable(path, wire(record))
        with closing(self.connect()) as connection, connection:
            connection.execute('INSERT OR IGNORE INTO news_model_failures VALUES(?,?,?,?,?,?,?,?,?)',
                (digest([self.plan_id, self.batch_id, str(folder), request_sha]), self.plan_id, self.batch_id, stage,
                 request_sha, status, str(folder), reason, record['created']))

    def _preflight(self, request):
        # Match the existing transport's actual serialization, including spaces.
        encoded = json.dumps(request, ensure_ascii=False, allow_nan=False).encode('utf-8')
        if request.get('model') != 'deepseek-flash' or len(encoded) > MAX_REQUEST_BYTES:
            raise ValueError('model_or_request_size_not_allowed')
        output = request.get('max_tokens')
        if type(output) is not int or not 1 <= output <= 32768:
            raise ValueError('bounded_max_tokens_required')
        if not isinstance(request.get('messages'), list) or not request['messages'] or any(
            not isinstance(m, dict) or m.get('role') not in ('system', 'user', 'assistant') or not isinstance(m.get('content'), str)
            for m in request['messages']):
            raise ValueError('invalid_model_messages')
        if request.get('stream') or request.get('tools') or request.get('n', 1) != 1:
            raise ValueError('only_single_nonstreaming_text_request_allowed')
        trial_transport.client.preflight(request)
        input_rate, output_rate = self._rates()
        # UTF-8 bytes plus framing reserve deliberately overestimate input tokens.
        reserve = int(((len(encoded) + 4096) * input_rate + output * output_rate).to_integral_value(rounding=ROUND_CEILING))
        return encoded, reserve

    def send(self, stage, request, folder):
        if not self.attempt_id or self.deadline is None:
            raise ValueError('explicit_analysis_attempt_required_not_sent')
        self.remaining()
        folder = Path(folder).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        request_sha = digest(request)
        identity=[self.plan_id, stage, request_sha]
        # Explicit repeat experiments share the original money cap; retries
        # within that experiment still reuse received answers or refuse resend.
        call_id = digest(identity if self.run_id is None else identity+[self.run_id])
        with closing(self.connect()) as connection:
            existing = connection.execute('SELECT * FROM news_model_calls WHERE id=?', (call_id,)).fetchone()
        if existing:
            if existing['status'] != 'received':
                raise ValueError('prior_attempt_unknown_or_failed_no_resend')
            provider = json.loads(existing['provider'])
            if digest(provider) != existing['provider_sha']:
                raise ValueError('cached_provider_binding_changed')
            immutable(folder / 'cache.json', wire(dict(call_id=call_id, original_folder=existing['folder'], model_executed=False)))
            return provider
        key = None
        preflight_stage = 'request_or_pricing'
        try:
            encoded, reserve = self._preflight(request)
            immutable(folder / 'request.json', encoded)
            if not self.send_model:
                raise ValueError('model_send_disabled')
            preflight_stage = 'credential_configuration'
            key = read_credential()
            if not isinstance(key, str) or not key or any(ord(ch) < 33 or ord(ch) > 126 for ch in key):
                raise ValueError('credential_configuration_invalid')
        except Exception as error:
            # Only exception class is retained: credentials/errors must never leak into artifacts.
            self._failure(folder, stage, request_sha, preflight_stage + ':' + type(error).__name__)
            key = None
            raise ValueError('model_preflight_failed_not_sent') from None
        blocked = False
        batch_id = self.batch_id
        with closing(self.connect()) as connection, connection:
            connection.execute('BEGIN IMMEDIATE')
            if connection.execute('SELECT 1 FROM news_model_calls WHERE id=?', (call_id,)).fetchone():
                key = None
                raise ValueError('request_already_reserved_no_resend')
            allocation_blocked=False
            if self.allocation:
                a=self.allocation
                rows=connection.execute('SELECT a.purpose,c.charged_micros FROM news_model_allocated_calls a JOIN news_model_calls c ON c.id=a.call_id WHERE a.allocation=?',(a['id'],)).fetchall()
                spent=sum(r['charged_micros'] for r in rows)
                engineering=sum(r['charged_micros'] for r in rows if r['purpose']!='ui_trial')
                allocation_blocked=(len(rows)>=147 or sum(r['purpose']==self.purpose for r in rows)>=a['calls'][self.purpose] or
                    spent+reserve>a['total_micros'] or (self.purpose!='ui_trial' and engineering+reserve>a['engineering_micros']))
                available=[b for b in a['batches'] if connection.execute('SELECT count(*) FROM news_model_calls WHERE plan=? AND batch=?',(self.plan_id,b)).fetchone()[0]<49]
                if not available:allocation_blocked=True
                else:batch_id=available[0]
            count = connection.execute('SELECT count(*) FROM news_model_calls WHERE plan=? AND batch=?', (self.plan_id, batch_id)).fetchone()[0]
            attempt_count = connection.execute('SELECT count(*) FROM news_model_attempt_calls WHERE plan=? AND attempt=?',
                (self.plan_id, self.attempt_id)).fetchone()[0] if self.attempt_id else 0
            committed = connection.execute('SELECT coalesce(sum(charged_micros),0) FROM news_model_calls WHERE plan=?', (self.plan_id,)).fetchone()[0]
            phase_blocked=False
            if self.phase:
                phase_spent=connection.execute('SELECT coalesce(sum(c.charged_micros),0) FROM news_model_calls c JOIN news_model_phases p ON p.call_id=c.id WHERE c.plan=? AND p.phase=?',(self.plan_id,self.phase)).fetchone()[0]
                phase_blocked=phase_spent+reserve>COMPETITION_PHASES[self.phase]*1000000
            if allocation_blocked or phase_blocked or count >= self.max_calls or attempt_count >= self.attempt_limit or committed + reserve > self.total_micros:
                key = None
                blocked = True
            else:
                connection.execute('INSERT INTO news_model_calls VALUES(?,?,?,?,?,?,?,?,?,?,?,?)',
                    (call_id, self.plan_id, batch_id, stage, request_sha, 'reserved_unknown_no_retry', reserve, reserve, str(folder), None, None, now()))
                if self.attempt_id:
                    connection.execute('INSERT INTO news_model_attempt_calls VALUES(?,?,?)', (self.plan_id, self.attempt_id, call_id))
                if self.allocation:
                    connection.execute('INSERT INTO news_model_allocated_calls VALUES(?,?,?)',(call_id,self.allocation['id'],self.purpose))
                if self.phase:connection.execute('INSERT INTO news_model_phases VALUES(?,?)',(call_id,self.phase))
        if blocked:
            self._failure(folder, stage, request_sha, 'budget_or_batch_limit', 'budget_blocked_not_sent')
            raise ValueError('budget_or_batch_limit')
        captured = []
        provider = None
        status, charged = 'send_unknown_no_retry', reserve
        try:
            immutable(folder / 'reservation.json', wire(dict(id=call_id, reserved_micros=reserve, pricing=self.pricing)))
            provider = trial_transport.send(request, key, self.remaining(), captured)
            self.remaining()
            status = 'received_invalid_no_retry'
            raw = b''.join(captured)
            if key.encode() in raw or trial_transport.client.common.redact(provider, key) != provider:
                raise ValueError('credential_echo_not_saved')
            if raw:
                immutable(folder / 'response.bin', raw)
            immutable(folder / 'provider.json', wire(provider))
            if provider.get('model') != 'deepseek-flash' or not isinstance(provider.get('choices'), list) or len(provider['choices']) != 1:
                raise ValueError('provider_identity_or_shape')
            answer = provider['choices'][0]['message']['content']
            if not isinstance(answer, str):
                raise ValueError('provider_content_missing')
            immutable(folder / 'answer.txt', answer.encode('utf-8'))
            usage = provider.get('usage') or {}
            if all(type(usage.get(k)) is int and usage[k] >= 0 for k in ('prompt_tokens', 'completion_tokens')):
                input_rate, output_rate = self._rates()
                charged = int((usage['prompt_tokens'] * input_rate + usage['completion_tokens'] * output_rate).to_integral_value(rounding=ROUND_CEILING))
            status = 'received'
            return provider
        except Exception as error:
            raw = b''.join(captured)
            if raw and key.encode() not in raw:
                immutable(folder / 'response.bin', raw)
            diagnostic=getattr(error,'diagnostics',{})
            balance=(isinstance(error,trial_transport.client.common.TransportFailure) and
                     diagnostic.get('http_status')==402 and diagnostic.get('send_status')=='response_received' and
                     diagnostic.get('parse_status')=='parsed' and
                     str(diagnostic.get('server_error',{}).get('message','')).startswith('Insufficient Balance'))
            self._failure(folder, stage, request_sha, 'provider_balance_insufficient' if balance else type(error).__name__, status)
            failure=ValueError('model_send_failed_or_unknown_no_retry')
            if balance:failure.public_reason='模型账户余额不足，分析已停止；原文和已完成结果已保留，不自动重发。'
            raise failure from None
        finally:
            key = None
            with closing(self.connect()) as connection, connection:
                saved = wire(provider).decode() if status == 'received' else None
                connection.execute('UPDATE news_model_calls SET status=?,charged_micros=?,provider=?,provider_sha=? WHERE id=?',
                    (status, charged, saved, digest(provider) if saved else None, call_id))
                if self.allocation or self.phase:
                    connection.execute('INSERT INTO news_model_settlements VALUES(?,?,?,?)',(call_id,status,charged,now()))

    def phase_snapshot(self):
        if not self.phase:return None
        with closing(self.connect()) as c:
            totals=dict(c.execute('SELECT p.phase,sum(c.charged_micros) FROM news_model_calls c JOIN news_model_phases p ON p.call_id=c.id WHERE c.plan=? GROUP BY p.phase',(self.plan_id,)))
        return dict(plan=self.plan_id,phase=self.phase,total_limit_rmb=self.total_micros/1000000,
                    phases={k:dict(limit_rmb=v,committed_rmb=totals.get(k,0)/1000000) for k,v in COMPETITION_PHASES.items()},invoice_verified=False)

    def allocation_snapshot(self):
        if not self.allocation:return None
        with closing(self.connect()) as c:
            rows=[dict(r) for r in c.execute('SELECT a.purpose,c.batch,c.stage,c.status,c.charged_micros FROM news_model_allocated_calls a JOIN news_model_calls c ON c.id=a.call_id WHERE a.allocation=?',(self.allocation['id'],))]
        return dict(id=self.allocation['id'],limits=self.allocation,purposes={p:dict(calls=sum(r['purpose']==p for r in rows),committed_rmb=sum(r['charged_micros'] for r in rows if r['purpose']==p)/1000000,remaining_calls=n-sum(r['purpose']==p for r in rows)) for p,n in self.allocation['calls'].items()},committed_rmb=sum(r['charged_micros'] for r in rows)/1000000,invoice_verified=False)

    def snapshot(self):
        with closing(self.connect()) as connection:
            calls = [dict(r) for r in connection.execute('SELECT id,batch,stage,status,reserved_micros,charged_micros,folder FROM news_model_calls WHERE plan=? ORDER BY created,id', (self.plan_id,))]
            failures = [dict(r) for r in connection.execute('SELECT status,stage,reason,folder FROM news_model_failures WHERE plan=? ORDER BY created,id', (self.plan_id,))]
        spent = sum(r['charged_micros'] for r in calls)
        return dict(plan_id=self.plan_id, total_rmb=str(Decimal(self.total_micros) / 1000000), committed_rmb=str(Decimal(spent) / 1000000),
                    calls=calls, failures=failures, invoice_verified=False)
