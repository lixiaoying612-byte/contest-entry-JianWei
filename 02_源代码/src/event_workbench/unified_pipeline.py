"""Small persistent stage orchestrator; legacy algorithms remain separate adapters."""
import copy
import json
import sqlite3
import threading
from contextlib import contextmanager
from pathlib import Path
from event_workbench import document_pipeline as documents
from event_workbench import incremental_relations as relations
from event_workbench import impact_watch

digest=documents.digest
STAGES=('extract','mentions','retrieval','verification','relation_candidates','relations','impact')
DEPS={'extract':[],'mentions':['extract'],'retrieval':['mentions'],'verification':['mentions','retrieval'],'relation_candidates':['mentions'],'relations':['mentions','relation_candidates'],'impact':['mentions','verification']}
SIGNALS={'extract':['extraction_request'],'mentions':['mention_rule'],'retrieval':['evidence_corpus'],'verification':['verification_rule','evidence_corpus'],'relation_candidates':['history'],'relations':['relation_rule','history'],'impact':['impact_rule']}
GOOD={'ready','cached','partial'}


def semantic(value):
    if isinstance(value,dict):return {k:semantic(v) for k,v in value.items() if k not in ('created','started','at','assessed_at','imported_at','check_version','review_version','current_version','log_version','ui_version')}
    if isinstance(value,list):return [semantic(v) for v in value]
    return value


def source_aware_impact(job,document_catalog):
    """Build an unsent impact premise package; never infer source from equal text."""
    units=[];lookup={};claims=[];unmapped=[]
    def locate(proof):
        # Document ID and document fingerprint must both identify the same version.
        doc=document_catalog.get(proof.get('document_id'))
        if not doc:return None
        try:impact_watch.verify_proof(proof,document_catalog)
        except (ValueError,KeyError,TypeError):return None
        key=(proof['document_id'],proof['document_sha256'],proof['section'],proof['start'],proof['end'],proof['quote_sha256'])
        if key not in lookup:
            uid='S'+str(len(units));lookup[key]=uid;units.append(dict(id=uid,proof=copy.deepcopy(proof),source_family=doc.get('source_family'),source_dependency=doc.get('source_dependency','unknown')))
        return lookup[key]
    checked=job['verification']['checked']
    for c in checked.get('valid',[]):
        row=dict(mention=c['original_mention_index'],claim=c['claim'],status='checkable_not_verified',basis=None,opinions=[],insufficient_without_quote=0,isolated_opinions=len(c['quarantined_assessments']))
        row['basis']=locate(c['basis'])
        if row['basis'] is None:unmapped.append(dict(mention=row['mention'],kind='basis',original=c['basis']))
        for a in c['valid_assessments']:
            if not a.get('proof'):row['insufficient_without_quote']+=1;continue
            uid=locate(a['proof']);row['opinions'].append(dict(judgment=a['proposal']['judgment'],source_relation=a['source_relation'],unit=uid,status='mapped' if uid else 'unmapped_pending'))
            if uid is None:unmapped.append(dict(mention=row['mention'],kind='opinion',original=a['proof']))
        claims.append(row)
    for c in checked.get('quarantined',[]):claims.append(dict(mention=c.get('original',{}).get('mention'),status='isolated_not_processed',original=copy.deepcopy(c)))
    return dict(mentions=copy.deepcopy(job['preview']['events']),units=units,claims=claims,coverage=copy.deepcopy(checked.get('coverage')),unmapped=unmapped,independent_verification='not_established',generated_model_request=False,contract='eng023-source-aware-unsent')


