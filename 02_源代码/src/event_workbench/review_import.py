"""Import saved session judgments through existing admission gates; no transport."""
import copy
import json
import re
from event_workbench import news_mvp, news_blocks, compact_model_io, current_verification, auto_impact
from event_workbench import unified_pipeline as core
from event_workbench import review_scoring

VERSION='session-review-20260927-v1'

def receive_credibility_review(app, run_id, record, catalog, *, origin):
    """Receive a saved claim/group assessment; transport never controls the score."""
    from event_workbench.credibility_review import apply
    return apply(app,run_id,dict(record,origin=origin),catalog)


def receive_impact_review(app, run_id, record, *, origin, cause='new_material', calendar=None):
    """One impact answer gate for session and model transports; no external call."""
    from event_workbench.alert_review import apply
    return apply(app, run_id, record, origin=origin, cause=cause, calendar=calendar)

def content(document, definitions):
    rep,_=compact_model_io.source_representation(document)
    return dict(rep,c=definitions,unit_roles=dict(core=[u for u,t in rep['u'] if u.startswith('B')],context_only=[u for u,t in rep['u'] if u.startswith('T')]))

def admit_extraction(raw_answer, document, source_content, mapping=None):
    """Session responses and model transport use the same recognition gate."""
    from event_workbench.competition_enrichment import admit_baseline
    from event_workbench import news_business,news_joint
    wire=compact_model_io.common.strict_json(raw_answer)
    result=news_joint.admit_recognition(raw_answer,document,source_content,mapping) if isinstance(wire,dict) and wire.get('schema_version')==news_joint.VERSION else news_business.admit(raw_answer,document,source_content,mapping) if isinstance(wire,dict) and wire.get('schema_version')==news_business.VERSION else admit_baseline(raw_answer, document, source_content, mapping)
    result['candidate_input']=dict(definition_ids=[d['definition_id'] for d in source_content.get('c',[])],
        input_sha=core.digest(source_content),selection='supplied_to_analysis_not_retrieval_accuracy')
    return result


def admit_elements(raw_answer, document):
    """Supplement a saved identity with the existing named element contract."""
    checked = news_blocks.admit_items(raw_answer, document, content(document, []))
    if len(checked['items']) != 1 or not checked['items'][0]['accepted']:
        raise ValueError('element_review_rejected:' + json.dumps(checked, ensure_ascii=False))
    return checked['items'][0]['projection']


def admit_joint_review(raw_answer, context, prepared, *, processor, origin, unadmitted_matters=None):
    """Session, offline fixture and actual transport share one compact/V3 gate."""
    from event_workbench.news_joint import receive_joint
    if origin not in ('codex_session','model_response','offline_model_fixture'):raise ValueError('unknown_review_origin')
    return receive_joint(raw_answer,context,prepared,origin=origin,unadmitted_matters=unadmitted_matters)


def validate_review(document, definitions, raw_answer, article_claims, scoring, evidence_catalog, *, reviewer_type='codex_session', cutoff=None, raw_emotions=None):
    """All raw judgments survive; a failed stage never silently becomes ready."""
    c=content(document,definitions)
    admitted=admit_extraction(raw_answer,document,c)
    if admitted['rejected'] and not admitted['accepted']:raise ValueError('extraction_rejected:'+json.dumps(admitted['rejected'],ensure_ascii=False))
    if raw_emotions is not None:
        from event_workbench import baseline_emotion
        admitted=baseline_emotion.apply(raw_emotions,dict(content=c),admitted)
    extracted=news_mvp.extraction(document,[admitted]);jid=core.digest(document)
    mentions=news_mvp.mentions(extracted,jid)
    raw=json.loads(article_claims)
    indices={str(x['mention']) for x in raw['claims']}
    allowed={i['original_index'] for i in admitted['accepted']}
    if any(int(i) not in allowed for i in indices):raise ValueError('article_claim_outside_review')
    batch=dict(mapping=dict(mentions={i:dict(original_index=int(i),event=dict(field_warnings=[])) for i in indices},
        materials={'own':dict(field='text',text=document['text'],start=0,relation='own_document',document=dict(local_id=jid,document=document))}),
        content=dict(candidates=[dict(mention=int(i),materials=['own']) for i in indices]))
    batch['mapping']['materials']['own_title']=dict(field='title',text=document.get('title') or '',start=0,relation='own_document',document=dict(local_id=jid,document=document))
    checked,scores=admit_claims(document,mentions,batch,article_claims,scoring,evidence_catalog,
                              reviewer_type=reviewer_type,cutoff=cutoff)
    return dict(content=c,admission=admitted,extract=extracted,mentions=mentions,verification=checked,scores=scores)


