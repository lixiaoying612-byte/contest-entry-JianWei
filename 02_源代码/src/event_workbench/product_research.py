"""Saved dossier snapshots and human workspace records; never invoke models."""
import hashlib
import copy
import json
import uuid
from contextlib import closing
from datetime import date
from event_workbench import training_corpus as corpus
from event_workbench.alert_rules import pathway_text

SCHEMA = '''
CREATE TABLE IF NOT EXISTS replay_research(run_id TEXT,kind TEXT,id TEXT,event_id TEXT,
 revision INTEGER,body TEXT,PRIMARY KEY(run_id,kind,id));
CREATE TABLE IF NOT EXISTS replay_research_log(seq INTEGER PRIMARY KEY,run_id TEXT,
 kind TEXT,id TEXT,event_id TEXT,revision INTEGER,body TEXT,created TEXT);
'''
CONTRACT = 'research-dossier-v1'


def digest(value):
    return hashlib.sha256(corpus.js(value).encode()).hexdigest()


def business_matches(event):
    """Keep a grounded object and business name without inventing an official label."""
    scope=event.get('monitoring_scope',{});matches=copy.deepcopy(scope.get('matches',[]))
    for obj in scope.get('business_objects',[]):
        same=[m for m in matches if m.get('decision') in ('matched','m') and
              m.get('definition',{}).get('target_name')==obj['target_name'] and
              (m.get('definition',{}).get('target_type')=='industry')==(obj['target_type']=='industry')]
        if same:
            for match in same:
                if obj.get('object_id'):match['definition']['object_id']=obj['object_id']
        else:
            matches.append(dict(decision='supplementary',definition=dict(obj,event_name=obj['business_name']),
                reason=(scope.get('unmatched') or {}).get('reason') or obj['reason'],evidence=obj['evidence'],
                semantic_review_status=scope.get('semantic_review_status')))
    return matches


def objects(event):
    found = {}
    for match in business_matches(event):
        d = match.get('definition', {})
        if match.get('decision') not in ('matched', 'm','supplementary') or not (d.get('target_code') or d.get('object_id')):
            continue
        kind = 'industry' if d.get('target_type') == 'industry' else 'stock'
        key = d.get('object_id') or kind + ':' + str(d['target_code'])
        found[key] = dict(id=key, name=d['target_name'], code=str(d['target_code']) if d.get('target_code') else None, type=kind)
    return list(found.values())


def affected_objects(event):
    """Only saved object judgments qualify; a monitoring match alone is not impact."""
    result = []
    for obj in objects(event):
        judgments = [d for d in event.get('warning_reasons', {}).get('decisions', [])
                     if d.get('target') == obj['name'] or obj['name'] in d.get('targets', [])]
        if not judgments:
            continue
        effects = [x.get('original', {}) for d in judgments for x in d.get('effects', [])
                   if x.get('original', {}).get('target', {}).get('name') == obj['name']]
        if not effects:
            continue
        directions = set(x.get('direction', 'uncertain') for x in effects)
        direction = next(iter(directions)) if len(directions) == 1 else 'mixed' if directions else 'uncertain'
        result.append(dict(obj, direction=direction, level=judgments[0].get('level', '待定'),
                           pathways=list(dict.fromkeys(pathway_text(x.get('pathway')) for x in effects if pathway_text(x.get('pathway')))),
                           conditions=list(dict.fromkeys(v for x in effects for v in x.get('conditions', []))),
                           gaps=list(dict.fromkeys(v for d in judgments for v in d.get('missing', []))),
                           decision_ids=[d['id'] for d in judgments if d.get('id')]))
    return result


def object_emotion(emotions,definition_id,object_id=None):
    checked=[x for x in emotions if (x.get('object_id')==object_id if object_id else definition_id is not None and x.get('definition_id')==definition_id) and x.get('status')!='failed']
    labels={x.get('label') for x in checked if x.get('label') in ('正面','中性','负面')}
    label=next(iter(labels)) if len(labels)==1 else None
    return dict(label=label,status='judged' if label else 'conflicting' if len(labels)>1 else 'undetermined',evidence=checked)


