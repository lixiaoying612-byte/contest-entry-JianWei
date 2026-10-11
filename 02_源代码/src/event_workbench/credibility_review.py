"""Append scoped credibility assessments, leaving business and alert history intact."""
import copy
import json
from contextlib import closing
from event_workbench import review_scoring as scoring, dossier_review, training_corpus as corpus
from event_workbench.news_materials import moment

SCHEMA='''CREATE TABLE IF NOT EXISTS credibility_reviews(
 id TEXT PRIMARY KEY,run_id TEXT,event_id TEXT,status TEXT,body TEXT,projection TEXT,error TEXT,created TEXT)'''


def claim_groups(event, claims, raw):
    """Admit explicit semantic reviews; matching strings do not prove equivalence."""
    from event_workbench.product_research import objects
    fields={'subject','action','item','phase','time','conditions','quantity','objects'}
    existing={q['claim_id']:q for q in event.get('credibility',{}).get('claims',[])}
    current={q['claim_id']:q for q in claims};allowed={o['id'] for o in objects(event)}
    if not isinstance(raw,list):raise ValueError('claim_groups_list_required')
    result=[];seen=set()
    for group in raw:
        if not isinstance(group,dict) or set(group)!={'members','reason'} or not isinstance(group['reason'],str) or not group['reason'].strip():raise ValueError('claim_group_reason_required')
        members=group['members']
        if not isinstance(members,list) or len(members)<2:raise ValueError('claim_group_needs_multiple_claims')
        signatures=[];ids=[];roles=set()
        for m in members:
            if not isinstance(m,dict) or set(m)!={'claim_id','quote','scope'}:raise ValueError('claim_group_member_fields')
            cid=m['claim_id'];q=current.get(cid);old=existing.get(cid,{})
            if not q or cid in seen or cid in ids:raise ValueError('claim_group_member_outside_or_repeated')
            if q.get('claim_role')=='historical' or q.get('superseded_by') or old.get('score_role') in ('历史阶段','已被更正'):raise ValueError('claim_group_not_current')
            roles.add(q.get('claim_role'))
            if m['quote']!=q.get('score_input',{}).get('basis',{}).get('quote'):raise ValueError('claim_group_quote_binding')
            scope=m['scope']
            if not isinstance(scope,dict) or set(scope)!=fields or any(not isinstance(scope[k],str) or not scope[k].strip() for k in fields-{'objects'}):raise ValueError('claim_group_scope_fields')
            targets=scope['objects']
            if not isinstance(targets,list) or not targets or any(not isinstance(t,str) for t in targets) or len(targets)!=len(set(targets)) or not set(targets)<=allowed:raise ValueError('claim_group_object_scope')
            signatures.append(dict(scope,objects=sorted(targets)));ids.append(cid)
        if len(roles)!=1:raise ValueError('claim_group_roles_differ')
        if any(s!=signatures[0] for s in signatures[1:]):raise ValueError('claim_group_scope_differs')
        seen.update(ids)
        result.append(dict(copy.deepcopy(group),claim_ids=ids,id='claim-group:'+corpus.digest(sorted(ids))))
    return result


def project(event, record, source_documents, catalog):
    if record.get('rule_version')!=scoring.VERSION or not record.get('reason'):
        raise ValueError('credibility_review_rule_or_reason_required')
    cutoff=record['cutoff']
    if moment(cutoff)>moment(record['assessed_at']):raise ValueError('review_cutoff_after_assessment')
    existing={q['claim_id']:q for q in event.get('credibility',{}).get('claims',[])}
    outputs=[];seen=set()
    for item in record['claims']:
        cid=item['claim_id'];old=existing.get(cid)
        if cid in seen:raise ValueError('duplicate_claim_review')
        seen.add(cid)
        if not old and not item.get('additional'):raise ValueError('review_claim_not_in_event')
        if old and item.get('previous_claim')!=old['claim']:raise ValueError('review_claim_binding_changed')
        if item['source_row'] not in source_documents:raise ValueError('review_claim_source_outside_event')
        original=source_documents[item['source_row']]
        document={k:original[k] for k in scoring.FIELDS}
        if item['document_sha']!=corpus.digest(document):raise ValueError('review_document_hash_mismatch')
        if moment(original['available_at'])>moment(cutoff):raise ValueError('review_material_after_cutoff')
        source_ids={m.get('document_id') for m in event.get('evidence_sources',{}).get('members',[]) if m.get('source_row')==item['source_row']}
        source_ids.update((original['document_id'],corpus.digest(document)))
        if old and old.get('basis',{}).get('document_id') and old['basis']['document_id'] not in source_ids:
            raise ValueError('review_claim_document_mismatch')
        features=item['features']
        basis=(old or {}).get('basis')
        if basis and basis.get('quote')!=features.get('claim_quote'):basis=None
        if old and features.get('role')=='historical':
            scoring.features(document,features,catalog,cutoff,basis=basis)
            q=copy.deepcopy(old);q['claim_role']='historical';outputs.append(q);continue
        result=scoring.score(document,features,catalog,cutoff=cutoff,basis=basis)
        q=copy.deepcopy(old) if old else dict(claim_id=cid,valid_assessments=[],source='codex_session',scoring_only=True)
        q.pop('score_source_input',None)
        q.update(result,claim=features['claim'],claim_role=features['role'])
        q['verdict']=scoring.reviewed_verdict(features,result)
        if not old:q['basis']=dict(result['score_input']['basis'],document_id=original['document_id'],source_row=item['source_row'])
        if old and old['claim']!=q['claim']:q['previous_claim']=old['claim']
        outputs.append(q)
    current={q['claim_id'] for q in existing.values() if q.get('score_role')!='历史阶段'}
    if not current<=seen:raise ValueError('current_claims_not_fully_reviewed')
    groups=claim_groups(event,outputs,record['claim_groups']) if 'claim_groups' in record else None
    return dict(claims=outputs,**({'claim_groups':groups} if groups is not None else {}),assessed_at=record['assessed_at'],assessment_cutoff=cutoff,
                review_reason=record['reason'],reviewer_type=record['origin'],financial_human_review='pending')