class Pipeline:
    validate_document = staticmethod(documents.validate)
    def __init__(self,path,adapters=None,plan=None):
        self.path=Path(path);self.path.parent.mkdir(parents=True,exist_ok=True);self.adapters=adapters or {};self.plan=plan or dict(id='ENG023-zero',max_new_calls=0,allowed_stages=[]);self.lock=threading.RLock();self.threads=[]
        with self.connect() as c:
            c.executescript('CREATE TABLE IF NOT EXISTS jobs(id TEXT PRIMARY KEY,document TEXT,created TEXT); CREATE TABLE IF NOT EXISTS stages(job TEXT,stage TEXT,status TEXT,reason TEXT,input_sha TEXT,semantic_sha TEXT,result TEXT,provenance TEXT,PRIMARY KEY(job,stage)); CREATE TABLE IF NOT EXISTS signals(name TEXT PRIMARY KEY,body TEXT); CREATE TABLE IF NOT EXISTS attempts(id TEXT PRIMARY KEY,job TEXT,stage TEXT,plan TEXT,cost INTEGER,status TEXT,request TEXT); CREATE TABLE IF NOT EXISTS imports(id TEXT PRIMARY KEY,sha TEXT,body TEXT); CREATE TABLE IF NOT EXISTS changes(seq INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,stage TEXT,body TEXT); CREATE TABLE IF NOT EXISTS withdrawals(id TEXT PRIMARY KEY,reason TEXT);')
            for r in c.execute("SELECT * FROM stages WHERE status='running'").fetchall():self._write(c,r['job'],r['stage'],'unknown_no_retry','恢复发现未完成执行，不自动重试',r['input_sha'])
    @contextmanager
    def connect(self):
        from event_workbench.news_deadline import bind
        c=bind(sqlite3.connect(self.path,timeout=30),30000);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()
    def _states(self,c,jid):return {r['stage']:dict(r,result=json.loads(r['result']) if r['result'] else None,provenance=json.loads(r['provenance']) if r['provenance'] else None) for r in c.execute('SELECT * FROM stages WHERE job=?',(jid,))}
    def _signature(self,c,jid,stage,states):
        signals={r['name']:json.loads(r['body']) for r in c.execute('SELECT * FROM signals')}
        return digest(dict(document=jid,dependencies={d:states[d]['semantic_sha'] for d in DEPS[stage]},signals={k:semantic(signals.get(k)) for k in SIGNALS[stage]}))
    def _write(self,c,jid,stage,status,reason,input_sha,result=None,provenance=None):
        old=c.execute('SELECT * FROM stages WHERE job=? AND stage=?',(jid,stage)).fetchone();sem=digest(semantic(result)) if result is not None else None
        new=(status,reason,input_sha,sem,json.dumps(result,ensure_ascii=False) if result is not None else None,json.dumps(provenance,ensure_ascii=False) if provenance else None)
        if old and tuple(old[k] for k in ('status','reason','input_sha','semantic_sha','result','provenance'))==new:return
        c.execute('INSERT OR REPLACE INTO stages VALUES(?,?,?,?,?,?,?,?)',(jid,stage,*new))
        if not old or old['status']!=status or old['semantic_sha']!=sem:c.execute('INSERT INTO changes(job,stage,body) VALUES(?,?,?)',(jid,stage,json.dumps(dict(previous=old['status'] if old else None,current=status,reason=reason,semantic_sha=sem,external_notification=False))))
    def submit(self,value,background=True):
        if value.get('action')=='withdraw':return self.withdraw(value)
        doc=self.validate_document(value);jid=digest(doc)
        with self.lock,self.connect() as c:
            c.execute('BEGIN IMMEDIATE');exists=c.execute('SELECT id FROM jobs WHERE id=?',(jid,)).fetchone()
            if not exists:
                c.execute('INSERT INTO jobs VALUES(?,?,?)',(jid,json.dumps(doc,ensure_ascii=False),documents.now()))
                for stage in STAGES:self._write(c,jid,stage,'waiting','已保存，模型阶段待运行',None)
        if background:
            thread=threading.Thread(target=self.run,args=(jid,),daemon=True);self.threads.append(thread);thread.start()
        else:self.run(jid)
        return dict(id=jid,reused=bool(exists),status='saved_stage_plan_available',new_model_budget=self.plan['max_new_calls'])
    def import_legacy(self,bundle,cache_only=False):
        doc=self.validate_document(bundle['document']);jid=digest(doc);key=bundle['legacy_identity'];sha=digest(bundle)
        with self.lock,self.connect() as c:
            c.execute('BEGIN IMMEDIATE');old=c.execute('SELECT sha FROM imports WHERE id=?',(key,)).fetchone()
            if old:
                if old[0]!=sha:raise ValueError('legacy_identity_changed')
                return jid
            existing=c.execute('SELECT document FROM jobs WHERE id=?',(jid,)).fetchone()
            if existing and json.loads(existing[0])!=doc:raise ValueError('document_hash_collision')
            c.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?)',(jid,json.dumps(doc,ensure_ascii=False),documents.now()))
            for stage in STAGES:
                row=bundle['stages'][stage];states=self._states(c,jid)
                sig=self._signature(c,jid,stage,states)
                self._write(c,jid,stage,'waiting' if cache_only else row['status'],'原答已登记，待同一阶段适配器回放' if cache_only else row['reason'],None if cache_only else sig,None if cache_only else row.get('result'),dict(adapter='explicit_legacy',identity=key,original_request_identities=bundle['provenance'],stage_provenance=row.get('provenance')))
            c.execute('INSERT INTO imports VALUES(?,?,?)',(key,sha,json.dumps(bundle,ensure_ascii=False)))
        return jid
    def update_signal(self,name,value):
        with self.lock,self.connect() as c:
            old=c.execute('SELECT body FROM signals WHERE name=?',(name,)).fetchone()
            if old and semantic(json.loads(old[0]))==semantic(value):return False
            c.execute('INSERT OR REPLACE INTO signals VALUES(?,?)',(name,json.dumps(value,ensure_ascii=False)))
            for job in c.execute('SELECT id FROM jobs').fetchall():
                affected=set()
                for stage in STAGES:
                    if name in SIGNALS[stage] or affected.intersection(DEPS[stage]):
                        affected.add(stage);r=self._states(c,job['id'])[stage]
                        if r['status'] in GOOD|{'not_applicable'}:self._write(c,job['id'],stage,'stale','阶段依赖或规则语义变化',r['input_sha'],r['result'],r['provenance'])
        return True
    def update_result(self,jid,stage,result):
        with self.lock,self.connect() as c:
            states=self._states(c,jid);old=states[stage];changed=old['semantic_sha']!=digest(semantic(result))
            self._write(c,jid,stage,'ready','本地派生当前结果',self._signature(c,jid,stage,states),result,old['provenance'])
            if changed:
                affected={stage}
                for child in STAGES:
                    if child!=stage and affected.intersection(DEPS[child]):
                        affected.add(child);r=states[child]
                        if r['status'] in GOOD|{'not_applicable'}:self._write(c,jid,child,'stale','上游业务含义变化',r['input_sha'],r['result'],r['provenance'])
    def run(self,jid):raise RuntimeError('legacy_runner_removed_use_unified_execution')
    def withdraw(self,value):
        if not isinstance(value.get('reason'),str) or not value['reason'].strip():raise ValueError('撤回需说明原因')
        with self.lock,self.connect() as c:
            if value.get('relation_id'):
                rid=value['relation_id'];found=False
                for row in c.execute("SELECT result FROM stages WHERE stage='relations'"):
                    found|=any(p['id']==rid for p in (json.loads(row[0]) if row[0] else {}).get('proposals',[]))
                if not found:raise ValueError('未知关系')
                existed=c.execute('SELECT id FROM withdrawals WHERE id=?',(rid,)).fetchone();c.execute('INSERT OR IGNORE INTO withdrawals VALUES(?,?)',(rid,value['reason']))
                if not existed:
                    for r in c.execute("SELECT job,result FROM stages WHERE stage='relations'").fetchall():
                        if any(p['id']==rid for p in (json.loads(r['result']) if r['result'] else {}).get('proposals',[])):c.execute('INSERT INTO changes(job,stage,body) VALUES(?,?,?)',(r['job'],'relations',json.dumps(dict(current='relation_withdrawn',id=rid,reason=value['reason']))))
            else:
                jid=value['job'];stage=value['stage']
                if stage not in ('verification','relations','impact'):raise ValueError('stage_not_withdrawable')
                states=self._states(c,jid);r=states[stage];self._write(c,jid,stage,'withdrawn',value['reason'],r['input_sha'],r['result'],r['provenance'])
                if stage=='relations':
                    for p in (r['result'] or {}).get('proposals',[]):c.execute('INSERT OR IGNORE INTO withdrawals VALUES(?,?)',(p['id'],value['reason']))
                if stage=='verification':
                    r=states['impact'];self._write(c,jid,'impact','stale','核查撤回，影响失效',r['input_sha'],r['result'],r['provenance'])
        return dict(status='withdrawn')
    def counts(self):
        with self.connect() as c:n=c.execute('SELECT coalesce(sum(cost),0) FROM attempts').fetchone()[0]
        return dict(new_model_attempts=n,scope='ENG023 only',imported_history=dict(eng019_020_022=dict(extraction=2,verification=2,impact=2),eng012_021=dict(extraction=4,legacy_relation=1,incremental_relation=2)),project_total='not_asserted')
    def list(self):
        with self.connect() as c:return [dict(id=r['id'],document=json.loads(r['document']),created=r['created'],status='已保存，查看阶段状态') for r in c.execute('SELECT * FROM jobs ORDER BY rowid')]
    def get(self,jid):
        with self.connect() as c:
            row=c.execute('SELECT * FROM jobs WHERE id=?',(jid,)).fetchone()
            if not row:raise KeyError(jid)
            states=self._states(c,jid);withdrawn={r[0] for r in c.execute('SELECT id FROM withdrawals')};all_mentions={};all_proposals={}
            for r in c.execute("SELECT result FROM stages WHERE stage='mentions' AND status IN ('ready','cached','partial')"):
                for m in (json.loads(r[0]) if r[0] else {}).get('mentions',[]):all_mentions[m['id']]=m
            for r in c.execute("SELECT result FROM stages WHERE stage='relations' AND status IN ('ready','cached','partial')"):
                for p in (json.loads(r[0]) if r[0] else {}).get('proposals',[]):
                    if p['id'] not in withdrawn:all_proposals[p['id']]=p
            graph=relations.current_graph(list(all_mentions),list(all_proposals.values()))
            own={m['id'] for m in (states['mentions']['result'] or {}).get('mentions',[])}
            graph['proposals']=[p for p in graph['proposals'] if own.intersection(p['member_ids'])];graph['groups']=[g for g in graph['groups'] if own.intersection(g['members'])]
            return dict(id=jid,document=json.loads(row['document']),stages=states,current_relations=graph,mention_catalog=all_mentions,withdrawals=list(withdrawn),changes=[dict(r) for r in c.execute('SELECT stage,body FROM changes WHERE job=? ORDER BY seq',(jid,))],counts=self.counts())
