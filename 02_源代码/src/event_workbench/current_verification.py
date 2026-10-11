"""Current proposal selection and no-evidence insufficiency; original checks immutable."""
import copy
from event_workbench import auto_verification as old

VERSION='eng020-checks-r1-current-insufficient'

def coverage(checked,batch,answer):
    try:value=old.base.trial.common.strict_json(answer)
    except (ValueError,TypeError):value={}
    claims=value.get('claims',[]) if isinstance(value,dict) else []
    if not isinstance(claims,list):claims=[]
    notes=value.get('notes',[]) if isinstance(value,dict) else []
    rows=[]
    for mid,binding in batch['mapping']['mentions'].items():
        matches=lambda c:isinstance(c,dict) and type(c.get('mention')) is int and str(c['mention'])==mid
        raw=[c for c in claims if matches(c)];valid=[c for c in checked['valid'] if matches(c['original'])];quarantined=[c for c in checked['quarantined'] if matches(c['original'])]
        opinions=[a for c in valid for a in c['valid_assessments']];bad=sum(len(c['quarantined_assessments']) for c in valid)
        pending=sum(len(c['original'].get('assessments',[])) for c in quarantined if isinstance(c['original'].get('assessments'),list))
        evidence=sum(a.get('proof') is not None for a in opinions)
        status='uncovered' if not raw else 'all_claims_quarantined' if not valid else 'no_valid_material_opinion' if not opinions else 'partial_with_isolation' if quarantined or bad else 'insufficient_opinions_only' if not evidence else 'opinions_pending_review'
        rows.append(dict(mention=int(mid),original_index=binding['original_index'],returned_claims=len(raw),checkable_claims=len(valid),quarantined_claims=len(quarantined),valid_material_opinions=len(opinions),evidence_backed_opinions=evidence,no_quote_insufficient=sum(a.get('proof') is None and a['proposal']['judgment']=='insufficient' for a in opinions),quarantined_assessments=bad,unprocessed_assessments_in_quarantined_claims=pending,status=status,confirmed=False))
    return dict(mentions=rows,missing_explanation=not claims and not notes,all_mentions_returned=all(r['returned_claims'] for r in rows),all_mentions_checkable=all(r['checkable_claims'] for r in rows),semantics_verified=False)

def check(answer,batch,complete=True):
    strict=old.check(answer,batch,complete);result=copy.deepcopy(strict);result['version']=VERSION;result['original_check_version']=strict['version'];result['no_quote_adaptations']=[]
    materials=batch['mapping']['materials'];allowed={str(c['mention']):set(c['materials']) for c in batch['content']['candidates']}
    for claim in result['valid']:
        kept=[];mid=str(claim['original']['mention'])
        for item in claim['quarantined_assessments']:
            a=item['original'];candidate=a.get('material') if isinstance(a,dict) else None
            eligible=isinstance(a,dict) and set(a)=={'material','judgment','quote','reason'} and a['judgment']=='insufficient' and a['quote'] is None and isinstance(a['reason'],str) and bool(a['reason'].strip()) and isinstance(candidate,str) and candidate in materials and (materials[candidate]['relation']=='own_document' or candidate in allowed[mid])
            if not eligible:kept.append(item);continue
            addition=dict(original_index=item['original_index'],proposal=copy.deepcopy(a),proof=None,source_relation=materials[candidate]['relation'],independent_confirmation='not_established',reviewer_type='model_proposal',confirmed=False,opinion_kind='insufficient_without_supporting_quote',counts_as_evidence=False,original_strict_isolation=copy.deepcopy(item),rule_version=VERSION)
            claim['valid_assessments'].append(addition)
            result['no_quote_adaptations'].append(dict(original_claim_index=claim['original_claim_index'],original_assessment_index=item['original_index'],original=copy.deepcopy(a),original_strict_issue=item['issue'],proof=None))
        claim['quarantined_assessments']=kept;claim['valid_assessments'].sort(key=lambda a:a['original_index'])
        for a in claim['valid_assessments']:
            a['counts_as_evidence']=a.get('proof') is not None
            a['contributes_support_or_refute']=a['counts_as_evidence'] and a['proposal']['judgment'] in ('supports','refutes')
            if not a['counts_as_evidence'] and a['proposal']['judgment']=='insufficient':a['opinion_kind']='insufficient_without_supporting_quote'
    result['coverage']=coverage(result,batch,answer);return result


