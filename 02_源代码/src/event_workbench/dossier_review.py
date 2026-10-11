"""Append checked subject/summary corrections without inventing business progress."""
import copy
import json
from contextlib import closing
from event_workbench import review_import, training_corpus as corpus, product_research
from event_workbench.news_materials import moment

VERSION = 'dossier-elements-20261004-v1'
SCHEMA = '''CREATE TABLE IF NOT EXISTS dossier_reviews(
 id TEXT PRIMARY KEY,run_id TEXT,event_id TEXT,status TEXT,body TEXT,projection TEXT,error TEXT,created TEXT)'''
FIELDS = ('title', 'text', 'source', 'publication_time')


def documents(c, run, sources):
    result = {}
    for source in sources:
        row = c.execute('''SELECT t.title,t.body AS text,r.source_raw AS source,r.published_iso AS publication_time,
            r.document_id,p.available_at FROM source_rows r JOIN documents d ON d.id=r.document_id
            JOIN contents t ON t.id=d.content_id JOIN replay_rows p ON p.row_id=r.id
            JOIN replay_scope_rows s ON s.source_row=r.row_key
            WHERE s.run_id=? AND r.row_key=? AND p.guard='allowed' AND p.available_at<?''',
                        (run['id'], source, run['cutoff'])).fetchone()
        if row is None:
            raise ValueError('review_material_not_visible')
        result[source] = dict(row)
    return result


def material_context(c, run, event, record):
    """Bind a supplementary answer to one saved article and one admitted mention."""
    from event_workbench import business_evaluation
    source=record['primary_source_row']
    if source not in event['evidence_sources']['source_rows']:raise ValueError('review_source_outside_event')
    doc=documents(c,run,[source])[source];jid=corpus.digest({k:doc[k] for k in FIELDS})
    if record.get('document_sha')!=jid:raise ValueError('review_document_hash_mismatch')
    owner=c.execute('SELECT event_id FROM news_event_members WHERE run_id=? AND mention_id=?',(run['id'],record['mention_id'])).fetchone()
    if not owner or owner[0]!=event['member_id']:raise ValueError('review_mention_outside_event')
    states={r['stage']:dict(status=r['status'],result=json.loads(r['result']) if r['result'] else None)
            for r in c.execute('SELECT stage,status,result FROM stages WHERE job=?',(jid,))}
    reviewed_classifications(c,run['id'],source,jid,states,record.get('reviewed_at') or corpus.now())
    mention=next((m for m in business_evaluation.checked_mentions(states) if m.get('id')==record['mention_id']),None)
    if mention is None:raise ValueError('review_mention_outside_article')
    from event_workbench.news_events import current_member_owner
    if current_member_owner(c,run['id'],mention)!=event['member_id']:raise ValueError('review_mention_identity_changed')
    return doc,mention,states


def validate(record, source_documents):
    if not isinstance(record, dict) or record.get('contract_revision') != VERSION or not record.get('reviewed_at') or not record.get('reason'):
        raise ValueError('review_contract_or_provenance_missing')
    inputs = record.get('inputs')
    if not isinstance(inputs, list) or not inputs:
        raise ValueError('review_inputs_missing')
    participants, uncertainties, summaries, evidence = [], [], {}, []
    for item in inputs:
        source = item['source_row']
        if source not in source_documents or source in summaries:
            raise ValueError('review_source_outside_event_or_duplicate')
        original = source_documents[source]
        if original.get('available_at') and moment(original['available_at']) > moment(record['reviewed_at']):
            raise ValueError('review_uses_not_yet_available_material')
        doc = {k: original[k] for k in FIELDS}
        if item['document_sha'] != corpus.digest(doc):
            raise ValueError('review_document_hash_mismatch')
        facts = review_import.admit_elements(item['raw_answer'], doc)
        summaries[source] = facts['summary']
        proofs = []
        for q in facts.get('evidence_quotes', []):
            field = 'title' if q['section'] == 'title' else 'text'
            start = (doc[field] or '').find(q['quote'])
            if start < 0:
                raise ValueError('review_quote_not_located')
            proof = dict(q, start=start, end=start+len(q['quote']), source_row=source,
                         document_id=original.get('document_id'), document_sha=item['document_sha'],
                         available_at=original.get('available_at'))
            proofs.append(proof)
            if proof not in evidence:
                evidence.append(proof)
        for p in facts.get('participants', []):
            own = [proofs[i] for i in p['evidence_refs']]
            if not own or not any(p['name'] in q['quote'] for q in own):
                raise ValueError('subject_name_not_in_own_quote')
            participant = dict(name=p['name'], role=p['role'], evidence=own)
            if participant not in participants:
                participants.append(participant)
        uncertainties.extend(facts.get('uncertainties', []))
    primary = record.get('primary_source_row')
    if primary not in summaries:
        raise ValueError('summary_source_missing')
    if not participants and not uncertainties:
        raise ValueError('missing_subject_requires_reason')
    return dict(summary=dict(text=summaries[primary], evidence=evidence),
                subjects=dict(participants=participants, uncertainties=list(dict.fromkeys(uncertainties)),
                              reviewed_at=record['reviewed_at']),
                reason=record['reason'])