def admit_claims(document, mentions, batch, raw_claims, scoring, evidence_catalog, *, reviewer_type, cutoff):
    """Shared claim binding and computed scores for saved and transported answers."""
    jid=core.digest(document)
    checked=current_verification.check(raw_claims,batch)
    if checked['issues'] or checked['quarantined'] or any(x['quarantined_assessments'] for x in checked['valid']):raise ValueError('claim_review_rejected')
    scores=[]
    if len(scoring)!=len(checked['valid']):raise ValueError('scoring_claim_count')
    for claim,checks in zip(checked['valid'],scoring):
        if checks['claim']!=claim['claim']:raise ValueError('scoring_claim_binding')
        result=review_scoring.score(document,checks,evidence_catalog,cutoff=cutoff,basis=claim['basis'])
        if 'fact_refs' in checks:
            refs=checks['fact_refs'];body=next((m['body'] for m in mentions['mentions'] if m['original_index']==claim['original_mention_index']),None)
            if body is None:raise ValueError('claim_fact_mention_not_admitted')
            if not isinstance(refs,list) or not refs or len(set(refs))!=len(refs):raise ValueError('claim_fact_refs_required')
            for ref in refs:
                match=re.fullmatch(r'(details|times)\[(\d+)\]',ref)
                if not match and ref!='status':raise ValueError('claim_fact_path_invalid')
                if match and int(match[2])>=len(body.get(match[1],[])):raise ValueError('claim_fact_path_outside_mention')
                value=body['status'] if ref=='status' else body[match[1]][int(match[2])] if match else None
                literal=(value or {}).get('phase' if ref=='status' else 'text' if match[1]=='times' else 'value_text')
                if not literal or literal not in claim['basis']['quote']:raise ValueError('claim_fact_not_in_own_quote')
            claim['fact_refs']=copy.deepcopy(refs)
        claim['article_only']=checks.get('article_only',False)
        claim.update(result,claim_id='claim-'+core.digest([jid,claim['original_mention_index'],claim['claim']]),verdict=review_scoring.reviewed_verdict(checks,result),source=reviewer_type,claim_role=checks.get('role','current_core'))
        for a in claim['valid_assessments']:a.update(reviewer_type=reviewer_type,independent_confirmation='not_established')
        scores.append(result)
    for mention in mentions['mentions']:
        claims=[c for c in checked['valid'] if c['original_mention_index']==mention['original_index']]
        if mention['body'].get('status',{}).get('context')=='focus' and claims and all(c['claim_role']=='historical' for c in claims):
            raise ValueError('focal_mention_has_only_historical_claims:'+str(mention['original_index']))
    return checked,scores

def import_review(pipeline, document, definitions, record, evidence_catalog):
    return receive_review(pipeline, document, definitions, record, evidence_catalog, origin='codex_session')