def publication_gaps(event):
    """Reader readiness, independent of ingestion retention and alert severity."""
    if event.get('record_kind') != 'event':
        return []
    subjects = event.get('event_subjects', {})
    decisions = event.get('warning_reasons', {}).get('decisions', [])
    checks = {
        '事件摘要': event.get('event_summary', {}).get('text'),
        '主体或明确缺口': subjects.get('participants') or subjects.get('uncertainties'),
        '逐主张核验': event.get('credibility', {}).get('claims'),
        '影响分析与提醒依据': any(d.get('effects') and d.get('reasons') for d in decisions) and all(
            any((d.get('object_id')==obj['object_id'] if d.get('object_id') and obj.get('object_id') else d.get('target')==obj['target_name']) and d.get('effects') and d.get('reasons') for d in decisions)
            for obj in event.get('monitoring_scope',{}).get('business_objects',[])),
        '材料来源': event.get('evidence_sources', {}).get('members'),
        '时间脉络': event.get('event_timeline', {}).get('items'),
    }
    return [name for name, value in checks.items() if not value]


def business_output(event):
    """One factual snapshot projected for readers and label comparison, without labels as input."""
    rows=[];impacts=affected_objects(event)
    for match in business_matches(event):
        d=match.get('definition',{})
        if match.get('decision') not in ('matched','m','possible','supplementary') or not (d.get('target_code') or d.get('object_id')):continue
        kind='industry' if d.get('target_type')=='industry' else 'company'
        metadata=next((m for m in event.get('industry_metadata',[]) if m.get('company_code')==str(d['target_code']) and m.get('company_name')==d.get('target_name')),None) if kind=='company' else None
        emotion=object_emotion(event.get('emotions',[]),d.get('definition_id'),d.get('object_id'))
        impact=next((x for x in impacts if (x['id']==d['object_id'] if d.get('object_id') else x['code']==str(d['target_code']) and (x['type']=='industry')==(kind=='industry'))),None)
        decisions=[x for x in event.get('warning_reasons',{}).get('decisions',[]) if x.get('target')==d.get('target_name') or d.get('target_name') in x.get('targets',[])]
        analysis=dict(status='available' if impact else 'pending',facts=list(dict.fromkeys(c['claim'] for n in decisions for c in n.get('bound_claims',[]) if c.get('claim'))),
            direction=impact['direction'] if impact else None,pathways=impact['pathways'] if impact else [],conditions=impact['conditions'] if impact else [],gaps=impact['gaps'] if impact else ['该对象尚未保存影响判断'],
            measurements=[n['rule_score']['measurement'] for n in decisions if n.get('rule_score',{}).get('measurement')],
            next_observations=list(dict.fromkeys(n['next_observation']['text'] for n in decisions if n.get('next_observation',{}).get('text'))),
            scores=[{k:n.get('rule_score',{}).get(k) for k in ('score','score_text','label','components','rule_version')} for n in decisions if n.get('rule_score')],
            evidence=[q for n in decisions for q in n.get('impact_proofs',[])])
        full='；'.join(analysis['pathways'])
        if analysis['conditions']:full+='。成立条件：'+'；'.join(analysis['conditions'])
        if analysis['gaps']:full+=('。' if full else '')+'待核信息：'+'；'.join(analysis['gaps'])
        if analysis['next_observations']:full+='。后续观察：'+'；'.join(analysis['next_observations'])
        rows.append(dict(target_type=kind,object_id=d.get('object_id'),definition_id=d.get('definition_id') if match['decision']!='supplementary' else None,
            official_label_eligible=match['decision'] in ('matched','m'),
            trading_code=str(d['target_code']) if kind=='company' and d.get('target_code') else None,secu_abbr=d['target_name'] if kind=='company' else None,
            industry_code=str(d['target_code']) if kind=='industry' and d.get('target_code') else (metadata or d).get('industry_code'),industry_name=d['target_name'] if kind=='industry' else (metadata or d).get('industry_name'),
            industry_status='known' if kind=='industry' or metadata or d.get('industry_code') else 'not_saved',industry_reference=metadata,
            event_name=d.get('event_name'),classification=dict(decision=match['decision'],reason=match.get('reason'),semantic_review_status=match.get('semantic_review_status','not_recorded'),evidence=match.get('evidence',[])),
            event_emotion=emotion['label'],emotion_status=emotion['status'],emotion_evidence=emotion['evidence'],
            event_impact_analysis=full,impact=analysis))
    cred=event.get('credibility',{})
    credibility={k:cred.get(k) for k in ('score','score_label','score_rule','score_max','score_status','short_reason','score_determining_claim','assessed_at','score_items','score_limits','score_scope','display')}
    credibility['claims']=[{k:c.get(k) for k in ('claim_id','claim','claim_role','verdict','score','short_reason','basis','score_items','score_limits')} for c in cred.get('claims',[])]
    return dict(contract='event-business-output-20261005-v1',event_id=event.get('member_id'),event_version=event.get('event_version'),material_version=event.get('material_version'),
        summary=event.get('event_summary',{}),subjects=event.get('event_subjects',{}),facts=event.get('event_facts',[]),label_rows=rows,
        credibility=credibility,sources=event.get('evidence_sources',{}).get('members',[]),timeline=event.get('event_timeline',{}),
        relationships=[dict(id=r.get('id'),relation=r.get('raw',{}).get('k'),reason=r.get('raw',{}).get('why'),operation=r.get('aggregation',{}).get('operation')) for r in event.get('event_relations',[])],
        warnings=[{k:n.get(k) for k in ('id','target','active','alert','route','reasons','delivery','next_observation')} for n in event.get('warning_reasons',{}).get('decisions',[])],
        label_comparison_note='当前档案快照的逐对象结果，非整篇所有事项的预测导出；未读取官方答案。文章重复ID须从材料关系映射，不能用事件ID代替。')