def overlay(event, projection, identity, *, exact=False):
    cred=event['credibility'];incoming={q['claim_id']:q for q in cred.get('claims',[])}
    if exact:
        cred['claims']=copy.deepcopy(projection['claims'])
    else:
        for checked in projection['claims']:
            cid=checked['claim_id']
            if cid in incoming and incoming[cid].get('score_role')!='历史阶段':
                old=incoming[cid];role='historical' if old.get('score_role')=='历史阶段' else checked['claim_role']
                old.pop('score_source_input',None)
                for k,v in checked.items():
                    if k.startswith('score') or k in ('short_reason','raw_score','display','applied_caps','probability','claim','claim_role','previous_claim'):
                        old[k]=copy.deepcopy(v)
                old['claim_role']=role
            elif checked.get('scoring_only') and exact:
                cred['claims'].append(copy.deepcopy(checked))
    cred.pop('claim_groups',None)
    groups=projection.get('claim_groups',[]) if exact else []
    cred.update(scoring.event_score(cred.get('claims',[]),claim_groups=groups))
    if groups:cred['claim_groups']=copy.deepcopy(groups)
    cred.update({k:projection[k] for k in ('assessed_at','assessment_cutoff','review_reason','reviewer_type','financial_human_review')})
    cred['review_id']=identity


def restore(c, run, event):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='credibility_reviews'").fetchone():return
    row=c.execute("SELECT * FROM credibility_reviews WHERE run_id=? AND event_id=? AND status='accepted' ORDER BY created DESC,rowid DESC LIMIT 1",(run,event['member_id'])).fetchone()
    if not row:return
    record=json.loads(row['body']);projection=json.loads(row['projection'])
    sources=set(event.get('evidence_sources',{}).get('source_rows',[]))
    # Supplementary claims and their evidence only belong to the reviewed scope.
    exact=set(record['source_rows'])==sources and record['event_version']==event.get('event_version')
    overlay(event,projection,row['id'],exact=exact)


def apply(app, run_id, record, catalog):
    if record.get('origin') not in ('codex_session','offline_model_fixture','model_response'):
        raise ValueError('unknown_review_origin')
    identity='credibility:'+corpus.digest([record,catalog])
    with closing(app.connect()) as c:c.executescript(SCHEMA)
    try:
        with closing(app.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            prior=c.execute('SELECT status FROM credibility_reviews WHERE id=?',(identity,)).fetchone()
            if prior and prior[0]=='accepted':return dict(id=identity,changed=False)
            run=app.run(c,run_id)
            row=c.execute('SELECT body FROM replay_events WHERE run_id=? AND id=?',(run_id,record['event_id'])).fetchone()
            if not row:raise ValueError('review_event_missing')
            old=json.loads(row[0])
            if corpus.digest(old)!=record['base_body_sha']:raise ValueError('review_snapshot_changed')
            sources=old.get('evidence_sources',{}).get('source_rows',[])
            if set(sources)!=set(record['source_rows']) or old['event_version']!=record['event_version']:
                raise ValueError('review_material_or_stage_scope_changed')
            docs=dossier_review.documents(c,run,sources)
            # A catalog reference to an in-library article must be in this event's scope.
            for material in catalog.values():
                if material.get('source_row') and material['source_row'] not in docs:
                    raise ValueError('scoring_catalog_source_outside_event')
                if material.get('source_row'):
                    original=docs[material['source_row']]
                    if material['document']!={k:original[k] for k in scoring.FIELDS}:
                        raise ValueError('scoring_catalog_document_changed')
                    if scoring.publication_moment(material['available_at'])<scoring.publication_moment(original['available_at']):
                        raise ValueError('scoring_catalog_availability_backdated')
            projection=project(old,record,docs,catalog)
            updated=copy.deepcopy(old);overlay(updated,projection,identity,exact=True)
            updated['assessment_version']=old.get('assessment_version',0)+1
            updated['assessment_signature']=dossier_review.assessment_signature(updated)
            updated['material_version']=old['material_version']+1
            prev=c.execute('SELECT signature FROM news_event_versions WHERE run_id=? AND event_id=? ORDER BY material_version DESC LIMIT 1',(run_id,record['event_id'])).fetchone()
            body=corpus.js(updated)
            c.execute('INSERT INTO news_event_versions VALUES(?,?,?,?,?,?,?)',(run_id,record['event_id'],old['event_version'],updated['material_version'],prev[0],body,record['assessed_at']))
            c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?',(body,run_id,record['event_id']))
            c.execute('INSERT OR REPLACE INTO credibility_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run_id,record['event_id'],'accepted',corpus.js(dict(record,evidence_catalog=catalog)),corpus.js(projection),'',record['assessed_at']))
            c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run_id,))
            return dict(id=identity,changed=True,event_id=record['event_id'],score=updated['credibility'].get('score'))
    except (ValueError,KeyError,TypeError,IndexError) as error:
        with closing(app.connect()) as c,c:
            c.execute('INSERT OR REPLACE INTO credibility_reviews VALUES(?,?,?,?,?,?,?,?)',(identity,run_id,record.get('event_id'),'rejected',corpus.js(dict(record,evidence_catalog=catalog)),'',str(error),corpus.now()))
        raise
