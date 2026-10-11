"""Dependency-bound legacy reads and SQLite-owned bounded execution."""
import copy
import json
import os
import threading
import uuid
from pathlib import Path
import psutil
from event_workbench import unified_pipeline as base




class Pipeline(base.Pipeline):
    def __init__(self,path,adapters=None,plan=None):
        # Do not run the old constructor's unconditional running recovery.
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.adapters=adapters or {};self.plan=plan or dict(id='ENG023-R1-zero',max_new_calls=0,allowed_stages=[]);self.lock=threading.RLock();self.threads=[]
        if type(self.plan.get('max_new_calls')) is not int or self.plan['max_new_calls']<0 or not isinstance(self.plan.get('id'),str) or not self.plan['id'] or not set(self.plan.get('allowed_stages',[]))<=set(base.STAGES):raise ValueError('invalid_finite_plan')
        self.plan=dict(self.plan,allowed_stages=sorted(set(self.plan['allowed_stages'])))
        if 'stage_order' in self.plan and (len(self.plan['stage_order'])!=len(base.STAGES) or set(self.plan['stage_order'])!=set(base.STAGES)):raise ValueError('invalid_stage_order')
        self.pid=os.getpid();self.birth=psutil.Process(self.pid).create_time()
        with self.connect() as c:
            c.executescript('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,document TEXT,created TEXT); CREATE TABLE IF NOT EXISTS stages(job TEXT,stage TEXT,status TEXT,reason TEXT,input_sha TEXT,semantic_sha TEXT,result TEXT,provenance TEXT,PRIMARY KEY(job,stage)); CREATE TABLE IF NOT EXISTS signals(name TEXT PRIMARY KEY,body TEXT); CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,job TEXT,stage TEXT,plan TEXT,cost INTEGER,status TEXT,request TEXT); CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,sha TEXT,body TEXT); CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,stage TEXT,body TEXT); CREATE TABLE IF NOT EXISTS withdrawals(id TEXT PRIMARY KEY,reason TEXT); CREATE TABLE IF NOT EXISTS finite_plans(id TEXT PRIMARY KEY,config TEXT); CREATE TABLE IF NOT EXISTS cache_bindings(job TEXT,stage TEXT,signature TEXT,PRIMARY KEY(job,stage));')
            c.execute('BEGIN IMMEDIATE');columns={r['name'] for r in c.execute('PRAGMA table_info(attempts)')}
            for name,kind in [('owner','TEXT'),('owner_pid','INTEGER'),('owner_birth','REAL'),('input_sha','TEXT'),('output','TEXT'),('output_sha','TEXT')]:
                if name not in columns:c.execute('ALTER TABLE attempts ADD COLUMN '+name+' '+kind)
            config=json.dumps(self.plan,sort_keys=True)
            old=c.execute('SELECT config FROM finite_plans WHERE id=?',(self.plan['id'],)).fetchone()
            if old and old[0]!=config:raise ValueError('finite_plan_configuration_drift')
            c.execute('INSERT OR IGNORE INTO finite_plans VALUES(?,?)',(self.plan['id'],config))
        self.recover_abandoned()

    @staticmethod
    def owner_alive(pid,birth):
        if pid is None or birth is None:return True  # Unknown legacy ownership is not proof of death.
        try:return psutil.Process(pid).create_time()==birth
        except psutil.NoSuchProcess:return False
        except psutil.AccessDenied:return True

    def recover_abandoned(self):
        with self.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            for a in c.execute("SELECT * FROM attempts WHERE status='running'").fetchall():
                if self.owner_alive(a['owner_pid'],a['owner_birth']):continue
                c.execute("UPDATE attempts SET status='unknown_no_retry' WHERE id=? AND owner=?",(a['id'],a['owner']))
                r=self._states(c,a['job'])[a['stage']]
                if r['status']=='running':self._write(c,a['job'],a['stage'],'unknown_no_retry','执行进程已退出，发送状态未知，不自动重试',r['input_sha'],r['result'],r['provenance'])


    def update_signal(self,name,value):
        normalized=base.semantic(value)
        with self.connect() as c:
            old=c.execute('SELECT body FROM signals WHERE name=?',(name,)).fetchone();prior=base.semantic(json.loads(old[0])) if old else None
        if (normalized or None)==(prior or None):return False
        return super().update_signal(name,value)

    def run(self,jid):
        for stage in self.plan.get('stage_order',base.STAGES):
            with self.connect() as c:
                # All quota tests and the unique ownership insert are one transaction.
                c.execute('BEGIN IMMEDIATE');states=self._states(c,jid);r=states[stage]
                if r['status'] in base.GOOD|{'running','unknown_no_retry','failed','not_applicable','withdrawn'}:continue
                sig=self._signature(c,jid,stage,states)
                if stage not in ('extract','mentions') and states['mentions']['status'] in base.GOOD and not (states['mentions']['result'] or {}).get('mentions'):
                    self._write(c,jid,stage,'not_applicable','上游没有可用提及',sig);continue
                if any(states[d]['status'] not in base.GOOD for d in base.DEPS[stage]):continue
                if stage=='relations' and not (states['relation_candidates']['result'] or {}).get('candidates'):
                    self._write(c,jid,stage,'not_applicable','没有历史候选，不调用关系模型',sig);continue
                config=json.loads(c.execute('SELECT config FROM finite_plans WHERE id=?',(self.plan['id'],)).fetchone()[0])
                if config!=self.plan:raise ValueError('finite_plan_configuration_drift')
                adapter=self.adapters.get(stage)
                if not adapter or stage not in config['allowed_stages']:continue
                if type(adapter.get('cost')) is not int or adapter['cost']<0:raise ValueError('invalid_adapter_cost')
                doc=json.loads(c.execute('SELECT document FROM jobs WHERE id=?',(jid,)).fetchone()[0])
                signals={x['name']:base.semantic(json.loads(x['body'])) for x in c.execute('SELECT * FROM signals')}
                context=dict(document=doc,job=jid,dependencies={d:states[d]['result'] for d in base.DEPS[stage]},dependency_signatures={d:states[d]['semantic_sha'] for d in base.DEPS[stage]},signals=signals,input_signature=sig)
                try:request=adapter['prepare'](context)
                except (ValueError,TypeError,KeyError,IndexError,OSError) as exc:
                    result=r['result']
                    if str(exc) in ('joint_request_capacity_exceeded_not_sent','request_over_limit_no_truncation'):
                        result=dict(result or {},public_reason='本次正文及分析材料超过单次请求容量，请选择较短的正文范围后重新提交。未截断原文，本阶段未发送模型请求。')
                    self._write(c,jid,stage,'failed','本地准备失败（未发送）：'+type(exc).__name__+': '+str(exc),sig,result,r['provenance'])
                    continue
                if isinstance(request,dict) and request.get('deferred_reason'):
                    self._write(c,jid,stage,r['status'],request['deferred_reason'],r['input_sha'],r['result'],r['provenance'])
                    continue
                if request is None:
                    if r['status']=='stale':self._write(c,jid,stage,'stale','当前依赖/规则不适用旧缓存；原结果仅历史可看',r['input_sha'],r['result'],r['provenance'])
                    continue
                aid=base.digest(dict(stage=stage,document=doc,request=request));previous=c.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone()
                if previous and previous['status']!='running' and previous['input_sha']!=sig:
                    # Explicitly reset stages carry a new input signature. Preserve
                    # the old attempt and give the new attempt its own identity.
                    aid=base.digest(dict(previous_request=aid,input_signature=sig))
                    previous=c.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone()
                if previous:
                    if previous['status'] in base.GOOD and previous['input_sha']==sig and previous['output']:
                        output=json.loads(previous['output'])
                        if base.digest(output)!=previous['output_sha']:raise ValueError('completed_output_binding_changed')
                        self._write(c,jid,stage,'cached','已绑定成功结果复用，不再执行',sig,output.get('result'),dict(request_identity=aid,owner=previous['owner']))
                    elif previous['status'] in ('unknown_no_retry','reserved_unknown'):
                        self._write(c,jid,stage,'unknown_no_retry','已知未知发送记录，不重新执行',r['input_sha'],r['result'],r['provenance'])
                    # Observers never change an active/unknown owner's state or execute.
                    continue
                config=json.loads(c.execute('SELECT config FROM finite_plans WHERE id=?',(self.plan['id'],)).fetchone()[0])
                spent=c.execute('SELECT coalesce(sum(cost),0) FROM attempts WHERE plan=?',(self.plan['id'],)).fetchone()[0]
                if spent+adapter['cost']>config['max_new_calls']:
                    self._write(c,jid,stage,r['status'],'有限计划额度已占用，不执行',r['input_sha'],r['result'],r['provenance']);continue
                owner=uuid.uuid4().hex
                c.execute('INSERT INTO attempts(id,job,stage,plan,cost,status,request,owner,owner_pid,owner_birth,input_sha) VALUES(?,?,?,?,?,?,?,?,?,?,?)',(aid,jid,stage,self.plan['id'],adapter['cost'],'running',json.dumps(request,ensure_ascii=False),owner,self.pid,self.birth,sig))
                self._write(c,jid,stage,'running','已取得数据库唯一执行权',sig,provenance=dict(owner=owner,request_identity=aid))
            try:
                output=adapter['execute'](context,request);status=output.get('status','ready')
                if status not in base.GOOD|{'failed','unknown_no_retry','not_applicable'}:raise ValueError('adapter_status')
            except Exception as exc:
                status='unknown_no_retry' if adapter['cost'] else 'failed'
                output=dict(reason='执行失败或发送未知，不自动重试' if adapter['cost'] else '本地执行失败（未发送）：'+type(exc).__name__+': '+str(exc))
            with self.connect() as c:
                c.execute('BEGIN IMMEDIATE');owned=c.execute('SELECT status FROM attempts WHERE id=? AND owner=?',(aid,owner)).fetchone()
                if not owned or owned['status']!='running':continue
                c.execute('UPDATE attempts SET status=?,output=?,output_sha=? WHERE id=? AND owner=?',(status,json.dumps(output,ensure_ascii=False),base.digest(output),aid,owner))
                self._write(c,jid,stage,status,output.get('reason','执行结果已绑定保存'),sig,output.get('result'),dict(owner=owner,request_identity=aid,synthetic=self.plan.get('synthetic',False)))