def receive_review(pipeline, document, definitions, record, evidence_catalog, *, origin):
    """Receive already obtained JSON. This entrypoint never sends a model request."""
    if origin not in ('codex_session', 'model_response', 'offline_model_fixture'):
        raise ValueError('unknown_review_origin')
    is_v3=isinstance(record,dict) and any(s.get('schema_version')==review_scoring.CONTRACT for s in record.get('scoring',[]))
    identity = VERSION + ':' + core.digest([record, origin, evidence_catalog] if is_v3 else [record, origin])
    with pipeline.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS review_receipts(id TEXT PRIMARY KEY,origin TEXT,status TEXT,reason TEXT,body TEXT)')
    try:
        if not isinstance(record, dict):
            raise ValueError('review_record_must_be_object')
        if record.get('document_sha') != core.digest(document):
            raise ValueError('review_document_hash_mismatch')
        if record.get('contract_revision', news_mvp.VERSION) != news_mvp.VERSION:
            raise ValueError('review_contract_mismatch')
        if not record.get('source_row') or not record.get('cutoff') or not record.get('assessed_at'):
            raise ValueError('review_provenance_missing')
        from event_workbench.news_materials import moment
        if moment(record['cutoff']) > moment(record['assessed_at']):
            raise ValueError('review_cutoff_after_assessment')
        if origin!='codex_session' and any(s.get('schema_version')!=review_scoring.CONTRACT for s in record['scoring']):
            raise ValueError('model_response_requires_v3_scoring_features')
        result=validate_review(document,definitions,record['raw_extraction'],record['raw_claims'],record['scoring'],evidence_catalog,reviewer_type=origin,cutoff=record['cutoff'],raw_emotions=record.get('raw_emotions'))
    except (ValueError, TypeError, KeyError, IndexError) as error:
        with pipeline.connect() as c:
            c.execute('INSERT OR REPLACE INTO review_receipts VALUES(?,?,?,?,?)', (identity, origin, 'rejected', str(error), json.dumps(record, ensure_ascii=False)))
        raise
    stages={s:dict(status='waiting',reason='未审阅',result=None) for s in core.STAGES}
    for name in ('extract','mentions'):stages[name]=dict(status=result['admission']['status'],reason='原始答复经逐事项校验；失败事项保留原因' if result['admission']['rejected'] else '原始答复经原文与对象范围校验',result=result[name])
    stages['verification']=dict(status='ready',reason='逐主张审阅；引文校验不等于专家真值',result=dict(verification=dict(checked=result['verification']),source_aware_impact_input={},reviewer_type=origin,assessment_cutoff=record['cutoff'],assessed_at=record['assessed_at']))
    # Keep existing session import identities so old responses remain idempotent.
    legacy_identity = VERSION+':'+core.digest(record) if origin == 'codex_session' and not is_v3 else identity
    provenance=dict(kind=origin,transport_executed=False,human_financial_review='pending',raw_answer=record,evidence_catalog=copy.deepcopy(evidence_catalog),score_rule=review_scoring.VERSION)
    bundle=dict(document=document,legacy_identity=legacy_identity,stages=stages,provenance=provenance)
    with pipeline.connect() as c:saved=c.execute('SELECT body FROM imports WHERE id=?',(legacy_identity,)).fetchone()
    if saved:
        # Identical old responses reuse their stored result; changing a formula never rewrites it.
        previous=json.loads(saved[0]);verified=previous['stages']['verification']['result']['verification']['checked']
        for name in ('extract','mentions'):result[name]=copy.deepcopy(previous['stages'][name]['result'])
        result['admission']=copy.deepcopy(result['extract']['admission']['parts'][0])
        result['verification']=verified
        result['scores']=[{k:v for k,v in claim.items() if k.startswith('score') or k in ('short_reason','display','raw_score','applied_caps','probability')} for claim in verified['valid']]
        result['reused_legacy_result']=True;jid=core.digest(document)
    else:jid=pipeline.import_legacy(bundle)
    with pipeline.connect() as c:
        c.execute('INSERT OR REPLACE INTO review_receipts VALUES(?,?,?,?,?)', (identity, origin, 'partial' if result['admission']['status']=='partial' else 'accepted', json.dumps(result['admission']['rejected'],ensure_ascii=False) if result['admission']['rejected'] else '', json.dumps(record, ensure_ascii=False)))
    return jid,result


