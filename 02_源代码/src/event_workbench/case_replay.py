"""Replay reviewed material relationships through persistent rules and versions."""
import copy,json,uuid
from contextlib import closing
from types import SimpleNamespace
from event_workbench import training_corpus as corpus,competition_rules as rules,news_notifications
from event_workbench.product_replay import publication
from event_workbench.news_events import EventPublisher,SCHEMA as EVENT_SCHEMA

PATH=corpus.ROOT/'data/showcase-cases.json'
SCHEMA=EVENT_SCHEMA+'''
CREATE TABLE IF NOT EXISTS showcase_sessions(run_id TEXT PRIMARY KEY,case_id TEXT,step INTEGER,bundle_sha TEXT);
CREATE TABLE IF NOT EXISTS showcase_sources(source_row TEXT PRIMARY KEY,body TEXT);
'''

def bundle():
    data=PATH.read_bytes()
    import hashlib
    return json.loads(data),hashlib.sha256(data).hexdigest()

def describe(app,run):
    if not PATH.exists():return {}
    data,sha=bundle()
    result=dict(cases=[{k:c[k] for k in ('id','title','description')} for c in data['cases']])
    with closing(app.connect()) as c:
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='showcase_sessions'").fetchone():
            s=c.execute('SELECT * FROM showcase_sessions WHERE run_id=?',(run,)).fetchone()
            if s:
                case=next(x for x in data['cases'] if x['id']==s['case_id'])
                result['case_session']=dict(case_id=s['case_id'],step=s['step'],total=len(case['steps']),bundle_matches=s['bundle_sha']==sha)
    return result

def material(c,run,key,record,sha):
    doc=record['document'];file_sha='case:'+sha
    index=int(corpus.digest(key)[:12],16)
    header=dict(enumerate(sorted(corpus.CORE),1))
    values=dict(article_file_id='case:'+key,article_title=doc['title'],content=doc['text'],article_source=doc['source'],article_publish_time=doc['publication_time'])
    cells={i:dict(type='str',value=values[k],formula=False) for i,k in header.items()}
    c.execute('INSERT OR IGNORE INTO files VALUES(?,?)',(file_sha,'data/showcase-cases.json'))
    corpus.insert_row(c,file_sha,'case',index,header,cells,'1900')
    row=c.execute('SELECT * FROM source_rows WHERE file_sha=? AND row_number=?',(file_sha,index)).fetchone()
    available,precision=publication(dict(type='str',value=doc['publication_time'],formula=False),'1900')
    if not available:raise ValueError('案例发布时间无效')
    c.execute('INSERT OR IGNORE INTO replay_rows VALUES(?,?,?,?,?,?)',(row['id'],'case',available,precision,'allowed',len(doc['text'])))
    c.execute('INSERT OR IGNORE INTO replay_scope_rows VALUES(?,?,?)',(run,row['row_key'],corpus.js(dict(source_row=row['row_key'],available_at=available))))
    provenance=dict(source_check=record.get('source_check',{}),source_url=record.get('source_url') or record.get('source_check',{}).get('url'),received_at=corpus.now())
    c.execute('INSERT OR IGNORE INTO showcase_sources VALUES(?,?)',(row['row_key'],corpus.js(provenance)))
    return dict(source_row=row['row_key'],document_id=row['document_id'],title=doc['title'],source=doc['source'],available_at=available,publication_time=doc['publication_time'])

def proof(doc,quote):
    if not isinstance(quote,str) or not quote:raise ValueError('依据原句不能为空')
    for section,field in [('body','text'),('title','title')]:
        position=(doc.get(field) or '').find(quote)
        if position>=0:return dict(section=section,start=position,end=position+len(quote),quote=quote)
    raise ValueError('依据无法在原文定位')