def overlay(event, projection, review_id):
    if 'industry_metadata' in projection:
        event['industry_metadata']=copy.deepcopy(projection['industry_metadata'])
        return
    if 'classification_scope' in projection:
        event['monitoring_scope']=copy.deepcopy(projection['classification_scope'])
        event['event_subjects']['standard_entity']=[m for m in event['monitoring_scope']['matches'] if m['decision']=='matched']
        event['classification_review']=dict(id=review_id,reason=projection['reason'],reviewed_at=projection['reviewed_at'],
            reviewer_type=projection.get('origin','codex_session'),financial_human_review='pending')
        return
    event['event_summary'] = copy.deepcopy(projection['summary'])
    subjects=copy.deepcopy(projection['subjects'])
    for participant in subjects.get('participants',[]):
        if 'evidence' in participant:continue
        for current in event.get('event_subjects',{}).get('participants',[]):
            if participant=={k:v for k,v in current.items() if k!='evidence'} and current.get('evidence'):
                participant['evidence']=copy.deepcopy(current['evidence'])
                break
    event['event_subjects'] = dict(event.get('event_subjects', {}), **subjects)
    if 'emotions' in projection:event['emotions']=copy.deepcopy(projection['emotions'])
    event['identification_review'] = dict(id=review_id, rule_version=VERSION, reason=projection['reason'],
                                         reviewer_type=projection.get('origin','codex_session'), financial_human_review='pending')
    if projection.get('reviewed_at'):event['identification_review']['reviewed_at']=projection['reviewed_at']


def assessment_signature(event):
    impacts = copy.deepcopy(event.get('warning_reasons'))
    for decision in (impacts or {}).get('decisions', []):
        for key in ('id', 'version', 'event_id', 'event_version', 'external_notification', 'change', 'active', 'delivery', 'legacy_notice_id'):
            decision.pop(key, None)
    value=dict(credibility=event.get('credibility'), emotions=event.get('emotions'),
               impacts=impacts, identification=event.get('identification_review'))
    if event.get('classification_review'):value['classification']=event['classification_review']
    if event.get('industry_metadata'):value['industry_metadata']=event['industry_metadata']
    return corpus.digest(value)