def receive_relation_review(pipeline,jid,record,*,origin):
    """Persist a saved answer against the current program-generated candidates."""
    from event_workbench.live_stage_adapters import Adapters
    if origin not in ('codex_session','model_response','offline_model_fixture'):raise ValueError('unknown_review_origin')
    identity='relation-review-v2:'+core.digest([jid,record,origin])
    with pipeline.lock,pipeline.connect() as c:
        c.execute('CREATE TABLE IF NOT EXISTS review_receipts(id TEXT PRIMARY KEY,origin TEXT,status TEXT,reason TEXT,body TEXT)')
        c.execute('BEGIN IMMEDIATE')
        try:
            states=pipeline._states(c,jid);candidates=states['relation_candidates']
            if candidates['status'] not in core.GOOD or core.digest(candidates['result'])!=record['input_sha']:raise ValueError('relation_candidates_changed')
            batch=candidates['result']['batch']
            current={m['id']:m for row in c.execute('SELECT id FROM jobs') for m in (pipeline._states(c,row[0])['mentions']['result'] or {}).get('mentions',[])}
            from event_workbench.aggregation_v2 import endpoint_fingerprint
            for d in batch['content']['docs']:
                for index,event in enumerate(d['events']):
                    mid=batch['mapping'][d['id']+':'+str(index)]
                    if mid not in current or endpoint_fingerprint(current[mid]['body'])!=endpoint_fingerprint(event):raise ValueError('relation_endpoint_changed')
            envelope=dict(raw_answer=record['raw_answer'],complete=True,identity=identity,provider_sha256=core.digest(record['raw_answer']))
            result=Adapters.relation_check(envelope,batch)
            if result['processing_status']=='pending' and (not result['proposals'] or result['isolated'] or result['missing_candidates']):raise ValueError('relation_review_pending:'+json.dumps({k:result[k] for k in ('isolated','missing_candidates','per_mention')},ensure_ascii=False))
            bind_relation_ids(result,jid)
            old=c.execute('SELECT body FROM imports WHERE id=?',(identity,)).fetchone()
            if old:
                result=json.loads(old[0])['result']
                if states['relations']['status'] not in ('waiting','stale'):return result
                # Endpoints and candidate binding passed again; an explicit resubmission
                # may restore the saved answer after re-analysis cleared this stage.
            status='ready' if result['processing_status']=='complete' else 'partial'
            pipeline._write(c,jid,'relations',status,'共同入口校验后的逐提及关系答复',pipeline._signature(c,jid,'relations',states),result,dict(kind=origin,transport_executed=False,identity=identity))
            impact=states['impact']
            if impact['status'] in core.GOOD:pipeline._write(c,jid,'impact','stale','关系审阅变化，原影响仅保留为历史',impact['input_sha'],impact['result'],impact['provenance'])
            c.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?)',(identity,core.digest(record),json.dumps(dict(record=record,origin=origin,result=result),ensure_ascii=False)))
            receipt=('accepted' if status=='ready' else 'partial','')
        except (ValueError,TypeError,KeyError,IndexError) as error:
            receipt=('rejected',str(error));result=None
        c.execute('INSERT OR REPLACE INTO review_receipts VALUES(?,?,?,?,?)',(identity,origin,*receipt,json.dumps(record,ensure_ascii=False)))
    if result is None:raise ValueError(receipt[1])
    return result


def bind_relation_ids(result, jid):
    """Business relation identity depends on judgments, not provider/receipt names."""
    for p in result.get('proposals',[]):
        p['id']=jid[:16]+'-relation-'+core.digest([p['member_ids'],p['raw'],p.get('aggregation')])
    return result

def validate_impact(job, raw_answer, official_targets):
    batch=auto_impact.prepare(job)
    batch['official_targets']=official_targets
    checked=auto_impact.check(raw_answer,batch,dict(version=VERSION))
    if checked['issues'] or any(a['issues'] or a['invalid_effects'] for a in checked['assessments']):raise ValueError('impact_review_rejected:'+json.dumps(checked,ensure_ascii=False))
    return dict(record=dict(checked=checked),reviewer_type='codex_session',raw_answer=raw_answer)
