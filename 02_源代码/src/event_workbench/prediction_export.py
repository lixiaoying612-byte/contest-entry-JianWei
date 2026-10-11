"""Finite, read-only saved predictions. Never read official answer tables or send models."""
import argparse
import hashlib
import json
import sqlite3
from collections import Counter
from contextlib import closing
from pathlib import Path

from event_workbench import training_corpus as corpus, unified_pipeline as core
from event_workbench import business_evaluation as evaluation, product_research, product_downstream
from event_workbench import dossier_review, news_events, news_outcomes
from event_workbench.news_materials import moment

VERSION='saved-prediction-export-20261006-v3-retained-archives'
CORE=('article_file_id','article_title','article_publish_time','article_source','content')
TAIL=('industry_code','industry_name','event_name','event_emotion','event_impact_analysis','duplication_id')
COLUMNS=dict(company=CORE+('trading_code','secu_abbr')+TAIL,industry=CORE+TAIL)
TABLES={'sqlite_master','json_each','replay_runs','replay_scope_rows','source_rows','replay_rows','documents','contents',
        'jobs','stages','attempts','product_stage_versions','news_event_members','replay_events','impact_reviews','dossier_reviews','review_receipts','withdrawals'}


def prediction_connection(path):
    c=corpus.connect(path,readonly=True)
    # A future helper accidentally reading answer columns fails, even in a copied database.
    c.set_authorizer(lambda action,table,column,db,trigger:
        sqlite3.SQLITE_DENY if action==sqlite3.SQLITE_READ and table not in TABLES else sqlite3.SQLITE_OK)
    c.execute('BEGIN')
    return c


def source_metadata(c,run,source):
    row=c.execute('''SELECT r.row_key,r.article_id_raw,r.published_raw,r.document_id,p.available_at,p.split,p.guard
        FROM source_rows r JOIN replay_rows p ON p.row_id=r.id
        JOIN replay_scope_rows s ON s.source_row=r.row_key WHERE s.run_id=? AND r.row_key=?''',(run,source)).fetchone()
    if row is None:raise ValueError('source_outside_run')
    if row['guard']!='allowed' or row['split'] not in ('train','train_development','public_primary','case','user_input'):
        raise ValueError('protected_or_non_development_source')
    return dict(row)


def candidate_trace(document_id,extraction):
    parts=extraction.get('admission',{}).get('parts',[])
    if parts and all('candidate_input' in p for p in parts):
        return dict(document_id=document_id,status='recorded',
            selection='supplied_to_analysis_not_retrieval_accuracy',
            definition_ids=sorted({d for p in parts for d in p['candidate_input']['definition_ids']}))
    requests=extraction.get('source_prepared',{}).get('extraction_input',{}).get('requests',[])
    # An empty projection placeholder is not the original candidate input.
    return dict(document_id=document_id,status='recorded' if requests and all('c' in r.get('content',{}) for r in requests) else 'not_saved',
        definition_ids=sorted({d['definition_id'] for r in requests for d in r.get('content',{}).get('c',[])}))


def saved_candidate_trace(c,source,jid,extraction,tables):
    trace=candidate_trace(source,extraction)
    if trace['status']=='recorded' or 'attempts' not in tables:return trace
    stage=c.execute("SELECT provenance,input_sha FROM stages WHERE job=? AND stage='extract'",(jid,)).fetchone()
    if not stage:return trace
    aid=json.loads(stage['provenance'] or '{}').get('request_identity')
    if not aid:return trace
    attempt=c.execute("SELECT job,stage,request,input_sha,status FROM attempts WHERE id=?",(aid,)).fetchone()
    if not attempt:return trace
    try:
        request=json.loads(attempt['request']);prepared=request['prepared'];doc=prepared['plan']['document']
        if (attempt['job']!=jid or attempt['stage']!='extract' or attempt['input_sha']!=stage['input_sha']
            or prepared.get('source_row',source)!=source or core.digest(doc)!=jid
            or core.digest(dict(stage='extract',document=doc,request=request))!=aid):
            raise ValueError('candidate_attempt_binding_changed')
        trace=candidate_trace(source,dict(source_prepared=prepared))
        trace.update(provenance='bound_saved_attempt',attempt_id=aid,attempt_status=attempt['status'],
                     selection='prepared_for_analysis_not_proof_of_delivery_or_accuracy')
        return trace
    except (ValueError,KeyError,TypeError):
        return dict(document_id=source,status='binding_invalid',definition_ids=[])