def project(data,case,index,run,old,sources):
    item=case['steps'][index];key=item['material'];record=data['materials'][key];doc=record['document'];source=sources[key]
    repeat=item['relation']=='repeat'
    if repeat and old is None:raise ValueError('转载必须关联已有事件')
    eid='case-event:'+corpus.digest([run,item['event']])
    dto=copy.deepcopy(old) if repeat else dict(member_id=eid,job_id=source['source_row'],record_kind='event',case_id=case['id'],
        event_summary=dict(text=item['summary']),event_subjects=dict(participants=[]),
        monitoring_scope=dict(matches=[dict(decision='matched',definition=dict(definition_id=corpus.digest([o['code'],o['category']]),target_type='company',target_code=o['code'],target_name=o['name'],event_name=o['category'])) for o in item['objects']]),
        emotions=[dict(definition_id=corpus.digest([o['code'],o['category']]),label=item['emotion']) for o in item['objects']],
        credibility=dict(status='有材料支持，范围见逐项依据',claims=[]),warning_reasons=dict(decisions=[]),
        provenance=dict(kind='assistant_editorial_review',bundle_version=data['version'],financial_human_review='pending'))
    members=copy.deepcopy((old or {}).get('evidence_sources',{}).get('members',[]))
    if source['source_row'] not in {m['source_row'] for m in members}:members.append(source)
    if not repeat:
        claims=[]
        for number,p in enumerate(item['claims']):
            if p['material'] not in sources:raise ValueError('不允许引用尚未加入的材料')
            origin=data['materials'][p['material']];check=origin.get('source_check',{});s=sources[p['material']]
            q=dict(proof(origin['document'],p['quote']),document_id=s['document_id'])
            primary=check.get('source_kind')=='public_primary' and check.get('status') in ('original_page_checked','original_pdf_checked')
            score=rules.support_score(executed=True,evidence=[dict(judgment='supports',applicable=True,located=True,primary_verified=primary,independence_verified=False,origin_id=check.get('url') or s['document_id'])])
            claims.append(dict(claim_id='claim:'+corpus.digest([item['event'],p['text']]),claim=p['text'],verdict='有材料支持',support_score=score,
                basis=q,assessment_version=data['version'],valid_assessments=[dict(source_row=s['source_row'],source_relation='own_document',source_check=check,proof=q,proposal=dict(judgment='supports',reason='原文支持上述限定范围；不推及疗效、资金到账、收益或已投产。'))]))
        dto['credibility']['claims']=claims
        for e in item['effects']:
            basis=e.get('dimension_basis',{})
            if any(type(i) is not int or i<0 or i>=len(claims) for i in [*basis.values(),*e.get('claims',[])]):raise ValueError('影响引用的主张不存在')
            grade=rules.impact_score(e['dimensions'],supported_dimensions=set(basis),comparison_located=False)
            premises=[claims[i] for i in e.get('claims',[])]
            notice=rules.reminder(grade,[p['support_score'] for p in premises])
            level={'unknown':'待定','low':'低','medium':'中','high':'高'}[grade['level']]
            dto['warning_reasons']['decisions'].append(dict(target=e['target'],level=level,rule_version=rules.VERSION,rule_score=grade,
                alert=notice['kind'] is not None,route='important_pending' if notice['kind']=='check_notice' else 'event_notice' if notice['kind'] else 'observation',
                reasons=[notice['reason'],item['summary']],missing=e['gaps'],bound_claims=[p['claim_id'] for p in premises],
                effects=[dict(original=dict(e,target=dict(name=e['target']))) ]))
    if not repeat:
        from event_workbench.review_scoring import case_claim, event_score
        for claim,raw in zip(dto['credibility']['claims'],item['claims']):
            origin=data['materials'][raw['material']]
            previous=next((q for q in (old or {}).get('credibility',{}).get('claims',[]) if q['claim_id']==claim['claim_id']),None)
            if raw['material']!=item['material'] and previous:
                claim.update(copy.deepcopy(previous))
            else:claim.update(case_claim(origin['document'],raw,origin.get('source_check',{}),available_at=sources[raw['material']]['available_at']))
            claim['claim_role']='current_core' if raw['material']==item['material'] else 'historical'
        scored=dto['credibility']['claims']
        if scored:dto['credibility'].update(event_score(scored))
        dto['credibility']['assessed_at']=corpus.now()
    dto['assessment_version']=(old or {}).get('assessment_version',0)+(0 if repeat else 1)
    dto['material_count']=len(members)
    dto['progress_count']=(old or {}).get('progress_count',0)+(0 if repeat else 1)
    dto['available_at']=(old or {}).get('available_at',source['available_at'])
    dto['updated_at']=(old or {}).get('updated_at',source['publication_time']) if repeat else source['publication_time'];dto['change_summary']=item['summary']
    dto['event_version']=(old.get('event_version',0)+(0 if repeat else 1)) if old else 1
    dto['material_version']=(old or {}).get('material_version',0)+1
    dto['evidence_sources']=dict(original=copy.deepcopy((old or {}).get('evidence_sources',{}).get('original',doc) if repeat else doc),members=members,source_rows=[m['source_row'] for m in members],original_quotes=[])
    timeline=copy.deepcopy((old or {}).get('event_timeline',{}).get('items',[]))
    timeline.append(dict(time=doc['publication_time'],kind={'initial':'首次披露','repeat':'重复报道','separate':'另一事项','progress':'实际进展','clarification':'澄清更正'}.get(item['relation'],'待判断'),summary=item['summary'],source_row=source['source_row']))
    dto['event_timeline']=dict(items=timeline)
    edges=copy.deepcopy((old or {}).get('source_relations',{}).get('edges',[]))
    if old:
        edges.append(dict(material_a=source['source_row'],material_b=members[0]['source_row'],relation='同源转载' if repeat else '同一事项的后续材料',reason=item['summary'],independent_confirmation=False))
    dto['source_relations']=dict(nodes=members,edges=edges,note='关联材料与独立证据分别判断；同一发布者的后续材料不增加独立来源数。')
    return dto

