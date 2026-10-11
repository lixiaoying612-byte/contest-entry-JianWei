"""Model conditional-impact proposals, separate from explicit ENG017 AI reviews."""
import copy
import json
import re
from event_workbench import auto_verification as journal
from event_workbench import event_relations as relations
from event_workbench import impact_watch as watch

digest=journal.digest;VERSION='eng022-impact-v1';read=journal.read;save=journal.save
MVP_VERSION='news-mvp-claim-bound-impact-v2'


def prepare(job):
    rep,_=relations.compact.source_representation(job['document']);units=dict(rep['u']);proofs={}
    for u,text in rep['u']:
        p=relations.dossiers.locate(job['document'],'title' if u.startswith('T') else 'body',text)
        proofs[u]=dict(p,document_id=job['id'],document_sha256=relations.dossiers.fingerprint(job['document']),quote_sha256=relations.dossiers.sha(text.encode('utf-8')))
    def unit_refs(quote):return [u for u,text in rep['u'] if quote in text or text in quote]
    def compact_quotes(value):
        if isinstance(value,dict):
            if set(value)=={'section','quote'}:return dict(source_units=unit_refs(value['quote']))
            return {k:compact_quotes(v) for k,v in value.items()}
        if isinstance(value,list):return [compact_quotes(v) for v in value]
        return value
    checked=job['verification']['checked'];events=[];mapping={}
    for index,event in enumerate(job['preview']['events']):
        facts=copy.deepcopy(event['facts']);quotes=facts.pop('evidence_quotes');facts.pop('matches',None)
        refs=[[u for u,text in rep['u'] if text==q['quote']] for q in quotes]
        if any(not x for x in refs):raise ValueError('event_quote_not_in_units')
        oi=event['original_index'];mapping[str(index)]=dict(original_index=oi,event=event,evidence_units=refs)
        cov=next((r for r in checked.get('coverage',{}).get('mentions',[]) if r['original_index']==oi),dict(status='not_checked'))
        claims=[]
        for c in checked.get('valid',[]):
            if c['original_mention_index']!=oi:continue
            opinions=[dict(judgment=a['proposal']['judgment'],source_relation=a['source_relation'],source_units=unit_refs(a['proof']['quote']),quoted=True) for a in c['valid_assessments'] if a.get('proof')]
            claims.append(dict(text=c['claim'],basis_units=unit_refs(c['basis']['quote']),status='checkable_not_verified',opinions=opinions,no_quote_insufficient=sum(a.get('proof') is None for a in c['valid_assessments']),isolated_opinions=len(c['quarantined_assessments']),conflicting=c['conflicting_proposals']))
        for c in checked.get('quarantined',[]):
            original=c.get('original',{})
            if original.get('mention')==cov.get('mention'):claims.append(dict(text=original.get('claim'),status='isolated_not_processed',issue=c.get('issue'),basis_original=original.get('basis'),opinions=[]))
        events.append(dict(mention=index,fields=compact_quotes(facts),evidence_units=refs,warnings=event['field_warnings'],verification=dict(coverage=cov,claims=claims,independent_verification='not_established',premise_status='unverified',limitation='model material opinions, source dependence not independent confirmation; no-quote insufficient reasons omitted')))
    content=dict(units=rep,mentions=events,source_limit='current collected materials; no independent fact verification')
    return dict(job_id=job['id'],content=content,mapping=mapping,proofs=proofs,document=job['document'],verification_semantic_sha=digest(content))