def timeline(snapshots):
    # Business version is not node count: supplements/corrections keep the node.
    if snapshots and snapshots[-1][1].get('event_timeline',{}).get('business_nodes'):
        result=[]
        for node in snapshots[-1][1]['event_timeline']['business_nodes']:
            first=next(((r,b) for r,b in snapshots if set(node['members']) & {m['mention_id'] for m in b.get('evidence_sources',{}).get('members',[])}),None)
            if first:
                row,body=first
                # The navigation label is current knowledge; the opened body stays at first availability.
                result.append(dict(node,event_version=row['version'],material_version=row['material_version']))
        return result
    nodes, seen = [], set()
    for row, body in snapshots:
        if row['version'] in seen:
            continue
        progress = [t for t in body.get('event_timeline', {}).get('items', [])
                    if t.get('kind') in ('业务进展', '首次披露', '另一事项', '实际进展', '澄清更正')]
        if not progress and body.get('record_kind') == 'event':
            progress = [dict(time=body.get('updated_at') or body.get('available_at'),
                             kind=('首次收录（披露日期未提供）' if body.get('update_time_basis')=='received_date_unknown' or not body.get('evidence_sources',{}).get('original',{}).get('publication_time') else '首次披露') if row['version'] == 1 else '业务进展',
                             summary=body.get('event_summary', {}).get('text'))]
        if progress:
            nodes.append(dict(progress[-1], event_version=row['version'], material_version=row['material_version']))
            seen.add(row['version'])
    return nodes


def source_relations(event):
    if event.get('source_relations'):return event['source_relations']
    members = event.get('evidence_sources', {}).get('members', [])
    by_doc = {m.get('document_id'): m['source_row'] for m in members}
    visible = {m['source_row'] for m in members}
    edges = {}
    for claim in event.get('credibility', {}).get('claims', []):
        owner = by_doc.get(claim.get('source_document_id'))
        for a in claim.get('valid_assessments', []):
            evidence = a.get('source_row')
            relation = a.get('source_relation')
            if owner not in visible or evidence not in visible or owner == evidence:
                continue
            key = (owner, evidence, relation)
            edges[key] = dict(material_a=owner, material_b=evidence, relation=relation,
                              directed=False, independent_confirmation=False,
                              claim=claim.get('claim'), quote=(a.get('proof') or {}).get('quote'),
                              reason=a.get('proposal', {}).get('reason'))
    for relation in event.get('event_relations',[]):
        proofs=relation.get('original_proofs',[])
        if len(proofs)<2:continue
        endpoints=list(dict.fromkeys(by_doc.get(p.get('document_id')) for p in proofs if by_doc.get(p.get('document_id'))))
        if len(endpoints)==2:
            agg=relation.get('aggregation',{})
            edges[relation['id']]=dict(material_a=endpoints[0],material_b=endpoints[1],relation=relation['raw']['k'],reason=relation['raw']['why'],independent_confirmation=False,proofs=proofs,
                **(dict(operation=agg['operation'],operation_label=agg['label'],decision_status=relation['status'],claim_delta=agg['claim_delta']) if agg else {}))
    publication={t['source_row']:t['time'] for t in event.get('event_timeline',{}).get('items',[]) if t.get('source_row') and t.get('kind')=='材料发布时间（非发生时间）'}
    nodes={m['source_row']:dict(m,publication_time=m.get('publication_time') or publication.get(m['source_row'])) for m in members}
    return dict(nodes=list(nodes.values()), edges=list(edges.values()),
                note='关系来自已保存的逐主张核验；不推定转载方向或独立证实。')