def publish_step(c, app, data, case, index, run, sources):
    item=case['steps'][index];eid='case-event:'+corpus.digest([run,item['event']])
    existing=c.execute('SELECT body FROM replay_events WHERE id=?',(eid,)).fetchone()
    old=json.loads(existing[0]) if existing else None
    dto=project(data,case,index,run,old,sources)
    from event_workbench.dossier_review import review_case
    review_case(c,app,data,case,index,run,dto)
    from event_workbench.alert_review import review_case as review_impact_case
    review_impact_case(c,app,data,case,index,run,dto)
    publisher=EventPublisher(app,SimpleNamespace(run_id=run))
    dto['warning_reasons']['decisions']=publisher._reminders(c,dto,item['relation']!='repeat')
    news_notifications.record(c,run,dto)
    body=corpus.js(dto)
    c.execute('INSERT INTO news_event_versions VALUES(?,?,?,?,?,?,?)',(run,eid,dto['event_version'],dto['material_version'],corpus.digest(dto),body,corpus.now()))
    c.execute('INSERT OR REPLACE INTO replay_events VALUES(?,?,?,?,?)',(eid,run,dto['job_id'],dto['available_at'],body))
    return eid


def include_reviewed_cases(app):
    """Expose the already reviewed materials in the main radar, once per bundle."""
    data,sha=bundle();run='training-showcase'
    with closing(app.connect()) as c:
        c.executescript(SCHEMA)
        with c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute("SELECT value FROM meta WHERE key='radar_case_bundle'").fetchone()
            if prior:
                if json.loads(prior[0])['sha']!=sha:raise ValueError('已加入材料版本发生变化，需另行审阅迁移')
                return dict(added=False,materials=7,events=4)
            count=0
            for case in data['cases']:
                sources={}
                for index,item in enumerate(case['steps']):
                    key=item['material'];sources[key]=material(c,run,key,data['materials'][key],sha)
                    publish_step(c,app,data,case,index,run,sources);count+=1
            c.execute('INSERT INTO meta VALUES(?,?)',('radar_case_bundle',corpus.js(dict(sha=sha,materials=count,events=4))))
            c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run,))
    return dict(added=True,materials=count,events=4)


def change(app,payload):
    if not isinstance(payload,dict) or type(payload.get('revision')) is not int:raise ValueError('案例操作缺少版本')
    op=payload.get('operation');previous_run=payload.get('run')
    if op not in ('start','next','reset') or not isinstance(previous_run,str):raise ValueError('案例操作无效')
    data,sha=bundle()
    with closing(app.connect()) as c:
        if not corpus.metadata(c).get('showcase_version'):raise ValueError('本工作区没有案例回放')
        c.executescript(SCHEMA)
        session=c.execute('SELECT * FROM showcase_sessions WHERE run_id=?',(previous_run,)).fetchone()
        if op!='start' and not session:raise ValueError('案例尚未初始化')
        case_id=payload.get('case_id') if op=='start' else session['case_id']
        case=next((x for x in data['cases'] if x['id']==case_id),None)
        if case is None:raise ValueError('不存在该案例')
        if session and session['bundle_sha']!=sha:raise ValueError('案例资料版本已变化，旧会话保持只读')
        run=previous_run if op=='next' else 'case-'+uuid.uuid4().hex
        with c:
            c.execute('BEGIN IMMEDIATE')
            current=app.run(c,previous_run)
            if current['revision']!=payload['revision']:raise ValueError('工作区已变化，请刷新后再操作')
            if op=='next':
                session=c.execute('SELECT * FROM showcase_sessions WHERE run_id=?',(run,)).fetchone();index=session['step']
                if index>=len(case['steps']):return dict(run=run,complete=True)
            else:
                index=0;c.execute('INSERT INTO replay_runs(id,title,cutoff) VALUES(?,?,?)',(run,case['title'],'9999-01-01T00:00:00+00:00'))
                c.execute('INSERT INTO showcase_sessions VALUES(?,?,?,?)',(run,case_id,0,sha))
            sources={}
            for item in case['steps'][:index+1]:
                sources[item['material']]=material(c,run,item['material'],data['materials'][item['material']],sha)
            eid=publish_step(c,app,data,case,index,run,sources)
            c.execute('UPDATE showcase_sessions SET step=? WHERE run_id=?',(index+1,run))
            c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run,))
    return dict(run=run,step=index+1,event_id=eid)