def classification(record, doc, mention):
    """Recheck only the saved candidates, independently of the object's identity."""
    from event_workbench.monitoring_consistency import admit_named_scope
    definitions=mention.get('classification_candidates') or list({m['definition']['definition_id']:m['definition'] for m in mention['monitoring_scope']['matches']}.values())
    content=review_import.content({k:doc[k] for k in FIELDS},definitions)
    units=dict(content['u']);body=mention['body']
    own=[r for r,text in content['u'] if r.startswith('B') and any(q['section']=='body' and q['quote'] in text for q in body['evidence_quotes'])]
    content['a']=[dict(i=mention['original_index'],s=body['summary'],q=own,x=mention.get('field_warnings',[]))]
    checked=admit_named_scope(record['raw_classification'],content)
    scope=checked['items'][0]
    if scope['status']=='quarantined':raise ValueError('classification_answer_rejected:'+corpus.js(scope.get('rejection')))
    raw=checked['original_named']['items'][0]
    for item in raw['matches'] or [raw['unmatched']]:
        if not set(item['source_refs']) & set(own):raise ValueError('classification_evidence_outside_mention')
    scope.update(semantic_review_status='session_reviewed' if record['origin']=='codex_session' else 'model_reviewed',
                 candidate_scope='supplied_candidates_only',candidate_ids=[d['definition_id'] for d in definitions])
    for match in scope['matches']:match['semantic_review_status']=scope['semantic_review_status']
    objects=[];seen=set()
    for item in record.get('business_objects',[]):
        d=next((d for d in definitions if d['definition_id']==item.get('definition_id')),None)
        refs=item.get('source_refs')
        if d is None or not isinstance(refs,list) or not refs or any(r not in units for r in refs):raise ValueError('classification_object_outside_candidates')
        key=(d['target_type'],d['target_code'])
        if key in seen or not item.get('business_name') or not item.get('reason'):raise ValueError('classification_object_description_required')
        if not set(refs)&set(own) or not any(d[k] in units[r] for k in ('target_name','target_code') if d.get(k) for r in refs):raise ValueError('classification_object_evidence_missing')
        seen.add(key)
        objects.append(dict(definition_id=d['definition_id'],target_name=d['target_name'],target_code=d['target_code'],target_type=d['target_type'],
            business_name=item['business_name'],reason=item['reason'],evidence=[dict(ref=r,quote=units[r],source_row=record['primary_source_row'],document_sha=record['document_sha'],
                start=doc['text' if r.startswith('B') else 'title'].find(units[r]),section='body' if r.startswith('B') else 'title') for r in refs],official_label_eligible=False))
    scope['business_objects']=objects
    return dict(classification_scope=scope,classification_input=content,input_sha=corpus.digest(content),reason=record['reason'])