def event_sources(event):
    sources = set(event.get('evidence_sources', {}).get('source_rows', []))
    sources.update(m['source_row'] for m in event.get('evidence_sources', {}).get('members', []))
    sources.update(a['source_row'] for claim in event.get('credibility', {}).get('claims', [])
                   for a in claim.get('valid_assessments', []) if a.get('source_row'))
    return sources


def allowed(app, c, run, event):
    for source in event_sources(event):
        if not c.execute('''SELECT 1 FROM replay_scope_rows s JOIN source_rows r ON r.row_key=s.source_row
          JOIN replay_rows p ON p.row_id=r.id WHERE s.run_id=? AND s.source_row=?
          AND p.guard='allowed' AND p.available_at<?''', (run['id'], source, run['cutoff'])).fetchone():
            return False
    return True


def _dossier(app, c, run, event_id, material_version=None):
    if c.execute("SELECT 1 FROM sqlite_master WHERE name='news_event_aliases'").fetchone():
        seen=set()
        while True:
            if event_id in seen:raise ValueError('事件入口映射存在循环')
            seen.add(event_id)
            alias=c.execute('SELECT event_id FROM news_event_aliases WHERE run_id=? AND old_id=?',(run['id'],event_id)).fetchone()
            if not alias:break
            event_id=alias[0]
    current = next((json.loads(r['body']) for r in app.visible_event_rows(c, run,event_id)), None)
    if current is None or not allowed(app, c, run, current):
        raise ValueError('事件不在当前可见范围')
    exists = c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='news_event_versions'").fetchone()
    rows = c.execute('SELECT * FROM news_event_versions WHERE run_id=? AND event_id=? ORDER BY material_version',
                     (run['id'], event_id)).fetchall() if exists else []
    safe=[]
    for row in rows:
        body=json.loads(row['body'])
        if allowed(app,c,run,body):safe.append((row,body))
    selected = next(((r, e) for r, e in safe if r['material_version'] == material_version), None)
    if material_version is not None and selected is None:
        raise ValueError('版本不存在或材料不在当前可见范围')
    event = selected[1] if selected else current
    pending_updates=[]
    if selected is None:
        from event_workbench import news_outcomes
        for m in event.get('evidence_sources',{}).get('members',[]):
            state=news_outcomes.saved(c,m.get('document_id'),[event_id],m.get('source_row'))
            if state['processing_incomplete'] and m.get('source_row') not in pending_updates:pending_updates.append(m.get('source_row'))
    current_sha=digest(current)
    key = dict(event_id=event_id, event_version=event.get('event_version'),
               material_version=event.get('material_version'), body_sha256=digest(event) if selected else current_sha)
    # Navigation is current known history, separately from the selected factual snapshot.
    all_versions=[]
    for r,e in safe:
        body_sha=digest(e)
        all_versions.append(dict(event_version=r['version'],material_version=r['material_version'],
            assessment_version=e.get('assessment_version'),saved_at=r['created'],body_sha256=body_sha,current=body_sha==current_sha))
    versions = [v for v in all_versions if v['material_version'] <= key['material_version']]
    selected_snapshots = [(r, body) for r, body in safe if r['material_version'] <= key['material_version']]
    selected_timeline, navigation_timeline = timeline(selected_snapshots), timeline(safe)
    # A checked summary changes the selected assessment, not business node identity.
    # Use only this snapshot's review; never fill an early snapshot from a later review.
    if event.get('identification_review') and event.get('event_summary', {}).get('text'):
        current_node = event.get('event_timeline', {}).get('current_node')
        for nodes in (selected_timeline, navigation_timeline):
            for node in nodes:
                selected_node = node.get('id') == current_node if current_node else node.get('event_version') == key['event_version']
                if selected_node:
                    node['summary'] = event['event_summary']['text']
    reminders = {}
    for row, body in selected_snapshots:
        for notice in body.get('warning_reasons', {}).get('decisions', []):
            if not notice.get('id') or type(notice.get('version')) is not int or type(notice.get('active')) is not bool:
                continue
            reminders.setdefault((notice['id'], notice['version']), dict(id=notice['id'],
                                 version=notice['version'], active=notice['active'], target=notice.get('target'),
                                 material_version=row['material_version'], saved_at=row['created']))
    return dict(contract=CONTRACT, run=run['id'], revision=run['revision'], key=key,
                update_status=dict(pending_materials=pending_updates,message='新分析与旧事项尚不能唯一对应；当前保留原档案，等待核对关联。' if current.get('identity_update_pending') else '该材料已重新识别为背景，已退出当前事件列表；历史判断和用户记录保留。' if current.get('current_status')=='background' else '补充分析尚缺：'+'、'.join(current['publication_update_pending'])+'；当前保留原有效判断。' if current.get('publication_update_pending') else '部分材料的新分析尚未完成；当前显示最后保存的有效判断。' if pending_updates else ''),
                historical=key['body_sha256'] != current_sha, saved_at=selected[0]['created'] if selected else None,
                event=event, objects=objects(event), versions=versions, sources=source_relations(event),
                affected_objects=affected_objects(event),business_output=business_output(event),
                reminder_versions=list(reminders.values()), timeline_nodes=selected_timeline,
                navigation=dict(timeline_nodes=[{k: n.get(k) for k in ('time', 'kind', 'summary', 'event_version', 'material_version',*(['id','time_basis'] if n.get('id') else []))} for n in navigation_timeline], versions=all_versions,
                                selected_material_version=key['material_version']))


