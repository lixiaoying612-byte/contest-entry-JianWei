"""One persisted, user-submitted queue for the existing NewsProcessor."""
import copy
import json
import os
import queue
import sqlite3
import threading
from time import perf_counter, monotonic
from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path
import psutil
from event_workbench import news_intake, news_receivers_v3,news_joint
from event_workbench.news_budget import ModelBudget, now
from event_workbench import news_deadline
from event_workbench.news_materials import MaterialScope
from event_workbench.news_processing import NewsProcessor
from event_workbench.news_events import EventPublisher
from event_workbench.product_replay import ROOT, DEFAULT
from event_workbench.document_pipeline import digest

# Reserve one second for sealing state and the existing 700 ms page poll.
PROCESSING_SECONDS=89
TASK='mvp-20260921'
ALLOCATION=dict(id=TASK,batches=[TASK+'-01',TASK+'-02',TASK+'-03'],total_micros=10000000,engineering_micros=9000000,calls=dict(business=87,regression=48,ui_trial=12))


def budget_for(run_id,purpose,send_model=False,ledger=None):
    return ModelBudget(ledger or DEFAULT/'workbench.sqlite3',batch_id=ALLOCATION['batches'][0],send_model=send_model,run_id=TASK+':'+run_id,allocation=ALLOCATION,purpose=purpose)


def complete_result(c,run_id,job,events):
    from event_workbench.product_research import publication_gaps
    from event_workbench.news_events import current_member_owner
    rows=c.execute('SELECT stage,status,result FROM stages WHERE job=?',(job,)).fetchall()
    if len(rows)!=7 or any(r['status'] not in ('ready','cached','not_applicable') for r in rows):return False
    mentions=next((json.loads(r['result'] or '{}').get('mentions',[]) for r in rows if r['stage']=='mentions'),[])
    valid={e['member_id'] for e in events if not e.get('publication_update_pending') and not publication_gaps(e)
           and all(not d.get('stale_assessment') and d.get('impact_event_version',e.get('event_version'))==e.get('event_version') for d in e.get('warning_reasons',{}).get('decisions',[]))}
    linked={e['member_id'] for e in events if any(m.get('document_id')==job for m in e.get('evidence_sources',{}).get('members',[]))}
    return linked<=valid and all(current_member_owner(c,run_id,m) in valid for m in mentions)