def saved_archives(c,run,document_sha,exported_at):
    """Archive identity only; never reuse an old archive as a fresh prediction."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='replay_events'").fetchone():return []
    run_row=c.execute('SELECT * FROM replay_runs WHERE id=?',(run,)).fetchone()
    rows=c.execute('''SELECT DISTINCT e.id,e.body FROM replay_events e,
        json_each(e.body,'$.evidence_sources.members') m
        WHERE e.run_id=? AND json_extract(m.value,'$.document_id')=?
        AND json_extract(e.body,'$.record_kind')='event' ORDER BY e.id''',(run,document_sha))
    result=[]
    for row in rows:
        event=json.loads(row['body'])
        if not product_research.allowed(None,c,run_row,event):continue
        sources=product_research.event_sources(event)
        if not sources:continue
        try:
            if any(moment(source_metadata(c,run,s)['available_at'])>moment(exported_at) for s in sources):continue
        except ValueError:continue
        result.append(dict(event_id=row['id'],material_version=event.get('material_version'),prediction_labels_reused=False))
    return result


def bound_impact(c,run,entry,mention,states,event_id,documents,exported_at):
    claims=product_downstream.credibility((states.get('verification') or {}).get('result'),mention['original_index'])['claims']
    ids={q['claim_id'] for q in claims if q.get('claim_id')};selected={};skipped=[]
    tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    records=c.execute("SELECT id,projection FROM impact_reviews WHERE run_id=? AND event_id=? AND status IN ('accepted','historical_only','article_only') ORDER BY created DESC,rowid DESC",(run,event_id)).fetchall() if 'impact_reviews' in tables and event_id else []
    for record in records:
        for d in json.loads(record['projection']):
            bound={q.get('claim_id') for q in d.get('bound_claims',[])}
            if not bound or not bound<=ids or d['object_id'] in selected:continue
            reason=None
            if moment(d['impact_assessed_at'])>moment(exported_at):reason='assessment_after_export_time'
            for proof in d.get('impact_proofs',[]):
                source=proof.get('source_row');doc=documents.get(source)
                if doc is None:reason='evidence_outside_export_manifest';break
                if moment(doc['available_at'])>moment(entry['available_at']):reason='later_material_not_article_prediction';break
                if proof.get('document_sha')!=doc['job_id']:reason='evidence_content_changed';break
            if not d.get('impact_proofs'):reason='missing_impact_proof'
            if reason:skipped.append(dict(review_id=record['id'],object_id=d['object_id'],reason=reason));continue
            selected[d['object_id']]=dict(d,impact_review_id=record['id'])
    # Reuse legacy checked per-mention results; never borrow the latest dossier judgment.
    complete={s:states.get(s,dict(status='waiting',result=None)) for s in core.STAGES}
    legacy=news_events.auto_decisions(event_id or '',mention,complete,news_events._claims(complete,mention))
    for decision in legacy:
        definitions=[x['definition'] for x in mention.get('monitoring_scope',{}).get('matches',[]) if x.get('definition',{}).get('target_name')==decision.get('target')]
        for d in definitions:
            oid=('industry:' if d['target_type']=='industry' else 'stock:')+str(d['target_code'])
            proofs=[p for effect in decision.get('effects',[]) for p in effect.get('proofs',[])]
            # Legacy effects lack the full time envelope; only own-document proofs qualify.
            if proofs and all(p.get('document_id')==entry['job_id'] for p in proofs):selected.setdefault(oid,decision)
    return list(selected.values()),skipped


def reviewed_emotions(c,run,event_id,source,document_sha,fallback,exported_at,mention_id=None):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='dossier_reviews'").fetchone():return fallback,None
    rows=c.execute("SELECT id,body,projection FROM dossier_reviews WHERE run_id=? AND event_id=? AND status IN ('accepted','article_only') ORDER BY created DESC,rowid DESC",(run,event_id))
    for r in rows:
        record=json.loads(r['body']);projection=json.loads(r['projection'])
        if record.get('primary_source_row')!=source or 'emotions' not in projection:continue
        if record.get('mention_id') and record['mention_id']!=mention_id:continue
        if record.get('document_sha')!=document_sha or moment(record['reviewed_at'])>moment(exported_at):continue
        emotions=projection['emotions']
        if any(x.get('source_row')!=source for x in emotions):continue
        return emotions,r['id']
    return fallback,None


def duplicate_links(documents,states,notes):
    """Document duplication is narrower than event membership; all mentions must match."""
    raw={d['job_id']:{k:d[k] for k in dossier_review.FIELDS} for d in documents.values()}
    guarded=product_downstream.guarded_states(states,raw)
    owners={m['id']:jid for jid,ss in states.items() for m in evaluation.checked_mentions(ss) if m.get('id')}
    proposals=[p for ss in guarded.values() if ss['relations']['status'] in core.GOOD for p in (ss['relations'].get('result') or {}).get('proposals',[])]
    # Only direct comparisons are exported; no transitive article clustering is invented.
    pairs={}
    for p in proposals:
        if p.get('status')!='model_tentative' or p.get('issues') or p.get('aggregation',{}).get('operation')!='MATERIAL_ONLY' or not p.get('original_proofs'):continue
        try:
            if len(p['member_ids'])!=2 or not set(p['member_ids'])<=owners.keys() or not {owners[m] for m in p['member_ids']}<={q.get('document_id') for q in p['original_proofs']}:continue
            for proof in p['original_proofs']:news_events.dossiers.verify_relation_proof(proof,{j:dict(document=d) for j,d in raw.items()})
        except (ValueError,KeyError,TypeError):continue
        pairs[frozenset(p['member_ids'])]=p
    previous=[];links={}
    for source,d in sorted(documents.items(),key=lambda x:(moment(x[1]['available_at']),x[0])):
        ms=evaluation.checked_mentions(states[d['job_id']]);mids={m.get('id') for m in ms}
        ss=states[d['job_id']]
        parts=(ss['extract'].get('result') or {}).get('admission',{}).get('parts',[])
        complete=not notes.get(source) and bool(parts) and all(p.get('status')=='ready' and not p.get('rejected') for p in parts) and all(ss[s]['status'] in ('ready','cached') for s in ('extract','mentions'))
        for other,old,old_mids,old_complete in previous:
            if d['article_id_raw']==old['article_id_raw']:continue
            exact=bool(d['text'].strip()) and (d['title'],d['text'])==(old['title'],old['text'])
            matched={frozenset((a,b)) for a in mids for b in old_mids if a!=b and frozenset((a,b)) in pairs}
            covered=complete and old_complete and None not in mids|old_mids and mids and len(mids)==len(old_mids) and all(sum(a in p for p in matched)==1 for a in mids|old_mids)
            if exact or covered:
                links[source]=dict(article_file_id=old['article_id_raw'],source_row=other,method='exact_title_and_body' if exact else 'all_mentions_material_only',
                    relation_ids=[] if exact else sorted(pairs[p]['id'] for p in matched));break
        previous.append((source,d,mids,complete and not notes.get(source)))
    return links


def export_saved(database,manifest):
    if manifest.get('split')!='development' or not isinstance(manifest.get('documents'),list) or not manifest['documents']:
        raise ValueError('finite_development_manifest_required')
    sources=[x['source_row'] for x in manifest['documents']]
    if len(sources)!=len(set(sources)):raise ValueError('duplicate_manifest_source')
    exported_at=corpus.now()
    output=dict(version=VERSION,manifest=manifest,exported_at=exported_at,knowledge='current_saved_stages_with_article_time_evidence',
        status_scope='recognition_and_review_coverage; field_gaps_and_pipeline_disposition_are_reported_separately',
        columns={k:list(v) for k,v in COLUMNS.items()},company=[],industry=[],lineage=[],documents=[],candidates=[])
    with closing(prediction_connection(database)) as c:
        run=c.execute('SELECT * FROM replay_runs WHERE id=?',(manifest['run_id'],)).fetchone()
        if run is None:raise ValueError('unknown_run')
        tables={r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        docs={};states={};notes={};predictions={}
        for requested in manifest['documents']:
            source=requested['source_row']
            try:
                meta=source_metadata(c,run['id'],source)
                if moment(meta['available_at'])>moment(exported_at):raise ValueError('material_after_export_time')
                doc=dossier_review.documents(c,run,[source])[source];raw={k:doc[k] for k in dossier_review.FIELDS};jid=core.digest(raw)
                if requested.get('document_sha') and requested['document_sha']!=jid:raise ValueError('manifest_content_changed')
            except ValueError as error:
                output['documents'].append(dict(document_id=source,status='failed',labels=[],disposition=dict(code='blocked',reason=str(error))));continue
            docs[source]=dict(meta,**doc,job_id=jid)
            saved={r['stage']:dict(status=r['status'],reason=r['reason'],result=json.loads(r['result']) if r['result'] else None)
                   for r in c.execute('SELECT stage,status,reason,result FROM stages WHERE job=?',(jid,))} if 'stages' in tables else {}
            dossier_review.reviewed_classifications(c,run['id'],source,jid,saved,exported_at)
            states[jid]={s:saved.get(s,dict(status='waiting',result=None)) for s in core.STAGES}
            prediction=evaluation.prediction_from_states(source,saved)
            prediction['pipeline_stages']={s:dict(status=states[jid][s]['status'],reason=states[jid][s].get('reason')) for s in core.STAGES}
            disposition=news_outcomes.saved(c,jid,source_row=source);prediction['disposition']=disposition
            notes[source]=disposition.get('review_note') or ''
            if disposition.get('partial_analysis'):prediction['status']='partial_success'
            prediction.update(job_id=jid,article_file_id=meta['article_id_raw'],document_sha=jid,review_note=notes[source])
            predictions[source]=prediction;output['documents'].append(prediction)
            output['candidates'].append(saved_candidate_trace(c,source,jid,(saved.get('extract') or {}).get('result') or {},tables))
        withdrawn={r[0] for r in c.execute('SELECT id FROM withdrawals')} if 'withdrawals' in tables else set()
        for ss in states.values():
            for p in (ss['relations'].get('result') or {}).get('proposals',[]):
                if p.get('id') in withdrawn:p['status']='withdrawn'
        links=duplicate_links(docs,states,notes)
        for source,doc in docs.items():
            ss=states[doc['job_id']];prediction=predictions[source]
            prediction['saved_archives']=saved_archives(c,run['id'],doc['job_id'],exported_at)
            event_ids=sorted(a['event_id'] for a in prediction['saved_archives'])
            for label in prediction['labels']:
                mention=next(m for i,m in enumerate(evaluation.checked_mentions(ss)) if m.get('original_index',i)==label['original_index'])
                matches=[x for x in product_research.business_matches(mention) if (x.get('definition') or x).get('definition_id')==label['definition_id'] and x.get('decision') in ('matched','m')]
                from event_workbench.news_events import current_member_owner
                owner=current_member_owner(c,run['id'],mention) if 'news_event_members' in tables else None
                event_id=owner if owner in event_ids else None
                decisions,skipped=bound_impact(c,run['id'],doc,mention,ss,event_id,docs,exported_at) if mention.get('id') else ([],[])
                emotions,emotion_review=reviewed_emotions(c,run['id'],event_id,source,doc['job_id'],mention.get('emotions',[]),exported_at,mention.get('id'))
                from event_workbench.company_metadata import resolve as company_metadata
                view=product_research.business_output(dict(monitoring_scope=dict(matches=matches),industry_metadata=company_metadata(matches,exported_at),emotions=emotions,warning_reasons=dict(decisions=decisions)))['label_rows'][0]
                label.update(emotion=view['event_emotion'],emotion_status=view['emotion_status'])
                row=dict(article_file_id=doc['article_id_raw'],article_title=doc['title'],article_publish_time=doc['published_raw'],article_source=doc['source'],content=doc['text'],
                    **{k:view.get(k) for k in ('trading_code','secu_abbr','industry_code','industry_name','event_name','event_emotion')},
                    event_impact_analysis=view['event_impact_analysis'] if view['impact']['status']=='available' else None,
                    duplication_id=links.get(source,{}).get('article_file_id'))
                kind=view['target_type'];index=len(output[kind]);output[kind].append({k:row[k] for k in COLUMNS[kind]})
                gaps=[k for k in COLUMNS[kind] if row.get(k) in (None,'') and k!='duplication_id']
                output['lineage'].append(dict(sheet=kind,row_index=index,source_row=source,job_id=doc['job_id'],mention_id=mention.get('id'),original_index=label['original_index'],
                    event_id=event_id,definition_id=label['definition_id'],target_id=label['target_id'],classification=view['classification'],
                    emotion_status=view['emotion_status'],emotion_review_id=emotion_review,industry_reference=view.get('industry_reference'),impact=view['impact'],impact_review_ids=[d['impact_review_id'] for d in decisions if d.get('impact_review_id')],
                    skipped_impact=skipped,missing_fields=gaps,duplication=links.get(source,dict(status='not_established')),material_cutoff=doc['available_at']))
            prediction['disposition']=news_outcomes.saved(c,doc['job_id'],event_ids,source_row=source)
    output['summary']=dict(materials=len(sources),company_rows=len(output['company']),industry_rows=len(output['industry']),
        processing_status=dict(Counter(d['status'] for d in output['documents'])),dispositions=dict(Counter(d['disposition']['code'] for d in output['documents'])),
        pipeline_stage_status={s:dict(Counter(d.get('pipeline_stages',{}).get(s,{}).get('status','unavailable') for d in output['documents'])) for s in core.STAGES},
        rows_with_missing_fields=sum(bool(x['missing_fields']) for x in output['lineage']),candidate_trace_missing=len(sources)-sum(x['status']=='recorded' for x in output['candidates']),
        duplicate_links=len(links),model_calls=0,answer_tables_read=False,database_writes=False)
    return output


def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('--database',required=True);p.add_argument('--manifest',required=True);p.add_argument('--output',required=True)
    args=p.parse_args();manifest=json.loads(Path(args.manifest).read_text('utf-8'))
    result=export_saved(args.database,manifest);data=json.dumps(result,ensure_ascii=False,indent=2).encode('utf-8')
    path=Path(args.output)
    # Exclusive creation keeps an earlier sealed prediction from being overwritten.
    with path.open('xb') as f:f.write(data)
    print(json.dumps(dict(result['summary'],sha256=hashlib.sha256(data).hexdigest(),output=str(path)),ensure_ascii=False))


if __name__=='__main__':main()