def dossier(app, run_id, event_id, material_version=None):
    with closing(app.connect()) as c:
        return _dossier(app, c, app.run(c, run_id), event_id,
                        int(material_version) if material_version is not None else None)


def records(app, run_id):
    with closing(app.connect()) as c:
        run = app.run(c, run_id)
        visible = {}
        interested={r[0] for r in c.execute('SELECT event_id FROM replay_research WHERE run_id=? UNION SELECT event_id FROM replay_research_log WHERE run_id=?',(run_id,run_id))}
        for event_id in interested:
            candidates=app.visible_event_rows(c,run,event_id)
            if not candidates:continue
            row=candidates[0]
            if not allowed(app, c, run, json.loads(row['body'])): continue
            d = _dossier(app, c, run, row['id'])
            visible[row['id']] = {v['body_sha256'] for v in d['versions']} | {d['key']['body_sha256']}
        def visible_record(row):
            key = json.loads(row['body']).get('snapshot_key', {})
            return key.get('body_sha256') in visible.get(row['event_id'], set())
        items = [dict(kind=r['kind'], id=r['id'], event_id=r['event_id'], revision=r['revision'],
                      **json.loads(r['body'])) for r in c.execute('SELECT * FROM replay_research WHERE run_id=?', (run_id,))
                 if visible_record(r)]
        history = [dict(kind=r['kind'], id=r['id'], event_id=r['event_id'], revision=r['revision'],
                        **json.loads(r['body'])) for r in c.execute('SELECT * FROM replay_research_log WHERE run_id=? ORDER BY seq', (run_id,))
                   if visible_record(r)]
        return dict(contract=CONTRACT, revision=run['revision'], items=items, history=history)


def text(value, key, limit=1000, required=True):
    v = value.get(key, '')
    if not isinstance(v, str) or len(v) > limit or (required and not v.strip()):
        raise ValueError(key + '格式不正确')
    return v.strip()


