"""Validate impact answers and append assessments/reminders to the existing workbench."""
import copy
import json
import re
from contextlib import closing
from datetime import date
from event_workbench import alert_rules as rules,training_corpus as corpus,dossier_review,product_research,review_scoring,news_notifications
from event_workbench.news_materials import moment

SCHEMA='''CREATE TABLE IF NOT EXISTS impact_reviews(
 id TEXT PRIMARY KEY,run_id TEXT,event_id TEXT,status TEXT,body TEXT,projection TEXT,error TEXT,created TEXT)'''

def input_for_review(app,run_id,event_id,*,include_draft=False):
    """No retrieval, network or model call: only this saved event's permitted material."""
    with closing(app.connect()) as c:
        run=app.run(c,run_id)
        row=next((r for r in app.visible_event_rows(c,run) if r['id']==event_id),None)
        if row is None:raise ValueError('impact_event_not_visible')
        e=json.loads(row['body'])
        if include_draft and c.execute("SELECT 1 FROM sqlite_master WHERE name='news_event_drafts'").fetchone():
            draft=c.execute('SELECT base_body_sha,body FROM news_event_drafts WHERE run_id=? AND event_id=?',(run_id,event_id)).fetchone()
            if draft:
                if draft['base_body_sha']!=corpus.digest(e):raise ValueError('impact_draft_head_changed')
                e=json.loads(draft['body'])
        docs=dossier_review.documents(c,run,e['evidence_sources']['source_rows'])
        for d in docs.values():d['document_sha']=corpus.digest({k:d[k] for k in review_scoring.FIELDS})
        sent=[json.loads(r[0]) for r in c.execute('SELECT body FROM news_notifications WHERE run_id=? AND event_id=?',(run_id,event_id))]
        return dict(schema_version=rules.CONTRACT,rule_version=rules.VERSION,event_id=event_id,event_version=e['event_version'],base_body_sha=corpus.digest(e),source_rows=list(docs),cutoff=corpus.now(),notified_history=sent,
            event_context=dict({k:copy.deepcopy(e[k]) for k in ('event_identity','event_summary','event_subjects','source_status') if k in e},identity_resolutions=[copy.deepcopy(o['identity_resolution']) for o in e.get('monitoring_scope',{}).get('business_objects',[]) if o.get('identity_resolution')]),
            allowed_objects=[dict(o,classification_basis=classification_basis(e,o)) for o in objects(e).values()],documents=docs,event_document_ids=sorted({m['document_id'] for m in e.get('evidence_sources',{}).get('members',[]) if m.get('document_id')}),claims=e.get('credibility',{}).get('claims',[]),previous_decisions=e.get('warning_reasons',{}).get('decisions',[]),
            families=rules.catalog()['families'],anchors=rules.catalog()['anchors'],priority_conditions={k:sorted(v['conditions']) for k,v in rules.HARD.items()},instructions=(corpus.ROOT/'engineering/contracts/impact-v3-prompt.txt').read_text('utf-8'))

