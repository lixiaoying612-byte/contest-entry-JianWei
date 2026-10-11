"""Shared admission and saved-answer compatibility; no model execution."""
import copy
import json
from event_workbench import review_import, review_scoring, alert_rules
from event_workbench import unified_pipeline as core
from event_workbench import training_corpus as corpus
from event_workbench.news_materials import moment

VERSION='news-receivers-20261007-v15-object-identity'


def verification_catalog(processor, ctx, cutoff):
    batch=ctx['dependencies']['retrieval']['batch']
    if batch.get('transmission_audit',{}).get('status')!='complete':raise ValueError('v3_requires_complete_material_audit')
    catalog={};documents={}
    for ref,material in batch['mapping']['materials'].items():
        source=material['document'];identity=source.get('source_row',{}).get('row_key',source['local_id'])
        if identity not in documents:
            entry=processor.scope.entry(identity)
            documents[identity]=(entry,processor.scope.document(entry['source_row'],cutoff=cutoff))
        entry,doc=documents[identity]
        if doc!=source['document']:raise ValueError('v3_catalog_document_changed')
        item=dict(document=doc,available_at=entry['available_at'],source_row=entry['source_row'])
        saved=processor.evidence_catalog.get(core.digest(doc))
        if saved:
            if saved['document']!=doc or moment(saved['available_at'])<moment(entry['available_at']):raise ValueError('v3_catalog_binding_changed')
            if moment(saved['available_at'])<=moment(cutoff) and (not saved.get('source_review',{}).get('reviewed_at') or moment(saved['source_review']['reviewed_at'])<=moment(cutoff)):
                item=copy.deepcopy(saved)
                independence=item.get('source_review',{}).get('independence',{})
                if independence.get('reviewed_at') and moment(independence['reviewed_at'])>moment(cutoff):item['source_review'].pop('independence')
        catalog[ref]=item
        if material.get('relation')=='own_document':catalog['own']=copy.deepcopy(item)
    return catalog


def receive_verification(raw, document, mentions, envelope, cutoff, *, origin):
    value=json.loads(raw)
    if not isinstance(value,dict) or set(value)!={'claims','notes','scoring'}:raise ValueError('verification_v3_root')
    expected={c['mention'] for c in envelope['batch']['content']['candidates']}
    if not isinstance(value['claims'],list) or {c.get('mention') for c in value['claims'] if isinstance(c,dict)}!=expected:
        raise ValueError('verification_v3_mention_coverage')
    if not isinstance(value['scoring'],list) or any(not isinstance(f,dict) or f.get('schema_version')!=review_scoring.CONTRACT for f in value['scoring']):raise ValueError('verification_requires_v3_features')
    checked,scores=review_import.admit_claims(document,mentions,envelope['batch'],
        json.dumps(dict(claims=value['claims'],notes=value['notes']),ensure_ascii=False),value['scoring'],
        envelope['catalog'],reviewer_type=origin,cutoff=cutoff)
    if not checked['valid']:raise ValueError('verification_v3_no_admitted_claim')
    return dict(verification=dict(checked=checked),source_aware_impact_input={},scores=scores,
        method_version=VERSION,assessment_cutoff=cutoff,assessed_at=corpus.now(),reviewer_type=origin)


def receive_provider(processor,stage,ctx,envelope,cutoff,folder,provider,skipped=()):
    """Live response or explicitly replayed saved response, with identical checks."""
    from event_workbench.news_processing import _save
    raw=None
    try:
        choice=provider['choices'][0];raw=choice['message']['content']
        if provider.get('model')!=envelope['request']['model'] or choice.get('finish_reason')!='stop' or not isinstance(raw,str):raise ValueError('v3_incomplete_or_wrong_model')
        if stage=='verification':
            result=receive_verification(raw,ctx['document'],ctx['dependencies']['mentions'],envelope,cutoff,origin='model_response')
            output=dict(status='ready',result=result,reason='shared_v3_claim_admission')
        else:
            value=json.loads(raw)
            if not isinstance(value,dict) or set(value)!={'reviews'} or not isinstance(value['reviews'],list) or any(not isinstance(r,dict) for r in value['reviews']):raise ValueError('impact_v3_root')
            expected={e['event_id']:e for e in envelope['events']};answers=value['reviews']
            transmitted={e['event_id']:e['documents'] for e in json.loads(envelope['request']['messages'][1]['content'])['events']}
            if len(answers)!=len(expected) or {a.get('event_id') for a in answers}!=set(expected):raise ValueError('impact_v3_event_coverage')
            receipts=[];failures=[]
            for record in answers:
                try:
                    sent=expected[record['event_id']]
                    if any(record.get(k)!=sent[k] for k in ('schema_version','rule_version','event_version','base_body_sha','source_rows','cutoff','reviewed_at')):raise ValueError('impact_v3_request_binding_changed')
                    if {a['object_id'] for a in record['assessments']}!={o['id'] for o in sent['allowed_objects']}:raise ValueError('impact_v3_object_coverage')
                    for assessment in record['assessments']:
                        for quote in assessment['quotes']:
                            document=transmitted[record['event_id']].get(quote['source_row'])
                            if document is None or quote.get('document_sha')!=document['document_sha']:raise ValueError('impact_quote_not_in_transmitted_material')
                    receipts.append(review_import.receive_impact_review(processor.app,processor.scope.run_id,record,origin='model_response',cause=processor.impact_cause))
                except (ValueError,KeyError,TypeError,IndexError) as error:
                    failures.append(dict(event_id=record.get('event_id'),reason=str(error),raw_answer=record))
            result=dict(receiver=VERSION,record=dict(checked=dict(assessments=[],issues=failures)),receipts=receipts,skipped=skipped,
                        assessment_cutoff=cutoff,assessed_at=corpus.now())
            output=dict(status='partial' if receipts and (failures or skipped) else 'failed' if failures else 'ready',result=result,reason='shared_v3_impact_admission')
    except (ValueError,KeyError,TypeError,IndexError,AttributeError) as error:
        output=dict(status='failed',reason='v3_answer_rejected:'+str(error),result=dict(receiver=VERSION,error=str(error),raw_answer=raw))
    _save(folder/'checked.json',output)
    return output