def reviewed_classifications(c, run_id, source, document_sha, states, as_of):
    """Apply article-bound category reviews to an in-memory prediction, not old stages."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='dossier_reviews'").fetchone():return
    selected={}
    for row in c.execute("SELECT r.id,r.body,r.projection FROM dossier_reviews r WHERE r.run_id=? AND r.status IN ('accepted','article_only') ORDER BY created DESC,rowid DESC",(run_id,)):
        record=json.loads(row['body']);projection=json.loads(row['projection'])
        if 'classification_scope' not in projection or record.get('primary_source_row')!=source or record.get('document_sha')!=document_sha:continue
        mid=record.get('mention_id')
        if mid in selected or moment(record['reviewed_at'])>moment(as_of):continue
        owner=c.execute('SELECT event_id FROM news_event_members WHERE run_id=? AND mention_id=?',(run_id,mid)).fetchone()
        if not owner or owner[0]!=record['event_id']:continue
        selected[mid]=(row['id'],projection)
    for pool in ('all_mentions','pending_mentions','not_applicable_mentions','mentions'):
        for mention in (states.get('mentions',{}).get('result') or {}).get(pool,[]):
            if mention.get('id') in selected:
                from event_workbench.news_events import current_member_owner
                if current_member_owner(c,run_id,mention) is None:continue
                identity,projection=selected[mention['id']]
                mention['monitoring_scope']=copy.deepcopy(projection['classification_scope']);mention['classification_review_id']=identity
                mention['classification_candidates']=copy.deepcopy(projection['classification_input']['c'])


def apply(app, run_id, record, *, origin='codex_session'):
    """Record failed attempts too; successful publication and receipt are one transaction."""
    if origin not in ('codex_session','offline_model_fixture','model_response'):raise ValueError('unknown_dossier_review_origin')
    review_id = corpus.digest([run_id, record] if origin=='codex_session' else [run_id,record,origin])
    stored=dict(record,origin=origin)
    with closing(app.connect()) as c:
        c.execute(SCHEMA)
        c.commit()
        try:
            with c:
                c.execute('BEGIN IMMEDIATE')
                prior = c.execute('SELECT status FROM dossier_reviews WHERE id=? AND run_id=?', (review_id, run_id)).fetchone()
                if prior and prior['status'] in ('accepted','article_only'):
                    return dict(id=review_id, changed=False,status=prior['status'])
                run = app.run(c, run_id)
                snapshot = product_research._dossier(app, c, run, record['event_id'])
                old = snapshot['event']
                if corpus.digest(old) != record['base_body_sha256']:
                    raise ValueError('review_snapshot_changed')
                sources = {m['source_row'] for m in old.get('evidence_sources', {}).get('members', [])}
                if set(record['source_rows']) != sources or record['base_event_version'] != old['event_version']:
                    raise ValueError('review_event_scope_changed')
                article_only=record.get('article_only') is True
                classification_only=record.get('classification_only') is True
                if article_only and (not (record.get('emotion_only') is True or classification_only) or not record.get('mention_id')):raise ValueError('article_review_binding_required')
                if record.get('industry_only') is True:
                    from event_workbench.company_metadata import resolve
                    if record.get('contract_revision')!=VERSION or not record.get('reason') or not record.get('reviewed_at') or record.get('raw_emotions') is not None or classification_only or article_only:raise ValueError('industry_review_provenance_required')
                    if moment(record['reviewed_at'])>moment(corpus.now()):raise ValueError('industry_review_future')
                    metadata=resolve(product_research.business_matches(old),record['reviewed_at'])
                    if not metadata or any(m['metadata_sha']!=record.get('metadata_sha') for m in metadata):raise ValueError('industry_review_reference_mismatch')
                    projection=dict(industry_metadata=metadata,reason=record['reason'])
                elif classification_only:
                    if record.get('contract_revision')!=VERSION or not record.get('reviewed_at') or not record.get('reason') or record.get('raw_emotions') is not None:raise ValueError('classification_review_provenance_missing')
                    doc,mention,_=material_context(c,run,old,record)
                    if not moment(doc['available_at'])<=moment(record['reviewed_at'])<=moment(corpus.now()):raise ValueError('review_uses_not_yet_available_material')
                    if not article_only and (record['mention_id']!=old.get('assessment_source',{}).get('mention_id') or record['primary_source_row']!=old.get('assessment_source',{}).get('source_row')):raise ValueError('classification_review_requires_current_mention')
                    projection=classification(stored,doc,mention)
                    if not article_only and {o['id'] for o in product_research.objects(old)}!={o['id'] for o in product_research.objects(dict(monitoring_scope=projection['classification_scope']))}:
                        raise ValueError('classification_review_cannot_change_object_scope')
                elif record.get('emotion_only') is True:
                    if record.get('contract_revision')!=VERSION or not record.get('reviewed_at') or not record.get('reason') or not record.get('raw_emotions'):raise ValueError('emotion_review_provenance_missing')
                    projection=dict(summary=copy.deepcopy(old['event_summary']),subjects=copy.deepcopy(old['event_subjects']),reason=record['reason'])
                else:projection = validate(record, documents(c, run, sources))
                if record.get('raw_emotions') is not None:
                    from event_workbench import baseline_emotion
                    source=record['primary_source_row']
                    if not article_only and source!=old.get('assessment_source',{}).get('source_row'):raise ValueError('emotion_review_requires_current_source')
                    doc=documents(c,run,[source])[source]
                    if not moment(doc['available_at'])<=moment(record['reviewed_at'])<=moment(corpus.now()):raise ValueError('review_uses_not_yet_available_material')
                    if record.get('emotion_only') and record.get('document_sha')!=corpus.digest({k:doc[k] for k in FIELDS}):raise ValueError('review_document_hash_mismatch')
                    content=review_import.content({k:doc[k] for k in FIELDS},[])
                    target=old;index=0
                    if record.get('mention_id'):
                        doc,target,_=material_context(c,run,old,record);index=target['original_index']
                        if not article_only and record['mention_id']!=old.get('assessment_source',{}).get('mention_id'):raise ValueError('emotion_review_requires_current_mention')
                    matches=[dict(definition_id=m['definition']['definition_id']) for m in target.get('monitoring_scope',{}).get('matches',[]) if m.get('decision') in ('matched','m')]
                    matches=list({m['definition_id']:m for m in matches+[dict(definition_id=o['definition_id']) for o in target.get('monitoring_scope',{}).get('business_objects',[]) if o.get('definition_id')]}.values())
                    if not matches:raise ValueError('emotion_review_requires_saved_objects')
                    part=dict(status='ready',items=[dict(original_index=index,core=True,original=dict(matches=matches))])
                    checked=baseline_emotion.apply(record['raw_emotions'],dict(content=content),part)
                    emotions=checked['items'][0]['emotions']
                    if any(x['status']=='failed' for x in emotions):raise ValueError('emotion_review_rejected')
                    projection['emotions']=[dict(x,source_row=source,reviewed_at=record['reviewed_at'],reviewer_type=origin,**(dict(mention_id=record['mention_id']) if record.get('mention_id') else {})) for x in emotions]
                projection['reviewed_at']=record['reviewed_at']
                if origin!='codex_session':projection['origin']=origin
                if article_only:
                    projection={k:v for k,v in projection.items() if k in ('emotions','reason','reviewed_at','origin','classification_scope','classification_input','input_sha')}
                    c.execute('INSERT INTO dossier_reviews VALUES(?,?,?,?,?,?,?,?)',
                              (review_id,run_id,old['member_id'],'article_only',corpus.js(stored),corpus.js(projection),'本篇补审，不改写档案快照',corpus.now()))
                    return dict(id=review_id,changed=False,recorded=True,status='article_only')
                updated = copy.deepcopy(old)
                overlay(updated, projection, review_id)
                updated['assessment_version'] = old.get('assessment_version', 0) + 1
                updated['material_version'] = old['material_version'] + 1
                updated['assessment_signature'] = assessment_signature(updated)
                previous = c.execute('SELECT signature FROM news_event_versions WHERE run_id=? AND event_id=? AND material_version=?',
                                     (run_id, old['member_id'], old['material_version'])).fetchone()
                if previous is None:
                    raise ValueError('review_snapshot_not_saved')
                c.execute('INSERT INTO news_event_versions VALUES(?,?,?,?,?,?,?)',
                          (run_id, old['member_id'], old['event_version'], updated['material_version'], previous['signature'], corpus.js(updated), corpus.now()))
                c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?', (corpus.js(updated), run_id, old['member_id']))
                c.execute('INSERT OR REPLACE INTO dossier_reviews VALUES(?,?,?,?,?,?,?,?)',
                          (review_id, run_id, old['member_id'], 'accepted', corpus.js(stored), corpus.js(projection), '', corpus.now()))
                c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?', (run_id,))
                return dict(id=review_id, changed=True, material_version=updated['material_version'])
        except (ValueError, KeyError, TypeError, IndexError) as error:
            with c:
                c.execute('INSERT OR REPLACE INTO dossier_reviews VALUES(?,?,?,?,?,?,?,?)',
                          (review_id, run_id, record.get('event_id', ''), 'rejected', corpus.js(stored), None, str(error), corpus.now()))
            raise


def restore(c, run_id, event):
    """Preserve a reviewed projection on resync only for its exact saved material scope."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='dossier_reviews'").fetchone():
        return
    sources = {m['source_row'] for m in event.get('evidence_sources', {}).get('members', [])}
    restored={}
    for row in c.execute("SELECT id,body,projection FROM dossier_reviews WHERE run_id=? AND event_id=? AND status='accepted' ORDER BY created DESC,rowid DESC", (run_id, event['member_id'])):
        record = json.loads(row['body'])
        if record['base_event_version'] == event['event_version'] and set(record['source_rows']) == sources:
            projection=json.loads(row['projection']);kind='industry' if 'industry_metadata' in projection else 'classification' if 'classification_scope' in projection else 'elements'
            if kind in restored:continue
            if kind=='classification' and (record['mention_id']!=event.get('assessment_source',{}).get('mention_id') or record['primary_source_row']!=event.get('assessment_source',{}).get('source_row')):continue
            restored[kind]=(projection,row['id'])
    for kind in ('elements','classification','industry'):
        if kind in restored:overlay(event,*restored[kind])


def review_case(c, app, data, case, index, run_id, event):
    """New case replays use saved, revalidated responses; existing snapshots stay intact."""
    path = corpus.ROOT / 'data/dossier-case-elements.json'
    if not path.exists():
        return
    step = case['steps'][index]
    records = json.loads(path.read_text(encoding='utf-8'))
    record = next((r for r in records if r['case_id'] == case['id'] and r['case_event'] == step['event']
                   and r['case_material'] == step['material']), None)
    if record is None:
        return
    sources = {m['source_row'] for m in event['evidence_sources']['members']}
    projection = validate(record, documents(c, app.run(c, run_id), sources))
    overlay(event, projection, corpus.digest(record))