def review_case(c,app,data,case,index,run,event):
    """Replay saved answers through the same gate, restricted to material already added."""
    if case['steps'][index]['relation']=='repeat':return
    saved=json.loads((corpus.ROOT/'data/showcase-impact-v3.json').read_text('utf-8'))
    template=next(t for t in saved['items'] if t['case_id']==case['id'] and t['step']==index)
    runrow=app.run(c,run);docs=dossier_review.documents(c,runrow,event['evidence_sources']['source_rows'])
    by_hash={corpus.digest({k:d[k] for k in review_scoring.FIELDS}):row for row,d in docs.items()}
    available=objects(event);claims=event['credibility']['claims'];answers=[]
    for t in template['assessments']:
        obj=next(o for o in available.values() if o['name']==t['target'])
        bound=[next(q['claim_id'] for q in claims if q['claim']==text and q.get('claim_role')!='historical') for text in t['claim_texts']]
        quotes=[]
        for raw in t['quotes']:
            if raw['document_sha']!=template['material_hashes'][raw['material']]:raise ValueError('case_impact_material_changed')
            if raw['document_sha'] not in by_hash:raise ValueError('case_impact_future_material')
            quotes.append(dict(source_row=by_hash[raw['document_sha']],**{k:raw[k] for k in ('document_sha','quote','section')}))
        answers.append(dict(object_id=obj['id'],bound_claim_ids=bound,quotes=quotes,features=t['features'],change=t['change'],classification='supplementary',classification_reason='有限案例逐事项审阅，不新增官方分类',fact_time=event['updated_at'],fact_summary=event['event_summary']['text']))
    record=dict(schema_version=rules.CONTRACT,rule_version=rules.VERSION,event_id=event['member_id'],event_version=event['event_version'],source_rows=list(docs),base_body_sha=corpus.digest(event),cutoff=corpus.now(),reviewed_at=corpus.now(),reason='有限案例原始答复经过共同校验入口',assessments=answers)
    projection=project(event,record,docs,origin='codex_session');identity='impact:'+corpus.digest([run,record])
    c.execute(SCHEMA)
    c.execute('INSERT INTO impact_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run,event['member_id'],'accepted',corpus.js(dict(record,origin='codex_session',cause='new_material')),corpus.js(projection),'',record['reviewed_at']))
    overlay(event,projection,identity)

def objects(event):
    values={o['id']:o for o in product_research.objects(event)}
    if not event.get('case_id'):return values
    names={o['name'] for o in values.values()}
    # Existing project-level case judgments stay explicit; a model cannot invent new objects.
    for d in event.get('warning_reasons',{}).get('decisions',[]):
        if d.get('target') and d['target'] not in names:
            key='existing-scope:'+corpus.digest([event['member_id'],d['target']])[:24]
            values[key]=dict(id=key,name=d['target'],type='project',code=None);names.add(d['target'])
    return values

def classification_basis(event,obj):
    """The same saved event match determines both model context and admission."""
    return [m for m in rules.catalog()['definitions'] if m['code']==obj['code'] and any(d.get('decision') in ('matched','m')
        and str(d.get('definition',{}).get('target_code'))==str(obj['code'])
        and (d.get('definition',{}).get('target_type')=='industry')==(obj['type']=='industry')
        and d.get('definition',{}).get('event_name')==m['event_name'] for d in event.get('monitoring_scope',{}).get('matches',[]))]


def locate(raw,documents,cutoff):
    source=raw['source_row']
    if source not in documents:raise ValueError('impact_quote_outside_event')
    doc=documents[source];given={k:doc[k] for k in review_scoring.FIELDS}
    if raw.get('document_sha')!=corpus.digest(given):raise ValueError('impact_document_hash_mismatch')
    if moment(doc['available_at'])>moment(cutoff) or doc.get('publication_time') and review_scoring.publication_moment(doc['publication_time'])>moment(cutoff):raise ValueError('impact_material_after_cutoff')
    field={'body':'text','title':'title'}.get(raw.get('section'))
    quote=raw.get('quote');text=doc.get(field,'') if field else ''
    if not isinstance(quote,str) or not quote:raise ValueError('impact_quote_required')
    start=raw.get('start',text.find(quote))
    if type(start)!=int or start<0 or text[start:start+len(quote)]!=quote:raise ValueError('impact_quote_not_located')
    return dict(raw,start=start,end=start+len(quote),document_id=doc['document_id'],available_at=doc['available_at'],publication_time=doc.get('publication_time'),source=doc.get('source'))

def current_claim_ids(claims):
    return {q['claim_id'] for q in claims if q.get('claim_id') and q.get('score_role') not in ('历史阶段','已被更正') and q.get('claim_role')!='historical' and not q.get('superseded_by')}


def validate_bound_premises(features,proofs,bound,*,retain_unbound=False):
    """A scored premise needs a bound claim at the actual quoted location."""
    sources=[]
    for claim in bound:
        if claim.get('claim_id') not in current_claim_ids([claim]):raise ValueError('impact_current_claim_binding_invalid')
        sources.append(claim.get('basis',{}))
        sources.extend(a['proof'] for a in claim.get('valid_assessments',[])
                       if a.get('proof') and a.get('proposal',{}).get('judgment') in ('supports','refutes'))
        sources.extend(r['proof'] for r in claim.get('score_input',{}).get('records',[])
                       if r.get('proof') and r.get('applicable') and r.get('time_eligible') and r.get('relation') in ('supports','reported','refutes'))
    ids=lambda x:{x[k] for k in ('document_id','document_sha','document_sha256') if x.get(k)}
    def same(p,s):
        return bool(ids(p)&ids(s)) and p.get('section')==s.get('section') and p.get('quote')==s.get('quote') and all(p.get(k)==s[k] for k in ('start','end') if s.get(k) is not None)
    for key,dimension in features.get('dimensions',{}).items():
        body=[proofs[i] for i in dimension['refs'] if proofs[i].get('section')=='body']
        if not body or any(not any(same(premise,s) for s in sources) for premise in body):
            refs=[i for i,p in enumerate(proofs) if p.get('section')=='body' and any(same(p,s) for s in sources)]
            if not retain_unbound or not refs:raise ValueError('impact_scoring_premise_not_bound:'+key)
            anchors=rules.catalog()['anchors'][key]
            dimension.clear()
            dimension.update(lower=min(anchors),upper=max(anchors),refs=refs,
                reason='当前主张不足以支持该维度的具体档位',gap='原评分引用未绑定当前主张，具体评分待定')
            if key=='M':features.pop('measurement',None)
            continue
        for index in dimension['refs']:
            if proofs[index].get('section')=='title' and not any(ids(proofs[index])&ids(p) for p in body):
                raise ValueError('impact_auxiliary_title_not_from_bound_body_document:'+key)


def project(event,record,documents,*,origin,calendar=None,notified_history=()):
    if record.get('schema_version')!=rules.CONTRACT or record.get('rule_version')!=rules.VERSION:raise ValueError('impact_review_contract_mismatch')
    if not record.get('reason') or moment(record['cutoff'])>moment(record['reviewed_at']):raise ValueError('impact_review_time_or_reason_invalid')
    if moment(record['cutoff'])>moment(corpus.now()):raise ValueError('impact_cutoff_is_in_the_future')
    available=objects(event);claims={q['claim_id']:q for q in event.get('credibility',{}).get('claims',[])}
    output=[];seen=set()
    for raw in record['assessments']:
        obj=available.get(raw.get('object_id'))
        if not obj or obj['id'] in seen:raise ValueError('impact_object_outside_event_or_duplicate')
        seen.add(obj['id']);features=copy.deepcopy(raw['features'])
        # Older prompt said "give rule_coverage" without specifying its parent.
        # Lift only this known shape; never invent missing reasons or evidence.
        if 'rule_coverage' in raw:
            if 'rule_coverage' in features and features['rule_coverage']!=raw['rule_coverage']:raise ValueError('impact_rule_coverage_conflict')
            features['rule_coverage']=copy.deepcopy(raw['rule_coverage'])
        proofs=[locate(p,documents,record['cutoff']) for p in raw['quotes']]
        ids=raw['bound_claim_ids']
        if not ids or len(set(ids))!=len(ids) or not set(ids)<=current_claim_ids(claims.values()):raise ValueError('impact_current_claim_binding_invalid')
        bound=[claims[cid] for cid in ids]
        quoted_docs={p['document_id'] for p in proofs}|{p['document_sha'] for p in proofs}
        if not all(q.get('basis',{}).get('document_id') in quoted_docs for q in bound):raise ValueError('impact_premise_quotes_missing')
        rules.validate(features,len(proofs),calendar)
        validate_bound_premises(features,proofs,bound)
        for value in [*features.get('dimensions',{}).values(),features['object_relation'],raw['change']]:
            review_scoring.check_source_text(value.get('reason',''),''.join(proofs[i]['quote'] for i in value['refs']))
        context=''.join(p['quote'] for p in proofs)
        review_scoring.check_source_text(json.dumps({k:v for k,v in features.items() if k not in ('dimensions','object_relation')},ensure_ascii=False),context)
        review_scoring.check_source_text(raw.get('fact_summary',''),context)
        mapped=classification_basis(event,obj)
        official=any(features['family'] in m['rule_families'] for m in mapped)
        if not official and (raw.get('classification')!='supplementary' or not raw.get('classification_reason')):raise ValueError('supplementary_rule_scope_requires_reason')
        evidence=rules.evidence_state(bound,record['cutoff'])
        if features['object_relation']['kind'] in ('none','uncertain'):evidence=dict(evidence,level=0,label='E0 · 对象关系待核')
        assessed=rules.calculate(features,evidence)
        change=raw['change']
        if not change.get('reason'):raise ValueError('impact_change_reason_required')
        rules.refs(change,len(proofs))
        prior=next((d for d in event.get('warning_reasons',{}).get('decisions',[]) if d.get('target')==obj['name']),{})
        fact_time=raw.get('fact_time')
        bound_docs={q.get('basis',{}).get('document_id') for q in bound}
        premise_proofs=[p for p in proofs if p['document_id'] in bound_docs or p['document_sha'] in bound_docs]
        disclosed=[p['publication_time'] for p in premise_proofs if p.get('publication_time')]
        # An explicit quoted disclosure date may fill missing metadata. A month/day,
        # occurrence date or planned publication is not evidence of a disclosure date.
        for p in premise_proofs:
            for match in re.finditer(r'(20\d{2})[年/-](\d{1,2})[月/-](\d{1,2})日?',p['quote']):
                context=p['quote'][max(0,match.start()-8):match.end()+12]
                if not re.search(r'公告|披露|刊发|报道',context) or re.search(r'拟|计划|将|预计|预期',context):continue
                try:disclosed.append(date(*map(int,match.groups())).isoformat())
                except ValueError:continue
        if fact_time is None:
            if disclosed:raise ValueError('impact_fact_time_missing_despite_dated_premise')
            assessed['missing']=list(dict.fromkeys(assessed['missing']+['本事项披露日期未提供；收录时间不能作为发生或披露时间']))
        elif not fact_time or review_scoring.publication_moment(fact_time)>moment(record['cutoff']):raise ValueError('impact_fact_time_invalid')
        if fact_time:
            given=review_scoring.publication_moment(fact_time)
            if not any(given.date()==review_scoring.publication_moment(value).date() and
                (len(fact_time)==10 or re.search(r'[T ]\d{1,2}:\d{2}',value) and given==review_scoring.publication_moment(value)) for value in disclosed):
                raise ValueError('impact_fact_time_has_no_disclosure_basis')
        old_statements=[b.get('claim') for b in prior.get('bound_claims',[]) if isinstance(b,dict)]+[prior.get('fact_summary')]
        for notice in notified_history:
            if notice.get('object_id')==obj['id'] or notice.get('target')==obj['name']:
                old_statements+=[b.get('claim') for b in notice.get('bound_claims',[]) if isinstance(b,dict)]+[notice.get('fact_summary')]
        if change.get('kind')=='correction' and change.get('old_statement') not in old_statements:raise ValueError('correction_old_claim_not_bound_to_previous_reminder')
        prefix='据报道，' if evidence['level']==1 else '待核线索：' if evidence['level']==0 else ''
        fact_summary=raw.get('fact_summary')
        if not fact_summary:raise ValueError('impact_fact_summary_required')
        if change.get('kind')=='correction' and change.get('new_statement') not in [q['claim'] for q in bound]+[fact_summary]:raise ValueError('correction_new_claim_not_bound')
        reason=[prefix+fact_summary,features['pathway']]
        effects=copy.deepcopy(prior.get('effects',[])) or [dict(original=dict(target=dict(name=obj['name']),conditions=[],horizon='unspecified'))]
        for effect in effects:
            effect['original'].update(direction=features['direction'],pathway=features['pathway'],gaps=assessed['missing'])
            effect['proofs']=copy.deepcopy(proofs)
        decision=dict(prior,rule_version=rules.VERSION,review_type=origin,confirmed=False,target=obj['name'],targets=[obj['name']],object_id=obj['id'],
            rule_score=assessed,level=assessed['level'],priority=assessed['priority'],route=assessed['route'],alert=assessed['visible'],
            reasons=reason,missing=assessed['missing'],effects=effects,bound_claims=[dict(claim_id=q['claim_id'],claim=q['claim'],basis=copy.deepcopy(q['basis'])) for q in bound],
            fact_summary=fact_summary,fact_time=fact_time,change_judgment=copy.deepcopy(change),next_observation=assessed['next_observation'],
            fact_time_status='disclosure_provided' if fact_time else 'disclosure_unknown',
            impact_features=copy.deepcopy(features),impact_proofs=proofs,official_definition_ids=[m['definition_id'] for m in mapped if features['family'] in m['rule_families']],classification='official' if official else 'supplementary',
            impact_assessed_at=record['reviewed_at'],impact_cutoff=record['cutoff'],impact_event_version=event['event_version'])
        if 'rule_coverage' in raw:decision['answer_normalization']=['assessment.rule_coverage -> assessment.features.rule_coverage']
        if type(prior.get('version')) is int:decision['legacy_notice_id']=prior['id']
        for key in ('version','active','change','delivery','notification_state','stale_assessment'):decision.pop(key,None)
        output.append(decision)
    if not output:raise ValueError('impact_assessments_empty')
    return output

def preferences(c,run,event,decision):
    actions={(r['kind'],r['target']):json.loads(r['body']) for r in c.execute('SELECT * FROM replay_actions WHERE run_id=?',(run,))}
    def enabled(kind,target):
        value=actions.get((kind,target),False)
        return value.get('value',False) if isinstance(value,dict) else value
    followed=bool(enabled('follow',event['member_id']) or enabled('subscription',decision['object_id']))
    obj=next((o for o in product_research.objects(event) if o['id']==decision['object_id']),None)
    if obj and obj.get('code'):followed=followed or bool(enabled('subscription',obj['type']+':'+obj['code']))
    muted=bool(enabled('mute',event['member_id']) or enabled('mute',decision.get('id','')))
    return followed,muted

def previous_assessment(item):
    if item.get('rule_version')==rules.VERSION:return item['rule_score']
    p={'高':'red','中':'orange','低':'yellow'}.get(item.get('level'),'grey')
    return dict(priority=p,delivery_priority=p,lower=0,fact_signature=None,evidence=dict(level=0),missing=item.get('missing',[]))

def publish(c,run,event,decision,*,cause):
    """Shared by explicit review import and the existing event publisher."""
    item=copy.deepcopy(decision)
    cause=item.get('review_cause',cause)
    rid=item.get('legacy_notice_id') or 'notice-'+corpus.digest([run,event['member_id'],item['object_id']])[:24]
    old=c.execute('SELECT * FROM news_reminder_versions WHERE run_id=? AND id=? ORDER BY version DESC LIMIT 1',(run,rid)).fetchone()
    prior=json.loads(old['body']) if old else None
    # A saved assessment is not a new semantic answer on each publisher refresh.
    if prior and prior.get('impact_review_id')==item.get('impact_review_id') and not item.get('stale_assessment'):
        return prior
    sent=c.execute('SELECT body FROM news_notifications WHERE run_id=? AND notice_id=? ORDER BY version DESC LIMIT 1',(run,rid)).fetchone()
    sent=json.loads(sent[0]) if sent else None
    baseline=previous_assessment(sent) if sent else None
    if baseline and sent.get('delivery'):baseline=dict(baseline,delivery_priority=sent['delivery']['delivery_priority'])
    followed,muted=preferences(c,run,event,item)
    if item.get('stale_assessment'):
        return dict(prior or item,stale_assessment=True,missing=list(dict.fromkeys((prior or item).get('missing',[])+['当前业务阶段尚待新的影响审阅'])))
    prior_assessment=prior
    if prior_assessment is None:
        saved=c.execute('SELECT body FROM replay_events WHERE run_id=? AND id=?',(run,event['member_id'])).fetchone()
        if saved:prior_assessment=next((d for d in json.loads(saved[0]).get('warning_reasons',{}).get('decisions',[]) if d.get('object_id')==item['object_id']),None)
    current=item['rule_score'];previous=previous_assessment(prior_assessment) if prior_assessment else None
    delivery=rules.transition(current,previous,baseline,item['change_judgment'],cause=cause,followed=followed,muted=muted)
    item.update(delivery=delivery,change=delivery['action'])
    # Wording, citations and reviewer provenance are assessment history, not a new alert.
    substance=dict(object_id=item['object_id'],fact=current['fact_signature'],priority=current['priority'],potential=current['potential_priority'],
        bounds=[current['lower'],current['upper']],evidence=[current['evidence']['level'],current['evidence']['conflict']],
        priority_rules=[r['rule'] for r in current['priority_rules']],rule_version=rules.VERSION)
    signature=corpus.digest(substance)
    if old and old['signature']==signature:return prior
    if not old and not item['alert'] and not delivery['notify']:return item
    item.update(id=rid,version=old['version']+1 if old else 1,event_id=event['member_id'],event_version=event['event_version'],active=bool(item['alert']),external_notification=False)
    c.execute('INSERT INTO news_reminder_versions VALUES(?,?,?,?,?,?,?)',(run,rid,item['version'],event['member_id'],signature,corpus.js(item),corpus.now()))
    return item

def overlay(event,projection,identity):
    existing=[d for d in event.get('warning_reasons',{}).get('decisions',[]) if d.get('target')];by={d['target']:dict(copy.deepcopy(d),impact_review_id=identity) for d in projection}
    event['warning_reasons']['decisions']=[by.pop(d.get('target'),d) for d in existing]+list(by.values())

def restore(c,run,event):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='impact_reviews'").fetchone():return
    allowed=set(objects(event))
    rows=c.execute("SELECT * FROM impact_reviews WHERE run_id=? AND event_id=? AND status='accepted' ORDER BY created DESC,rowid DESC",(run,event['member_id'])).fetchall();seen=set()
    for row in rows:
        record=json.loads(row['body']);projection=json.loads(row['projection']);selected=[]
        for d in projection:
            if d['object_id'] not in allowed:continue
            if d['object_id'] in seen:continue
            seen.add(d['object_id'])
            if record['event_version']!=event['event_version']:d['stale_assessment']=True
            selected.append(d)
        overlay(event,selected,row['id'])

def require_current_review(c,event):
    if corpus.metadata(c).get('impact_rule_version')!=rules.VERSION:return
    for d in event.get('warning_reasons',{}).get('decisions',[]):
        if d.get('rule_version')==rules.VERSION:continue
        d.update(rule_version='impact-review-required-v3',level='待定',alert=False,route='observation',
            reasons=['当前事项尚未完成四项影响审阅'],missing=list(dict.fromkeys(d.get('missing',[])+['按当前材料提交逐对象影响答复'])))
        for key in ('rule_score','id','version','active','delivery'):d.pop(key,None)

def apply(app,run_id,record,*,origin='codex_session',cause='new_material',calendar=None):
    if origin not in ('codex_session','offline_model_fixture','model_response'):raise ValueError('unknown_impact_review_origin')
    identity='impact:'+corpus.digest([record,origin,cause,calendar])
    with closing(app.connect()) as c:c.executescript(SCHEMA)
    stored=dict(record,origin=origin,cause=cause,trading_calendar=calendar) if isinstance(record,dict) else dict(raw_answer=record,origin=origin,cause=cause)
    try:
        if not isinstance(record,dict):raise ValueError('impact_answer_must_be_object')
        with closing(app.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            admitted=c.execute('SELECT status FROM impact_reviews WHERE id=?',(identity,)).fetchone()
            if admitted and admitted[0] in ('accepted','historical_only','article_only'):return dict(id=identity,changed=False,status=admitted[0])
            run=app.run(c,run_id);row=c.execute('SELECT body FROM replay_events WHERE run_id=? AND id=?',(run_id,record['event_id'])).fetchone()
            if not row:raise ValueError('impact_event_missing')
            old=json.loads(row[0])
            from_draft=False
            if corpus.digest(old)!=record['base_body_sha'] and c.execute("SELECT 1 FROM sqlite_master WHERE name='news_event_drafts'").fetchone():
                draft=c.execute('SELECT base_body_sha,body FROM news_event_drafts WHERE run_id=? AND event_id=?',(run_id,record['event_id'])).fetchone()
                if draft and draft['base_body_sha']==corpus.digest(old) and corpus.digest(json.loads(draft['body']))==record['base_body_sha']:
                    old=json.loads(draft['body']);from_draft=True
            processing_only=False
            if corpus.digest(old)!=record['base_body_sha'] and cause=='review_update':
                # Failed processing may update bookkeeping after a response arrived.
                # Reuse that response only if every business/evidence field is unchanged.
                def without_processing(value):
                    return {k:v for k,v in value.items() if k not in ('processing','saved_source','material_version')}
                for historical in c.execute('SELECT body FROM news_event_versions WHERE run_id=? AND event_id=? AND version=?',(run_id,record['event_id'],record['event_version'])):
                    base=json.loads(historical[0])
                    if corpus.digest(base)==record['base_body_sha'] and without_processing(base)==without_processing(old):
                        stored['admission_snapshot']=dict(original_body_sha=record['base_body_sha'],current_body_sha=corpus.digest(old),basis='business_and_evidence_unchanged_only_processing_metadata_changed')
                        processing_only=True
                        break
            if corpus.digest(old)!=record['base_body_sha'] and not processing_only:raise ValueError('impact_snapshot_changed')
            if old['event_version']!=record['event_version'] or set(old['evidence_sources']['source_rows'])!=set(record['source_rows']):raise ValueError('impact_material_or_stage_changed')
            docs=dossier_review.documents(c,run,record['source_rows'])
            article_only=record.get('article_only') is True
            basis=old
            if article_only:
                from event_workbench import product_downstream
                doc,mention,states=dossier_review.material_context(c,run,old,record)
                if moment(record['cutoff'])>moment(doc['available_at']):raise ValueError('article_review_cutoff_after_material')
                claims=product_downstream.credibility((states.get('verification') or {}).get('result'),mention['original_index'])['claims']
                ids={q['claim_id'] for q in claims if q.get('claim_id')}
                if any(not set(a['bound_claim_ids'])<=ids for a in record['assessments']):raise ValueError('article_impact_claim_outside_mention')
                bound={cid for a in record['assessments'] for cid in a['bound_claim_ids']}
                known={corpus.digest({k:d[k] for k in dossier_review.FIELDS}):d for d in docs.values()}
                for q in claims:
                    for evidence in q.get('score_input',{}).get('records',[]) if q['claim_id'] in bound else []:
                        material=known.get(evidence.get('document_sha'))
                        if evidence.get('applicable') and evidence.get('time_eligible') and (not material or moment(material['available_at'])>moment(record['cutoff'])):raise ValueError('article_claim_evidence_outside_cutoff')
                # Current reminder conditions must not leak into this article's separate assessment.
                basis=dict(old,monitoring_scope=copy.deepcopy(mention.get('monitoring_scope',{})),credibility=dict(claims=claims),warning_reasons=dict(decisions=[]))
            notified=[json.loads(r[0]) for r in c.execute('SELECT body FROM news_notifications WHERE run_id=? AND event_id=?',(run_id,old['member_id']))]
            projection=project(basis,record,docs,origin=origin,calendar=calendar,notified_history=() if article_only else notified)
            for d in projection:d['review_cause']=cause
            if article_only:
                c.execute('INSERT INTO impact_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run_id,record['event_id'],'article_only',corpus.js(stored),corpus.js(projection),'本篇补审，不修改提醒',record['reviewed_at']))
                return dict(id=identity,changed=False,recorded=True,status='article_only')
            previous={d.get('object_id'):d for d in old.get('warning_reasons',{}).get('decisions',[]) if d.get('rule_version')==rules.VERSION}
            stale=any(d['object_id'] in previous and previous[d['object_id']].get('fact_time') and
                (not d.get('fact_time') or review_scoring.publication_moment(d['fact_time'])<review_scoring.publication_moment(previous[d['object_id']]['fact_time'])) for d in projection)
            if stale:
                c.execute('INSERT INTO impact_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run_id,record['event_id'],'historical_only',corpus.js(stored),corpus.js(projection),'旧消息不覆盖较晚事实',record['reviewed_at']))
                return dict(id=identity,changed=False,status='historical_only')
            updated=copy.deepcopy(old);overlay(updated,projection,identity)
            if product_research.publication_gaps(updated):raise ValueError('impact_draft_still_incomplete')
            updated['warning_reasons']['decisions']=[publish(c,run_id,updated,d,cause=cause) if d.get('impact_review_id')==identity else d for d in updated['warning_reasons']['decisions']]
            updated['assessment_version']=old.get('assessment_version',0)+1;updated['material_version']=old['material_version']+1
            updated['assessment_signature']=dossier_review.assessment_signature(updated)
            prior=c.execute('SELECT signature FROM news_event_versions WHERE run_id=? AND event_id=? ORDER BY material_version DESC LIMIT 1',(run_id,old['member_id'])).fetchone()
            signature=prior[0]
            if from_draft:
                from event_workbench.news_events import EventPublisher
                signature=corpus.digest(EventPublisher._substance(updated))
            c.execute('INSERT INTO news_event_versions VALUES(?,?,?,?,?,?,?)',(run_id,old['member_id'],old['event_version'],updated['material_version'],signature,corpus.js(updated),record['reviewed_at']))
            c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?',(corpus.js(updated),run_id,old['member_id']))
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='news_event_drafts'").fetchone():c.execute('DELETE FROM news_event_drafts WHERE run_id=? AND event_id=?',(run_id,old['member_id']))
            c.execute('INSERT INTO impact_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run_id,old['member_id'],'accepted',corpus.js(stored),corpus.js(projection),'',record['reviewed_at']))
            news_notifications.record(c,run_id,updated)
            c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run_id,))
            return dict(id=identity,changed=True,status='accepted',event_id=old['member_id'],assessments=len(projection),reminders=[dict(id=d.get('id'),version=d.get('version'),score=d['rule_score']['score_text'],priority=d['priority'],delivery=d.get('delivery')) for d in updated['warning_reasons']['decisions'] if d.get('impact_review_id')==identity])
    except (ValueError,KeyError,TypeError,IndexError) as exc:
        with closing(app.connect()) as c,c:c.execute('INSERT OR REPLACE INTO impact_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run_id,record.get('event_id') if isinstance(record,dict) else None,'rejected',corpus.js(stored),'',str(exc),corpus.now()))
        raise

def check_due(app,run_id,as_of):
    """Explicit finite call, never a background timer or an HTTP read side effect."""
    today=date.fromisoformat(as_of);added=[]
    with closing(app.connect()) as c,c:
        c.execute('BEGIN IMMEDIATE');run=app.run(c,run_id)
        for row in app.visible_event_rows(c,run):
            event=json.loads(row['body'])
            for d in event.get('warning_reasons',{}).get('decisions',[]):
                node=d.get('next_observation',{});due=node.get('date')
                if d.get('rule_version')!=rules.VERSION or not due or date.fromisoformat(due)>today:continue
                identity='due:'+corpus.digest([event['member_id'],d['object_id'],node])[:24]
                if c.execute("SELECT 1 FROM replay_research WHERE run_id=? AND kind='task' AND id=?",(run_id,identity)).fetchone():continue
                body=dict(title='到期核查：'+node['text'],review_at=due,state='pending',trigger='R7',reason='时间节点到达；尚无结果不能认定失败或违约',snapshot_key=dict(event_id=event['member_id'],event_version=event['event_version'],material_version=event['material_version'],body_sha256=product_research.digest(event)))
                c.execute('INSERT INTO replay_research VALUES(?,?,?,?,?,?)',(run_id,'task',identity,event['member_id'],1,corpus.js(body)))
                c.execute('INSERT INTO replay_research_log(run_id,kind,id,event_id,revision,body,created) VALUES(?,?,?,?,?,?,?)',(run_id,'task',identity,event['member_id'],1,corpus.js(body),corpus.now()));added.append(identity)
        if added:c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run_id,))
    return dict(added=added,market_notifications=0)