class NewsService:
    def __init__(self,app,run_id,*,send_model=False,kind='user',budget=None,contract_revision=news_joint.VERSION):
        if contract_revision!=news_joint.VERSION:raise ValueError('legacy_execution_retired_use_explicit_contract_upgrade')
        if send_model and (budget is None or not budget.phase):raise ValueError('competition_budget_required')
        self.contract_revision=contract_revision
        if app.root==DEFAULT.resolve():raise ValueError('MVP必须使用新的隔离工作区')
        news_intake.register(app,run_id,kind)
        app.setup_catalog()
        self.app,self.run_id,self.send_model=app,run_id,send_model
        self.budget=budget or (budget_for(run_id,'ui_trial' if kind=='user' else kind,send_model) if send_model else None)
        self.pid=os.getpid();self.birth=psutil.Process(self.pid).create_time()
        self.queue=queue.Queue();self.stop=threading.Event()
        self.deadline_timers={}
        EventPublisher(app,MaterialScope(app,None,run_id))
        with closing(app.connect()) as c,c:
            c.execute('CREATE TABLE IF NOT EXISTS news_tasks(id TEXT PRIMARY KEY,run_id TEXT,source_row TEXT,contract TEXT,state TEXT,reason TEXT,owner_pid INTEGER,owner_birth REAL,created TEXT,updated TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS news_task_publications(task_id TEXT,event_id TEXT,change_kind TEXT,published_at TEXT,event_version INTEGER,material_version INTEGER,PRIMARY KEY(task_id,event_id))')
            c.execute('CREATE TABLE IF NOT EXISTS news_task_limits(task_id TEXT PRIMARY KEY,submitted_at TEXT,deadline_at TEXT)')
            c.execute('CREATE TABLE IF NOT EXISTS news_task_retries(task_id TEXT PRIMARY KEY,previous_task_id TEXT UNIQUE)')
        # Startup deliberately does not resume persisted jobs or read credentials.
        self.worker=threading.Thread(target=self._work,daemon=True,name='news-mvp-worker');self.worker.start()

    def close(self):
        self.stop.set();self.queue.put(None)
        for timer in list(self.deadline_timers.values()):timer.cancel()

    def _finish(self, tid, state, reason):
        # Terminal visibility also derives from persisted limits when a writer holds the DB.
        token=news_deadline.current.set(monotonic()+0.2)
        try:
            with closing(self.app.connect()) as c:
                news_deadline.bind(c,200)
                with c:
                    c.execute('BEGIN IMMEDIATE')
                    limits=c.execute('SELECT deadline_at FROM news_task_limits WHERE task_id=?',(tid,)).fetchone()
                    if limits and now()>=limits['deadline_at']:
                        state='partial';reason='本次分析已到处理时限；已有结果保留，可手动再次分析'
                    changed=c.execute("UPDATE news_tasks SET state=?,reason=?,updated=? WHERE id=? AND state IN ('queued','running')",(state,reason,now(),tid)).rowcount
                    if changed:c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(self.run_id,))
            return True
        except (sqlite3.OperationalError,TimeoutError) as error:
            if isinstance(error,sqlite3.OperationalError) and getattr(error,'sqlite_errorcode',None) not in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED,sqlite3.SQLITE_INTERRUPT):raise
            return False
        finally:news_deadline.current.reset(token)

    def _expire(self, tid):
        return self._finish(tid,'partial','本次分析已到处理时限；已有结果保留，可手动再次分析')

    @staticmethod
    def _deadline_state(task, limits):
        task=dict(task)
        if limits and task['state'] in ('queued','running') and now()>=limits['deadline_at']:
            task.update(state='partial',updated=limits['deadline_at'],reason='本次分析已到处理时限；已有结果保留，可手动再次分析')
        return task

    def submit(self,payload):
        submitted_at=now()
        if not isinstance(payload,dict) or set(payload)-{'run','revision','material_id','retry_of'}:raise ValueError('处理字段不合法')
        if payload.get('run')!=self.run_id:raise ValueError('不能处理其他运行的材料')
        if not self.send_model:raise ValueError('本服务未开启模型发送；新闻已保存')
        if payload.get('retry_of'):return self.retry(payload,submitted_at)
        identity=payload.get('material_id');document=self.app.document(self.run_id,identity,include_analysis=False)
        news_intake.guard({k:document[k] for k in ('title','text','source','publication_time')})
        tid=digest([self.run_id,identity,self.contract_revision,news_joint.METHOD]) if self.contract_revision==news_joint.VERSION else digest([self.run_id,identity,self.contract_revision])
        with closing(self.app.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE');run=self.app.run(c,self.run_id)
            old=c.execute('SELECT * FROM news_tasks WHERE id=?',(tid,)).fetchone()
            if self.contract_revision==news_joint.VERSION:
                prior=c.execute('''SELECT n.* FROM news_tasks n
                    JOIN source_rows r ON r.row_key=n.source_row JOIN documents d ON d.id=r.document_id JOIN contents t ON t.id=d.content_id
                    WHERE n.run_id=? AND n.state IN ('queued','running','unknown_no_retry','completed')
                    AND NOT EXISTS (SELECT 1 FROM news_task_retries retry WHERE retry.previous_task_id=n.id)
                    AND (n.source_row=? OR (t.body=? AND coalesce(r.source_raw,'')=? AND coalesce(r.published_raw,'')=?))
                    ORDER BY CASE n.state WHEN 'unknown_no_retry' THEN 0 WHEN 'running' THEN 1 WHEN 'queued' THEN 2 ELSE 3 END,n.created DESC LIMIT 1''',
                    (self.run_id,identity,document['text'],document['source'] or '',document['publication_time'] or '')).fetchone()
                if prior:
                    if prior['state']=='unknown_no_retry':return dict(task=dict(prior),duplicate=True)
                    jid=digest({k:document[k] for k in ('title','text','source','publication_time')})
                    alive=False
                    try:alive=abs(psutil.Process(prior['owner_pid']).create_time()-prior['owner_birth'])<0.001
                    except psutil.Error:pass
                    if prior['state']=='running' or prior['state']=='queued' and alive:
                        task=dict(prior)
                        if not alive:task.update(state='unknown_no_retry',reason='原任务发送状态待核对；不自动重发')
                        return dict(task=task,duplicate=True)
                    if prior['state']=='completed':
                        saved=[json.loads(r['body']) for r in self.app.visible_event_rows(c,run,current_only=True,events_only=True)]
                        if complete_result(c,self.run_id,jid,saved):return dict(task=dict(prior),duplicate=True)
            legacy=news_intake.legacy_material(self.app,c,run,document) if not old or old['state']=='completed' else None
            if legacy:
                stamp=now();events=[]
                for previous in legacy['events']:
                    event=copy.deepcopy(previous)
                    members=event['evidence_sources']['members']
                    if not any(m.get('source_row')==identity for m in members):
                        if payload.get('revision')!=run['revision']:raise ValueError('工作区版本已变化，请刷新后继续处理；新闻仍已保存')
                        member=dict(source_row=identity,document_id=digest({k:document[k] for k in ('title','text','source','publication_time')}),
                            title=document['title'],source=document['source'],publication_time=document['publication_time'],available_at=document['available_at'])
                        if not previous.get('case_id'):member.update(mention_id=None,repeated_material_of=legacy['material_id'])
                        members.append(member);event['evidence_sources']['source_rows'].append(identity)
                        event['material_count']=len(members);event['material_version']+=1
                        event['event_timeline']['items'].append(dict(time=document['available_at'],kind='新增重复正文材料',summary='正文、来源与发布时间一致，业务事实未变',source_row=identity))
                        event.setdefault('source_relations',dict(nodes=[],edges=[]))
                        event['source_relations']['nodes']=copy.deepcopy(members)
                        event['source_relations']['edges'].append(dict(material_a=identity,material_b=legacy['material_id'],relation='相同正文',reason='正文、来源与发布时间逐字一致，仅标题或输入记录不同',independent_confirmation=False))
                        last=c.execute('SELECT signature FROM news_event_versions WHERE run_id=? AND event_id=? ORDER BY material_version DESC LIMIT 1',(self.run_id,event['member_id'])).fetchone()
                        c.execute('INSERT INTO news_event_versions VALUES(?,?,?,?,?,?,?)',(self.run_id,event['member_id'],event['event_version'],event['material_version'],last['signature'],json.dumps(event,ensure_ascii=False),stamp))
                        c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?',(json.dumps(event,ensure_ascii=False),self.run_id,event['member_id']))
                        c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(self.run_id,))
                    events.append(event)
                reason='复用此前整理的历史档案；相同正文不增加事实或独立证据，本次未重新分析'
                c.execute('INSERT OR IGNORE INTO news_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,self.run_id,identity,self.contract_revision,'completed',reason,self.pid,self.birth,stamp,stamp))
                for event in events:c.execute('INSERT OR IGNORE INTO news_task_publications VALUES(?,?,?,?,?,?)',(tid,event['member_id'],'unchanged',stamp,event['event_version'],event['material_version']))
                return dict(task=dict(id=tid,state='completed',source_row=identity,reason=reason),duplicate=True,historical_result_reused=True)
            if not old:
                jid=digest({k:document[k] for k in ('title','text','source','publication_time')})
                stage_states=[r[0] for r in c.execute('SELECT status FROM stages WHERE job=?',(jid,))] if c.execute("SELECT 1 FROM sqlite_master WHERE name='stages'").fetchone() else []
                if all(s in ('ready','cached','not_applicable') for s in stage_states):
                    saved=[json.loads(r['body']) for r in self.app.visible_event_rows(c,run,current_only=True,events_only=True)]
                    saved=[e for e in saved if not e.get('publication_update_pending') and any(m.get('document_id')==jid for m in e.get('evidence_sources',{}).get('members',[]))]
                    if saved:
                        stamp=now();state='completed' if complete_result(c,self.run_id,jid,saved) else 'partial'
                        reason='复用已保存的有效档案' if state=='completed' else '已保存结果未通过当前发布检查；原文和旧档案保留'
                        c.execute('INSERT INTO news_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,self.run_id,identity,self.contract_revision,state,reason,self.pid,self.birth,stamp,stamp))
                        for event in saved:c.execute('INSERT OR IGNORE INTO news_task_publications VALUES(?,?,?,?,?,?)',(tid,event['member_id'],'unchanged',stamp,event.get('event_version'),event.get('material_version')))
                        return dict(task=dict(id=tid,state=state,source_row=identity),duplicate=True)
            if old:
                if old['state']=='completed':
                    jid=digest({k:document[k] for k in ('title','text','source','publication_time')})
                    saved=[json.loads(r['body']) for r in self.app.visible_event_rows(c,run,current_only=True,events_only=True)]
                    if not complete_result(c,self.run_id,jid,saved):
                        reason='已保存结果未通过当前发布检查；原文和旧档案保留'
                        c.execute("UPDATE news_tasks SET state='partial',reason=?,updated=? WHERE id=?",(reason,now(),tid))
                        return dict(task=dict(dict(old),state='partial',reason=reason),duplicate=True)
                active=False
                try:active=abs(psutil.Process(old['owner_pid']).create_time()-old['owner_birth'])<0.001
                except psutil.Error:pass
                resumable=False
                if old['state'] in ('failed','partial','completed') and c.execute("SELECT 1 FROM sqlite_master WHERE name='stages'").fetchone():
                    jid=digest({k:document[k] for k in ('title','text','source','publication_time')})
                    rows=c.execute('SELECT stage,status,result FROM stages WHERE job=?',(jid,)).fetchall()
                    resumable=any((r['status'] in ('failed','partial') and json.loads(r['result'] or '{}').get('processing_status') in ('not_sent','not_sent_remaining')) or
                        (r['stage']=='impact' and r['status']=='failed' and json.loads(r['result'] or '{}').get('receiver') not in (None,news_receivers_v3.VERSION)) for r in rows) and not any(r['status'] in ('running','unknown_no_retry') for r in rows)
                    publication_only=bool(rows) and all(r['status'] in ('ready','cached','not_applicable') for r in rows) and any(r['stage']=='mentions' and json.loads(r['result'] or '{}').get('mentions') for r in rows) and not c.execute('SELECT 1 FROM news_task_publications WHERE task_id=?',(tid,)).fetchone()
                    resumable=resumable or publication_only
                if not resumable and (old['state']!='queued' or active):
                    result=dict(old)
                    if old['state']=='running' and not active:result.update(state='unknown_no_retry',reason='处理进程已中断，发送状态待核对；不自动重发')
                    return dict(task=result,duplicate=True)
                # A dead owner's queued task never entered NewsProcessor; explicit submit resumes it.
            if payload.get('revision')!=run['revision']:raise ValueError('工作区版本已变化，请刷新后继续处理；新闻仍已保存')
            budget=self.budget.allocation_snapshot()
            phase=self.budget.phase_snapshot()
            if phase and phase['phases'][phase['phase']]['committed_rmb']>=phase['phases'][phase['phase']]['limit_rmb']:
                raise ValueError('当前用途预算耗尽；新闻仍已保存')
            purpose='ui_trial' if budget and self.budget.purpose=='ui_trial' else self.budget.purpose
            if budget and (budget['purposes'][purpose]['remaining_calls']<=0 or budget['committed_rmb']>=10 or (purpose!='ui_trial' and sum(v['committed_rmb'] for k,v in budget['purposes'].items() if k!='ui_trial')>=9)):
                raise ValueError('本轮预算或调用名额已耗尽；新闻仍已保存')
            stamp=now()
            if old:c.execute("UPDATE news_tasks SET state='queued',reason='明确提交继续未完成步骤，复用有效结果',owner_pid=?,owner_birth=?,updated=? WHERE id=?",(self.pid,self.birth,stamp,tid))
            else:c.execute('INSERT INTO news_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,self.run_id,identity,self.contract_revision,'queued','等待处理',self.pid,self.birth,stamp,stamp))
            deadline_at=(datetime.fromisoformat(submitted_at)+timedelta(seconds=PROCESSING_SECONDS)).isoformat()
            c.execute('INSERT OR REPLACE INTO news_task_limits VALUES(?,?,?)',(tid,submitted_at,deadline_at))
        self._enqueue(tid,deadline_at)
        return dict(task=dict(id=tid,state='queued',source_row=identity),duplicate=False)

    def retry(self, payload, submitted_at):
        previous=payload['retry_of']
        if not isinstance(previous,str):raise ValueError('再次分析任务编号不合法')
        tid=digest([previous,'manual-retry',news_joint.METHOD])
        with closing(self.app.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            run=self.app.run(c,self.run_id)
            old=c.execute('SELECT * FROM news_tasks WHERE id=? AND run_id=?',(previous,self.run_id)).fetchone()
            if not old or old['source_row']!=payload.get('material_id'):raise ValueError('再次分析的新闻不匹配')
            saved=c.execute('SELECT * FROM news_tasks WHERE id=?',(tid,)).fetchone()
            if saved:return dict(task=dict(saved),duplicate=True)
            old=self._deadline_state(old,c.execute('SELECT deadline_at FROM news_task_limits WHERE task_id=?',(previous,)).fetchone())
            if old['state'] not in ('failed','partial','unknown_no_retry','not_sent'):raise ValueError('仅未完成的任务可再次分析')
            active=c.execute("SELECT * FROM news_tasks WHERE run_id=? AND source_row=? AND state IN ('queued','running') AND id NOT IN (SELECT task_id FROM news_task_limits WHERE deadline_at<=?) ORDER BY created DESC LIMIT 1",(self.run_id,old['source_row'],now())).fetchone()
            if active:return dict(task=dict(active),duplicate=True)
            if old['owner_pid']!=self.pid:
                try:alive=abs(psutil.Process(old['owner_pid']).create_time()-old['owner_birth'])<0.001
                except psutil.Error:alive=False
                if alive:raise ValueError('另一处理进程尚未退出，请稍后再次分析')
            if payload.get('revision')!=run['revision']:raise ValueError('工作区版本已变化，请刷新后再次分析')
            deadline_at=(datetime.fromisoformat(submitted_at)+timedelta(seconds=PROCESSING_SECONDS)).isoformat()
            c.execute('INSERT INTO news_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',(tid,self.run_id,old['source_row'],self.contract_revision,'queued','手动再次分析，复用有效结果',self.pid,self.birth,submitted_at,submitted_at))
            c.execute('INSERT INTO news_task_retries VALUES(?,?)',(tid,previous))
            c.execute('INSERT INTO news_task_limits VALUES(?,?,?)',(tid,submitted_at,deadline_at))
        self._enqueue(tid,deadline_at)
        return dict(task=dict(id=tid,state='queued',source_row=old['source_row']),duplicate=False)

    def _enqueue(self, tid, deadline_at):
        timer=threading.Timer(max(0,(datetime.fromisoformat(deadline_at)-datetime.fromisoformat(now())).total_seconds()),self._expire,args=(tid,))
        timer.daemon=True;self.deadline_timers[tid]=timer;timer.start()
        self.queue.put(tid)

    def _work(self):
        while not self.stop.is_set():
            tid=self.queue.get()
            if tid is None:return
            state='failed';reason='处理失败';started=perf_counter();trace=dict(task_id=tid,started_at=now(),phases=[]);processor=None
            checkpoint=started;deadline_token=None
            def mark(phase):
                nonlocal checkpoint
                current=perf_counter();trace['phases'].append(dict(phase=phase,elapsed_ms=round((current-checkpoint)*1000)));checkpoint=current
            try:
                with closing(self.app.connect()) as c,c:
                    c.execute('BEGIN IMMEDIATE')
                    row=c.execute('SELECT * FROM news_tasks WHERE id=?',(tid,)).fetchone()
                    if not row or row['state']!='queued' or row['owner_pid']!=self.pid or row['owner_birth']!=self.birth:continue
                    limits=c.execute('SELECT * FROM news_task_limits WHERE task_id=?',(tid,)).fetchone()
                    retry=bool(c.execute('SELECT 1 FROM news_task_retries WHERE task_id=?',(tid,)).fetchone())
                    remaining=(datetime.fromisoformat(limits['deadline_at'])-datetime.fromisoformat(now())).total_seconds() if limits else PROCESSING_SECONDS
                    deadline=monotonic()+remaining
                    if remaining<=0:raise TimeoutError('analysis_deadline_exceeded')
                    trace['queue_ms']=max(0,round((datetime.fromisoformat(now())-datetime.fromisoformat(row['updated'])).total_seconds()*1000))
                    c.execute("UPDATE news_tasks SET state='running',reason='正在执行AI初判',updated=? WHERE id=?",(now(),tid))
                deadline_token=news_deadline.current.set(deadline)
                scope=MaterialScope(self.app,None,self.run_id)
                before={e['member_id']:e for e in self.app.events(self.run_id,events_only=True)}
                mark('load_input_and_current_archives')
                task_budget=self.budget.for_task(tid,max_calls=2,deadline=deadline) if isinstance(self.budget,ModelBudget) else self.budget
                processor=NewsProcessor(self.app,scope,task_budget,self.app.root/'processing',plan_id=self.contract_revision+':'+self.run_id+':'+tid,send_model=self.send_model,contract_revision=self.contract_revision)
                processor.upgrade_user_contract(row['source_row'])
                if retry:processor.restart_incomplete(row['source_row'],tid)
                result=processor.process(row['source_row'])
                processor.check_deadline()
                mark('analysis')
                mark('publish')
                # Publish receipts describe readable archives, never intermediate drafts.
                after=self.app.events(self.run_id,events_only=True);stamp=now()
                with closing(self.app.connect()) as c,c:
                    for event in after:
                        if not any(m.get('document_id')==result['id'] for m in event.get('evidence_sources',{}).get('members',[])):continue
                        old=before.get(event['member_id'])
                        change='created' if old is None else 'progress' if event.get('progress_count',1)>old.get('progress_count',1) else 'material' if event.get('material_count',1)>old.get('material_count',1) else 'assessment' if event.get('material_version')!=old.get('material_version') else 'unchanged'
                        c.execute('INSERT OR IGNORE INTO news_task_publications VALUES(?,?,?,?,?,?)',(tid,event['member_id'],change,stamp,event.get('event_version'),event.get('material_version')))
                        c.execute('UPDATE news_task_publications SET event_version=?,material_version=? WHERE task_id=? AND event_id=?',(event.get('event_version'),event.get('material_version'),tid,event['member_id']))
                        from event_workbench.product_research import objects
                        for obj in objects(event):
                            if not obj['id'].startswith('business:'):continue
                            catalogue=dict(obj,definitions=[],source='已通过共同对象校验的业务对象',identity_status='code_resolved' if obj['code'] else 'name_grounded_code_unknown')
                            c.execute('INSERT OR REPLACE INTO replay_catalog VALUES(?,?)',(obj['id'],json.dumps(catalogue,ensure_ascii=False)))
                statuses={s['status'] for s in result['stages'].values()}
                state='unknown_no_retry' if 'unknown_no_retry' in statuses else 'partial' if 'failed' in statuses or 'waiting' in statuses or 'partial' in statuses else 'completed'
                reason=next((s['result']['public_reason'] for s in result['stages'].values()
                             if (s.get('result') or {}).get('public_reason')),
                            '结果及各阶段问题已保存；AI初判不等于独立核实')
                with closing(self.app.connect()) as c:
                    complete=complete_result(c,self.run_id,result['id'],after)
                if not complete and state=='completed':
                    state='partial';reason='已有具体事项，但档案发布检查尚未通过；原文和阶段结果已保留'
                mark('read_published_result')
            except Exception as error:
                # Never persist arbitrary credential/provider exception content.
                if isinstance(error,TimeoutError) or deadline_token is not None and monotonic()>=deadline:state='partial';reason='本次分析已到处理时限；已有结果保留，可手动再次分析'
                else:reason='处理未完成：'+type(error).__name__
            finally:
                if deadline_token is not None:news_deadline.current.reset(deadline_token)
                timer=self.deadline_timers.pop(tid,None)
                if timer:timer.cancel()
                sealed=self._finish(tid,state,reason)
                trace['terminal_state_persisted']=sealed
                trace.update(state=state,finished_at=now(),total_ms=round((perf_counter()-started)*1000),stages=processor.timings if processor else [])
                folder=self.app.root/'processing'/'task-timings';folder.mkdir(parents=True,exist_ok=True)
                (folder/(digest([tid,trace['started_at']])+'.json')).write_text(json.dumps(trace,ensure_ascii=False,indent=2),encoding='utf-8')
                self.queue.task_done()

    def status(self):
        with closing(self.app.connect()) as c:
            tasks=[dict(r) for r in c.execute('SELECT * FROM news_tasks WHERE run_id=? ORDER BY created',(self.run_id,))]
            latest={t['source_row']:t['id'] for t in tasks}
            has_stages=bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='stages'").fetchone())
            for task in tasks:
                limits=c.execute('SELECT submitted_at,deadline_at FROM news_task_limits WHERE task_id=?',(task['id'],)).fetchone()
                if limits:
                    task.update(self._deadline_state(task,limits))
                    task.update(dict(limits))
                    task['elapsed_seconds']=max(0,(datetime.fromisoformat(now() if task['state'] in ('queued','running') else task['updated'])-datetime.fromisoformat(limits['submitted_at'])).total_seconds())
                    task['show_partial']=task['elapsed_seconds']>=60 and task['state']!='completed'
                    preview=c.execute("SELECT json_extract(body,'$.document') FROM news_input_records WHERE run_id=? AND source_row=?",(self.run_id,task['source_row'])).fetchone() if task['show_partial'] and latest[task['source_row']]==task['id'] else None
                    if preview:
                        document=json.loads(preview[0])
                        task['preview']=dict(document=document,summaries=[],complete=False)
                        jid=digest({k:document[k] for k in ('title','text','source','publication_time')})
                        extraction=c.execute("SELECT result FROM stages WHERE job=? AND stage='extract' AND status IN ('ready','cached','partial')",(jid,)).fetchone() if has_stages else None
                        if extraction:
                            result=json.loads(extraction[0] or '{}')
                            task['preview']['summaries']=[item['core']['summary'] for part in result.get('admission',{}).get('parts',[]) for item in part.get('items',[]) if item.get('core',{}).get('summary')]
                task['publications']=[dict(r) for r in c.execute('SELECT event_id,change_kind,published_at,event_version,material_version FROM news_task_publications WHERE task_id=?',(task['id'],))]
                task['event_ids']=[p['event_id'] for p in task['publications']]
            inputs=[dict(r) for r in c.execute("SELECT source_row AS id,json_extract(body,'$.document.title') AS title,json_extract(body,'$.received_at') AS received_at FROM news_input_records WHERE run_id=? ORDER BY received_at DESC",(self.run_id,))]
        calls={}
        if self.budget:
            with closing(self.budget.connect()) as ledger:
                calls={r['attempt']:dict(r) for r in ledger.execute('''SELECT a.attempt,count(*) AS used,
                    sum(n.status='reserved_unknown_no_retry') AS uncertain
                    FROM news_model_attempt_calls a JOIN news_model_calls n ON n.id=a.call_id
                    WHERE a.plan=? GROUP BY a.attempt''',(self.budget.plan_id,))}
        for task in tasks:
            count=calls.get(task['id'],{})
            task.update(model_calls_used=count.get('used',0),model_calls_uncertain=count.get('uncertain',0),
                        model_call_limit=2,analysis_complete=task['state']=='completed')
            active=False
            try:active=abs(psutil.Process(task['owner_pid']).create_time()-task['owner_birth'])<0.001
            except psutil.Error:pass
            if not active and task['state']=='running':task.update(state='unknown_no_retry',reason='处理进程已中断，发送状态待核对；不自动重发')
            elif not active and task['state']=='queued':task.update(state='not_sent',reason='排队时进程已退出；明确点击处理后才继续')
            task.pop('owner_pid');task.pop('owner_birth')
        phase=self.budget.phase_snapshot() if self.budget else None
        if phase:phase['committed_rmb']=sum(v['committed_rmb'] for v in phase['phases'].values())
        return dict(news_input_enabled=True,model_send_enabled=self.send_model,contract_revision=self.contract_revision,tasks=tasks,input_materials=inputs,budget_purpose=self.budget.purpose if self.budget else None,budget=phase or (self.budget.allocation_snapshot() if self.budget else None))