def change(app, payload):
    if not isinstance(payload, dict):
        raise ValueError('操作必须是对象')
    run_id, event_id, kind = (payload.get(k) for k in ('run', 'event_id', 'kind'))
    if not all(isinstance(x, str) and 0 < len(x) < 300 for x in (run_id, event_id, kind)):
        raise ValueError('运行或事件身份无效')
    value = payload.get('value')
    if not isinstance(value, dict) or type(payload.get('revision')) is not int:
        raise ValueError('记录格式无效')
    with closing(app.connect()) as c, c:
        c.execute('BEGIN IMMEDIATE')
        run = app.run(c, run_id)
        if run['revision'] != payload['revision']:
            raise ValueError('运行已变化，请刷新')
        saved = _dossier(app, c, run, event_id)
        if payload.get('snapshot_key') != saved['key']:
            raise ValueError('事件版本已变化，请刷新后操作')
        record_id = payload.get('id') or uuid.uuid4().hex
        if not isinstance(record_id, str) or len(record_id) > 200:
            raise ValueError('记录身份无效')
        old = c.execute('SELECT * FROM replay_research WHERE run_id=? AND kind=? AND id=?', (run_id, kind, record_id)).fetchone()
        if old and old['event_id'] != event_id:
            raise ValueError('记录不属于本事件')
        expected = old['revision'] if old else 0
        if type(payload.get('record_revision', 0)) is not int or payload.get('record_revision', 0) != expected:
            raise ValueError('记录已被修改，请刷新')
        if kind == 'review':
            if old:
                raise ValueError('复核记录不可覆盖，请追加')
            object_id = text(value, 'object_id', 100)
            obj = next((o for o in saved['objects'] if o['id'] == object_id), None)
            effects = [x for d in saved['event'].get('warning_reasons', {}).get('decisions', [])
                       for x in d.get('effects', []) if obj and x.get('original', {}).get('target', {}).get('name') == obj['name']]
            if not effects:
                raise ValueError('该对象尚无可复核的方向判断')
            if value.get('result') not in ('positive', 'negative', 'neutral', 'mixed', 'uncertain'):
                raise ValueError('方向无效')
            body = dict(object_id=object_id, result=value['result'], reason=text(value, 'reason'),
                        operator=text(value, 'operator', 100), original_effects=effects,
                        judgment_sha256=digest(effects), review_type='user_recorded_review',
                        status='confirmed' if all(x['original'].get('direction') == value['result'] for x in effects) else 'corrected')
        elif kind == 'task':
            when = text(value, 'review_at', 10, False)
            if when:
                try:
                    if date.fromisoformat(when).isoformat() != when: raise ValueError()
                except ValueError:
                    raise ValueError('复查日期无效')
            if value.get('state') not in ('pending', 'completed'):
                raise ValueError('待办状态无效')
            body = dict(title=text(value, 'title'), review_at=when, state=value['state'])
        elif kind == 'handling':
            notice = next((d for d in saved['event'].get('warning_reasons', {}).get('decisions', [])
                           if d.get('id') == record_id and type(d.get('version')) is int
                           and d['version'] > 0 and type(d.get('active')) is bool), None)
            if not notice or value.get('notice_version') != notice['version']:
                raise ValueError('提醒版本已变化或未发布')
            if value.get('state') not in ('pending', 'in_progress', 'waiting_material', 'closed'):
                raise ValueError('处置状态无效')
            body = dict(state=value['state'], owner=text(value, 'owner', 100), note=text(value, 'note'),
                        notice_version=notice['version'], notice=notice)
        else:
            raise ValueError('未开放的研究操作')
        body.update(snapshot_key=saved['key'], operated_at=corpus.now())
        encoded = corpus.js(body)
        c.execute('INSERT OR REPLACE INTO replay_research VALUES(?,?,?,?,?,?)',
                  (run_id, kind, record_id, event_id, expected + 1, encoded))
        c.execute('INSERT INTO replay_research_log(run_id,kind,id,event_id,revision,body,created) VALUES(?,?,?,?,?,?,?)',
                  (run_id, kind, record_id, event_id, expected + 1, encoded, body['operated_at']))
        # Maintain the existing closed indicator without changing event/reminder facts.
        if kind == 'handling':
            action = corpus.js(dict(value=body['state'] == 'closed', notice_version=body['notice_version'], operated_at=body['operated_at']))
            c.execute('INSERT OR REPLACE INTO replay_actions VALUES(?,?,?,?)', (run_id, 'alert_closed', record_id, action))
        return dict(saved=True, id=record_id, record_revision=expected + 1)
