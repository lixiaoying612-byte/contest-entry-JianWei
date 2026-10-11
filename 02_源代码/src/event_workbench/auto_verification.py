"""Model-sourced material proposals, independent of explicit ENG016 AI decisions."""
import copy
import json
from types import SimpleNamespace
from event_workbench import document_pipeline as base
from event_workbench import verification_records as reviews

ROOT=base.ROOT;CONTRACT=ROOT/'engineering/contracts/eng020';VERSION='eng020-checks-v1'
read=base.read;save=base.save;digest=base.digest


def evidence(material,quote):
    if not isinstance(quote,str) or not quote:raise ValueError('empty_quote')
    field=material['field'];local=reviews.versions.locate({field:material['text']},'title' if field=='title' else 'body',quote)
    doc=material['document'];start=material['start']+local['start']
    proof=dict(document_id=doc['local_id'],document_sha256=reviews.versions.fingerprint(doc['document']),section=local['section'],quote=quote,start=start,end=start+len(quote),quote_sha256=reviews.versions.sha(quote.encode('utf-8')))
    reviews.check_proof({doc['local_id']:doc},proof)
    return proof

def check(answer,batch,complete=True):
    result=dict(version=VERSION,source='model_proposal',confirmed=False,independent_review_status='pending',independent_confirmation='not_established',valid=[],quarantined=[],issues=[],mode='retrospective_current_collected_materials')
    if not complete:result['issues']=['incomplete_or_failed_response'];return result
    try:value=base.trial.common.strict_json(answer)
    except (ValueError,TypeError):result['issues']=['invalid_json'];return result
    if not isinstance(value,dict) or set(value)!={'claims','notes'} or not isinstance(value['claims'],list) or not isinstance(value['notes'],list) or any(not isinstance(x,str) for x in value['notes']):result['issues']=['invalid_envelope'];return result
    result['notes']=value['notes'];mapping=batch['mapping'];materials=mapping['materials'];available={str(c['mention']):set(c['materials']) for c in batch['content']['candidates']}
    for ci,claim in enumerate(value['claims']):
        try:
            if not isinstance(claim,dict) or set(claim)!={'mention','claim','basis','assessments'} or type(claim['mention']) is not int or str(claim['mention']) not in mapping['mentions']:raise ValueError('claim_mention_or_structure')
            mi=str(claim['mention']);basis=claim['basis']
            if not isinstance(claim['claim'],str) or not claim['claim'].strip() or not isinstance(basis,dict) or set(basis)!={'material','quote'} or basis['material'] not in materials or materials[basis['material']]['relation']!='own_document':raise ValueError('claim_or_own_basis_invalid')
            proof=evidence(materials[basis['material']],basis['quote'])
            if not isinstance(claim['assessments'],list) or not claim['assessments']:raise ValueError('assessment_list_required')
            row=dict(original_claim_index=ci,original=copy.deepcopy(claim),original_mention_index=mapping['mentions'][mi]['original_index'],claim=claim['claim'],basis=proof,valid_assessments=[],quarantined_assessments=[],source='model_proposal',confirmed=False,field_warnings=mapping['mentions'][mi]['event']['field_warnings'])
            for ai,assessment in enumerate(claim['assessments']):
                try:
                    if not isinstance(assessment,dict) or set(assessment)!={'material','judgment','quote','reason'} or assessment['judgment'] not in ('supports','refutes','related','insufficient') or not isinstance(assessment['reason'],str) or not assessment['reason'].strip():raise ValueError('assessment_structure_or_enum')
                    mid=assessment['material']
                    if mid is None and assessment['judgment']=='insufficient' and assessment['quote'] is None:
                        relation='no_material';p=None
                    else:
                        if not isinstance(mid,str) or mid not in materials or materials[mid]['relation']!='own_document' and mid not in available[mi]:raise ValueError('material_not_in_mention_candidates')
                        p=evidence(materials[mid],assessment['quote']);relation=materials[mid]['relation']
                    row['valid_assessments'].append(dict(original_index=ai,proposal=assessment,proof=p,source_relation=relation,independent_confirmation='not_established',reviewer_type='model_proposal',confirmed=False))
                except (ValueError,TypeError,KeyError) as e:row['quarantined_assessments'].append(dict(original_index=ai,original=assessment,issue=str(e)))
            kinds={x['proposal']['judgment'] for x in row['valid_assessments']};row['conflicting_proposals']={'supports','refutes'}<=kinds
            result['valid'].append(row)
        except (ValueError,TypeError,KeyError) as e:result['quarantined'].append(dict(original_index=ci,original=claim,issue=str(e)))
    return result