def check(answer,batch,policy,complete=True):
    value=dict(version=MVP_VERSION if 'mvp_claims' in batch else VERSION,assessments=[],issues=[],coverage=[],confirmed=False,source='model_proposal')
    try:
        raw=relations.common.strict_json(answer) if complete else None
        if not isinstance(raw,dict) or set(raw)!={'assessments'} or not isinstance(raw['assessments'],list):raise ValueError('invalid_or_incomplete_root')
    except (ValueError,TypeError):value['issues']=['invalid_or_incomplete_root'];raw={'assessments':[]}
    rows=raw['assessments'];counts={i:sum(isinstance(r,dict) and type(r.get('mention')) is int and r['mention']==int(i) for r in rows) for i in batch['mapping']}
    for ri,row in enumerate(rows):
        a=dict(original_row=row,original_row_index=ri,valid_effects=[],invalid_effects=[],issues=[],status='not_processed')
        value['assessments'].append(a)
        try:
            if not isinstance(row,dict) or set(row)!={'mention','unable_reason','effects'} or type(row['mention']) is not int or str(row['mention']) not in batch['mapping']:raise ValueError('unknown_mention_or_fields')
            mid=str(row['mention']);a['mention']=row['mention'];a['original_index']=batch['mapping'][mid]['original_index']
            if counts[mid]!=1:raise ValueError('duplicate_mention')
            if not isinstance(row['effects'],list) or row['unable_reason'] is not None and (not isinstance(row['unable_reason'],str) or not row['unable_reason'].strip()):raise ValueError('effects_or_unable_reason')
            if not row['effects'] and not row['unable_reason']:raise ValueError('empty_without_reason')
            if row['effects'] and row['unable_reason'] is not None:raise ValueError('effects_and_unable_conflict')
            for ei,effect in enumerate(row['effects']):
                e=dict(original=effect,original_index=ei,issues=[],proofs=[],confirmed=False,source='model_proposal',premise_status='unverified')
                try:
                    required={'target','direction','origin','pathway','basis','conditions','gaps','horizon','grade','grade_reason','dimensions'}
                    if 'mvp_claims' in batch:required.add('premise_claim_ids')
                    if 'official_targets' in batch:required.add('dimension_evidence')
                    if not isinstance(effect,dict) or set(effect)!=required:raise ValueError('effect_fields')
                    if not isinstance(effect['target'],dict) or set(effect['target'])!={'name','role'} or any(not isinstance(effect['target'][k],str) or not effect['target'][k].strip() for k in ('name','role')):raise ValueError('target_required')
                    if effect['direction'] not in ('positive','negative','mixed','unknown') or effect['origin'] not in ('source_explicit','conditional_inference') or effect['grade'] not in ('high','medium','low','unknown'):raise ValueError('effect_enum')
                    if any(not isinstance(effect[k],str) or not effect[k].strip() for k in ('pathway','horizon','grade_reason')):raise ValueError('mechanism_horizon_reason_required')
                    for k in ('conditions','gaps'):
                        if not isinstance(effect[k],list) or not effect[k] or any(not isinstance(x,str) or not x.strip() for x in effect[k]):raise ValueError('conditions_and_gaps_required')
                    refs=effect['basis'];pool={u for us in batch['mapping'][mid]['evidence_units'] for u in us}
                    if not isinstance(refs,list) or not refs or len(set(refs))!=len(refs) or any(not isinstance(u,str) or u not in batch['proofs'] for u in refs) or not pool.intersection(refs):raise ValueError('unknown_or_unbound_basis')
                    e['proofs']=[batch['proofs'][u] for u in refs]
                    for p in e['proofs']:watch.verify_proof(p,{batch['job_id']:dict(document=batch['document'])})
                    text='\n'.join(p['quote'] for p in e['proofs'])
                    if effect['target']['name'] not in (batch['document']['title'] or '')+'\n'+(batch['document']['text'] or '') and effect['target']['name'] not in batch.get('official_targets',{}).get(str(a['original_index']),[]):raise ValueError('target_name_not_in_source')
                    if 'mvp_claims' in batch:
                        ids=effect['premise_claim_ids']
                        if not isinstance(ids,list) or not ids or any(not isinstance(cid,str) or cid not in batch['mvp_claims'] for cid in ids) or len(set(ids))!=len(ids):raise ValueError('unknown_or_empty_premise_claims')
                        facts=batch['mapping'][mid]['event']['facts']
                        if facts.get('status',{}).get('mode') in ('planned','forecast','conditional') and re.search(r'已经|已完成|已投产|已建成|实现了|already\s+(?:completed|operating)',effect['pathway'],re.I) and not re.search(r'若|如果|拟|将|预计|计划|有望|取决|\bif\b|\bplanned\b',effect['pathway'],re.I):
                            raise ValueError('planned_premise_asserted_as_completed')
                        for cid in ids:
                            claim=batch['mvp_claims'][cid];scope=claim['claim_scope']
                            if (scope['original_index']!=a['original_index'] or scope['document_id']!=batch['job_id'] or effect['target']['name'] not in scope['targets'] or scope['status']!=facts.get('status',{})):
                                raise ValueError('premise_claim_subject_phase_or_event_mismatch')
                        e['premise_claim_ids']=ids
                        e['premise_versions']={cid:batch['mvp_claims'][cid]['assessment_version'] for cid in ids}
                    # Numeric fabrication guard is lexical only, not semantic finance validation.
                    numbers=lambda s:set(re.findall(r'\d+(?:\.\d+)?',s))
                    if not numbers(effect['pathway']+' '+effect['grade_reason'])<=numbers(text):raise ValueError('unsupported_numeric_statement')
                    d=effect['dimensions']
                    if not isinstance(d,dict) or set(d)!={'scope','materiality','urgency','comparison_basis'} or d['scope'] not in ('limited','broad','unknown') or d['materiality'] not in ('minor','substantive','unknown') or d['urgency'] not in ('near_term','long_term','unknown') or d['comparison_basis'] is not None and (not isinstance(d['comparison_basis'],str) or not d['comparison_basis'].strip()):raise ValueError('dimensions_contract')
                    missing=any(d[k]=='unknown' for k in ('scope','materiality','urgency'))
                    if 'official_targets' in batch:
                        from event_workbench.competition_rules import impact_score
                        evidence=effect['dimension_evidence']
                        if not isinstance(evidence,dict) or set(evidence)!={'materiality','scope','urgency'}:raise ValueError('dimension_evidence_fields')
                        supported=[]
                        for name,item in evidence.items():
                            if not isinstance(item,dict) or set(item)!={'source_refs','reason'} or not isinstance(item['reason'],str) or not item['reason'].strip():raise ValueError('dimension_reason_required')
                            dim_refs=item['source_refs']
                            if not isinstance(dim_refs,list) or any(not isinstance(r,str) or r not in refs for r in dim_refs):raise ValueError('dimension_unbound_reference')
                            if dim_refs:supported.append(name)
                        e['rule_score']=impact_score(d,supported_dimensions=supported,comparison_located=bool(d['comparison_basis'] and d['comparison_basis'] in text))
                        e['dimension_evidence']=copy.deepcopy(evidence)
                    elif effect['grade']=='high' and (missing or d['materiality']!='substantive' or not d['comparison_basis']):raise ValueError('high_without_substantive_comparison')
                    e['tier']='insufficient_information' if effect['grade']=='unknown' or missing or effect['pathway']=='unknown' else 'important_pending_review' if effect['grade']=='high' else 'ordinary_tracking'
                    e['status']='model_tentative';e['grade_rule_version']=policy['version'];e['formal_alert']=False;a['valid_effects'].append(e)
                except (ValueError,TypeError,KeyError) as error:e['issues'].append(str(error));e['status']='invalid_pending_review';a['invalid_effects'].append(e)
            a['status']='insufficient_information' if not a['valid_effects'] else 'partial_with_isolation' if a['invalid_effects'] else 'model_tentative'
        except (ValueError,TypeError,KeyError) as error:a['issues'].append(str(error))
    for mid,m in batch['mapping'].items():
        found=[a for a in value['assessments'] if a.get('mention')==int(mid)]
        value['coverage'].append(dict(mention=int(mid),original_index=m['original_index'],status=found[0]['status'] if len(found)==1 else 'not_processed',returned=counts[mid]))
    return value


