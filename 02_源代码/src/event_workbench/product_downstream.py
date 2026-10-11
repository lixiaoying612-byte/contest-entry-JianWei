"""Product bindings to frozen semantic adapters; no new model prompts."""
import copy
import json
from pathlib import Path
from event_workbench import compact_verification_input as compact
from event_workbench.joint_extraction_trial import wire_bytes
from event_workbench import verification_materials as materials
from event_workbench import incremental_relations as relations
from event_workbench import current_verification as verification
from event_workbench import unified_pipeline as core
from event_workbench import current_event_dossiers as dossiers
from event_workbench import unified_execution as execution
from event_workbench import product_evidence_spans as spans
from event_workbench import product_phase_scope
from event_workbench.document_pipeline import DOCUMENT_MAX_BYTES, validate_product_document as validate_document

ROOT=Path(__file__).resolve().parents[2]
BUSINESS_MODEL='deepseek-flash'


def current_model_request(request):
    """Product-only default; historical constructors and saved responses stay intact."""
    return dict(request,model=BUSINESS_MODEL)


def usable_verification(result):
    if not isinstance(result,dict) or result.get('product_pending'):return False
    record=result.get('verification')
    return isinstance(record,dict) and isinstance(record.get('checked'),dict) and isinstance(result.get('source_aware_impact_input'),dict)


def usable_result(stage,result):
    if not isinstance(result,dict) or result.get('product_pending'):return False
    if stage=='verification':return usable_verification(result)
    if stage=='impact':return isinstance(result.get('record'),dict) and isinstance(result['record'].get('checked'),dict)
    if stage=='relations':return isinstance(result.get('proposals'),list)
    return True




class Pipeline(execution.Pipeline):
    validate_document=staticmethod(validate_document)
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        with self.connect() as c:c.executescript('CREATE TABLE IF NOT EXISTS product_dependencies(job TEXT PRIMARY KEY,body TEXT); CREATE TABLE IF NOT EXISTS product_stage_versions(seq INTEGER PRIMARY KEY AUTOINCREMENT,job TEXT,stage TEXT,body TEXT); CREATE TABLE IF NOT EXISTS product_retrieval_context(job TEXT PRIMARY KEY,body TEXT);')
    def _signature(self,c,jid,stage,states):
        sig=super()._signature(c,jid,stage,states)
        if stage in ('relation_candidates','relations'):
            from event_workbench.aggregation_v2 import VERSION,NORMALIZER
            sig=core.digest([sig,VERSION,NORMALIZER])
        if stage in ('retrieval','verification','impact'):
            row=c.execute('SELECT body FROM product_retrieval_context WHERE job=?',(jid,)).fetchone()
            if row:sig=core.digest([sig,json.loads(row[0])])
        if stage not in ('extract','mentions') and c.execute("SELECT 1 FROM sqlite_master WHERE name='product_dependencies'").fetchone():
            row=c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()
            if row:sig=core.digest([sig,json.loads(row[0])])
        return sig
    def _write(self,c,jid,stage,status,reason,input_sha,result=None,provenance=None):
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='product_stage_versions'").fetchone():
            old=c.execute('SELECT * FROM stages WHERE job=? AND stage=?',(jid,stage)).fetchone()
            if old and (old['status']!=status or old['semantic_sha']!=(core.digest(core.semantic(result)) if result is not None else None)):
                c.execute('INSERT INTO product_stage_versions(job,stage,body) VALUES(?,?,?)',(jid,stage,json.dumps(dict(old),ensure_ascii=False)))
        return super()._write(c,jid,stage,status,reason,input_sha,result,provenance)



def job(ctx):
    mentions=ctx['dependencies']['mentions']
    rep,positions=relations.relations.compact.source_representation(ctx['document'])
    preview=dict(mentions.get('preview') or {},events=[dict(original_index=m['original_index'],facts=m['body'],field_warnings=m.get('field_warnings',[])) for m in mentions['mentions']])
    prepared=mentions.get('prepared') or dict(local_id=ctx['job'],document=ctx['document'],representation=rep,source_positions=positions)
    return dict(id=ctx['job'],document=ctx['document'],preview=preview,prepared=prepared)


def material_batch(ctx,retrieval,lookup,supplements=()):
    j=job(ctx);selected=[]
    excluded_supplements=[]
    if retrieval.get('evaluation_time') and retrieval.get('time_basis')!='received_at':
        from event_workbench.corpus_evidence_index import instant
        eligible=[];cutoff=instant(retrieval['evaluation_time'],end_of_day=True)
        for supplement in supplements:
            try:
                if instant(supplement['document'].get('publication_time'),end_of_day=True)>cutoff:raise ValueError('future_material')
                eligible.append(supplement)
            except (ValueError,TypeError):excluded_supplements.append(dict(id=supplement['id'],reason='future_or_unknown_publication_at_explicit_evaluation'))
        supplements=eligible
    if retrieval.get('selected_original_indices') is not None:
        j['preview']['events']=[e for e in j['preview']['events'] if e['original_index'] in retrieval['selected_original_indices']]
    chosen={};source_identities={}
    for item in retrieval.get('items',[]):
        for candidate in item.get('candidates',[]):
            if candidate.get('source_row'):
                did=candidate['document_id'];row=candidate['source_row']
                if did in chosen and chosen[did]!=row:raise ValueError('conflicting_selected_source_rows')
                chosen[did]=row
                source_identities['ENG062-source:'+did+':'+row['row_key']]=did
    original_lookup=lookup
    def lookup(identity):
        did=source_identities.get(identity,identity)
        return original_lookup(did,source_row=chosen[did]) if did in chosen else original_lookup(did)
    own_ids={j['id']}|{r['parent_document_id'] for r in retrieval.get('saved',[]) if r.get('parent_document_id')}|{m['document_id'] for m in ctx['dependencies']['mentions'].get('mentions',[]) if m.get('document_id')}
    indices={e['original_index'] for e in j['preview']['events']}
    unassigned=[dict(entry=k,record=copy.deepcopy(r),reason='unknown_original_mention_index') for k in ('items','saved','packages') for r in retrieval.get(k,[]) if r.get('original_index') not in indices]
    if retrieval.get('batch'):
        result=copy.deepcopy(retrieval['batch'])
    else:
        rep,positions=relations.relations.compact.source_representation(j['document'])
        j['prepared']=dict(j['prepared'],representation=rep,source_positions=positions)
        docs={};packages=[]
        for event in j['preview']['events']:
            raw=[]
            for row in retrieval.get('items',[]):
                if row['original_index']==event['original_index']:raw.extend(row['candidates'])
            for row in retrieval.get('saved',[]):
                if row['original_index']==event['original_index']:raw.extend(row['displayed'])
            candidates=[]
            for row in retrieval.get('packages',[]):
                if row.get('original_index')!=event['original_index']:continue
                for candidate in row['candidates']:
                    w=candidate['window'];source=lookup(w['document_id'])
                    docs[w['document_id']]=dict(local_id=w['document_id'],document=source,source_family=None)
                    candidates.append(dict(window=w,source_relation='own_document' if w['document_id'] in own_ids else candidate['source_relation']))
                    selected.append(dict(original_index=event['original_index'],candidate=copy.deepcopy(candidate),window=copy.deepcopy(w),entry='legacy_package'))
            for r in raw:
                original_did=r['document_id'];did='ENG062-source:'+original_did+':'+r['source_row']['row_key'] if r.get('source_row') else original_did
                source=lookup(did);docs[did]=dict(local_id=did,document=source,source_family=None)
                if r.get('source_row'):docs[did].update(corpus_document_id=original_did,source_row=copy.deepcopy(r['source_row']))
                excerpts=r.get('excerpts') or [dict(field='body',start=r['start'],end=r['end'],text=r['quote'])]
                for xi,x in enumerate(excerpts):
                    field='text' if x['field']=='body' else x['field']
                    w=dict(document_id=did,field=field,start=x['start'],end=x['end'],source_text=x['text'])
                    candidates.append(dict(window=w,source_relation='own_document' if did in own_ids else r.get('source_relation','dependency_unknown')))
                    selected.append(dict(original_index=event['original_index'],candidate=copy.deepcopy(r),excerpt_index=xi,window=w,entry='selected_excerpt'))
            packages.append(dict(job=j['id'],event=event,material_package=dict(candidates=candidates)))
        result=materials.batch_input(j,dict(version='eng049-bound-source-candidates',documents=list(docs.values()),packages=packages))
        # Equal source text is not document identity, including an own-source copy.
        origin=dict(local_id=j['id'],document=j['document'],source_family=None)
        result['mapping']['document']=origin
        for ref,m in result['mapping']['materials'].items():
            if ref.startswith(('T','B')):m['document']=origin
    # Supplemental sources retain their own identity and exact positions. They are
    # candidate evidence until the semantic judgments return, never assumed support.
    for supplement in supplements:
        if supplement['parent_original_index'] not in indices:unassigned.append(dict(entry='supplement',record=copy.deepcopy(supplement),reason='unknown_original_mention_index'))
        doc=supplement['document'];rep,positions=relations.relations.compact.source_representation(doc);pos={p['ref']:p for p in positions}
        refs=[]
        for unit,text in rep['u']:
            if unit.startswith('T'):continue
            ref='X'+core.digest([supplement['id'],unit])[:16];p=pos[unit];refs.append(ref)
            result['mapping']['materials'][ref]=dict(document=dict(local_id=supplement['id'],document=doc,source_family=None),field='text',start=p['start'],end=p['end'],text=text,relation='dependency_unknown')
            if not any(m['id']==ref for m in result['content']['materials']):result['content']['materials'].append(dict(id=ref,text=text,relation='dependency_unknown',title=doc['title'],channel=doc['source'],publication_time=doc['publication_time']))
        for row in result['content']['candidates']:
            oi=result['mapping']['mentions'][str(row['mention'])]['original_index']
            if oi==supplement['parent_original_index']:row['materials']=list(dict.fromkeys(row['materials']+refs))
    # Preserve source-time and cutoff records independently; neither is event time.
    previous_context=result['content'].get('evidence_context',{})
    retrieval_cutoff=retrieval.get('cutoff',previous_context.get('original_retrieval_cutoff',result.get('cutoff',ctx['document'].get('publication_time'))))
    temporal=spans.collection_time(result['mapping']['materials'].values(),previous_context.get('current_judgment_cutoff',result.get('cutoff')))
    time_records=temporal.pop('records')
    if retrieval.get('evaluation_time'):
        from event_workbench.corpus_evidence_index import instant
        cutoff=instant(retrieval['evaluation_time'],end_of_day=True)
        for did,row in chosen.items():
            if instant(row['published_iso'],end_of_day=True)>cutoff:raise ValueError('future_selected_source_row')
        temporal.update(current_judgment_cutoff=cutoff.isoformat(),cutoff_basis='explicit_evaluation_time; not_latest_candidate_publication',time_status='explicit_evaluation_time')
    result['cutoff']=temporal['current_judgment_cutoff']
    result['content']['evidence_context']=dict(previous_context,contract=spans.VERSION,cutoff=result['cutoff'],
        original_retrieval_cutoff=retrieval_cutoff,**temporal,
        per_mention_cutoffs=[{k:copy.deepcopy(r[k]) for k in ('original_index','cutoff','cutoff_source','cutoff_warning') if k in r} for r in retrieval.get('saved',[])] or previous_context.get('per_mention_cutoffs',[]),
        supplement_times=previous_context.get('supplement_times',[])+[dict(document_id=s['id'],publication_time=s['document'].get('publication_time'),original_index=s['parent_original_index']) for s in supplements],
        independence='unknown; own statements, multiple windows, same-domain translations and reprints are not independent corroboration')
    result['content']['mode']='current_explicit_collected_materials; publication_records_retained; '+spans.VERSION
    before=copy.deepcopy(result)
    for ref,m in before['mapping']['materials'].items():
        if m['document']['local_id'] in own_ids:
            m['relation']='own_document'
            next(w for w in before['content']['materials'] if w['id']==ref)['relation']='own_document'
    own={j['id']:j['document'],**{s['id']:s['document'] for s in supplements}}
    result=spans.normalize(before,lambda did:own[did] if did in own else lookup(did))
    audit=result['transmission_audit'];old=before['mapping']['materials']
    audit['time_records']=time_records
    audit['selected_source_rows']=chosen
    audit['selected_original_indices']=retrieval.get('selected_original_indices')
    audit['excluded_supplements']=excluded_supplements
    if 'transmission_audit' in before:audit['previous_transmission_audit']=before['transmission_audit']
    # The user message is nested JSON in the request; include its actual escaping
    # cost, so the offline audit can compare complete requests before/after union.
    def encoded_user_bytes(batch):
        content=compact.prepare(batch)['content']
        return len(wire_bytes(json.dumps(content,ensure_ascii=False,separators=(',',':'))))
    audit['unmerged_serialized_user_bytes']=encoded_user_bytes(before)
    audit['merged_serialized_user_bytes']=encoded_user_bytes(result)
    for row in selected:
        w=row['window'];refs=[ref for ref,m in old.items() if m['document']['local_id']==w['document_id'] and (m['field'],m['start'],m['end'],m['text'])==(w['field'],w['start'],w['end'],w['source_text'])]
        row['sent_refs']=list(dict.fromkeys(audit['ref_map'][r] for r in refs if r in audit['ref_map']))
        row['status']='included' if row['sent_refs'] else 'not_included_invalid_span'
    audit['selected_records']=selected
    audit['unassigned_records']=unassigned
    audit['selected_not_included']=sum(r['status']!='included' for r in selected)
    if unassigned or audit['selected_not_included']:audit['status']='blocked_invalid_spans'
    audit['retrieval_context']={k:copy.deepcopy(v) for k,v in retrieval.items() if k!='batch'}
    return result




def relation_batch(incoming,history,documents,forced=(),top_k=10,matter_ids=None):
    """Independent lexical/object/identity routes, budgeted by saved matter."""
    from event_workbench import compact_model_io
    from event_workbench import aggregation_v2 as agg
    if not 1 <= top_k <= 50:raise ValueError('invalid_relation_top_k')
    matter_ids=matter_ids or {}
    def codes(m):
        return {(d.get('target_type'),str(d.get('target_code'))) for x in m.get('monitoring_scope',{}).get('matches',[]) if (d:=x.get('definition')) and d.get('target_code')}
    anchors=agg.identity_anchors;text=agg.recall_text
    ranked=[]
    for mi,m in enumerate(incoming):
        names={p['name'] for p in m['body'].get('participants',[])}
        eligible=list({h['id']:h for h in history+incoming[:mi] if h['id']!=m['id']}.values())
        union={}
        for h in eligible:
            shared=sorted(anchors(m)&anchors(h))
            if shared or h['id'] in forced:union[h['id']]=dict(exact_anchors=shared,explicit_supplement=h['id'] in forced)
        if eligible:
            import bm25s
            index=bm25s.BM25(method='lucene',k1=1.5,b=.75)
            bags=[list(compact_model_io.terms(text(h)).elements()) for h in eligible]
            if any(bags):
                index.index(bags,show_progress=False)
                scores=index.get_scores(list(compact_model_io.terms(text(m)).elements()))
                order=sorted(range(len(eligible)),key=lambda i:(-float(scores[i]),eligible[i]['id']))
                for channel in ('bm25_object',):
                    seen=set()
                    for i in order:
                        h=eligible[i];mid=h['id'];eid=matter_ids.get(mid,mid)
                        if scores[i]<=0 or eid in seen:continue
                        if channel=='bm25_object' and not (codes(m)&codes(h) or names&{p['name'] for p in h['body'].get('participants',[])}):continue
                        if len(seen)>=top_k:break
                        seen.add(eid);union.setdefault(mid,{})[channel]=float(scores[i])
        # Exact hits retain comparable nodes; never silently drop them at top_k.
        ordered=sorted(union,key=lambda mid:(not bool(union[mid].get('exact_anchors') or union[mid].get('explicit_supplement')),mid))
        overflow=max(0,len(ordered)-128)
        ranked.append(dict(mention=m['id'],candidates=[dict(mention=mid,event_id=matter_ids.get(mid,mid),persisted_matter=mid in matter_ids,channels=union[mid]) for mid in ordered[:128]],overflow=overflow,
            status='pending_candidate_overflow' if overflow else 'candidates_pending' if union else 'no_candidates',novelty_status='retrieval_scoped',
            method_version=agg.VERSION,normalizer_version=agg.NORMALIZER,dense_status='not_computed',traditional_conversion='not_enabled'))
    if not any(r['candidates'] for r in ranked):return dict(candidates=[],coverage=ranked)
    b=relations.batch(incoming,history,documents,ranked)
    b['content']['aggregation_contract']=agg.VERSION
    b['content']['identity_field_policy']=agg.identity_field_policy()
    b['method_version']=agg.VERSION
    return dict(candidates=b['content']['pairs'],batch=b,coverage=ranked)


def credibility(result,index):
    if (result or {}).get('claim_contract')=='news-mvp-claims-v1':
        from event_workbench.news_mvp import credibility as scoped
        return scoped(result,index)
    record=(result or {}).get('verification') or {}
    if not isinstance(record,dict):record={}
    checked=record.get('checked') or {}
    if not isinstance(checked,dict):checked={}
    claims=[c for c in checked.get('valid',[]) if c.get('original_mention_index')==index]
    opinions=[a for c in claims for a in c.get('valid_assessments',[])]
    kinds={a['proposal']['judgment'] for a in opinions if a.get('proof')}
    state='冲突提案' if {'supports','refutes'}<=kinds else '有反驳提案' if 'refutes' in kinds else '有支持提案' if 'supports' in kinds else '证据不足' if opinions or record.get('source')=='local_no_candidate' else '未核验'
    # The common V3 gate binds conclusions regardless of transport provenance.
    from event_workbench.review_scoring import VERSION as score_version
    if claims and all(c.get('verdict') and (c.get('source')=='codex_session' or c.get('score_rule')==score_version) for c in claims):
        conclusions={c['verdict'] for c in claims}
        state=next(iter(conclusions)) if len(conclusions)==1 else '分主张判断'
    scope='仅检查当前允许检索范围内的候选材料包，未发现可纳入的候选；未搜索全网，未执行语义核验，也未形成核验原答中的说法范围' if record.get('source')=='local_no_candidate' else '核验尚未执行或没有可用核验原答；待核查说法范围尚未形成' if not checked else '按已有核验原答的说法范围展示；未返回范围、引用隔离和整体覆盖仍待核查'
    return dict(status=state,claims=claims,locatable_evidence=sum(a.get('proof') is not None for a in opinions),known_independent_chains=None,pending_scope=scope,history_credit='尚未建立',confirmed=False)


def impact_result(result,index,facts):
    from event_workbench import product_decisions as decisions
    return decisions.summary(decisions.proposals(result if usable_result('impact',result) else None,index,facts,'unbound-preview'))


def guarded_states(states,documents=None):
    """Keep source-bound node judgments; legacy modes cannot establish node identity."""
    values=copy.deepcopy(states);mentions={m['id']:m for ss in values.values() for m in (ss['mentions']['result'] or {}).get('mentions',[])}
    owners={m['id']:jid for jid,ss in values.items() for m in (ss['mentions']['result'] or {}).get('mentions',[])}
    for ss in values.values():
        for p in (ss['relations']['result'] or {}).get('proposals',[]):
            if p.get('status')!='model_tentative' or p.get('raw',{}).get('k') not in ('same_event','same_project_new_progress'):continue
            if p.get('aggregation',{}).get('endpoint_fingerprints'):
                from event_workbench.aggregation_v2 import endpoint_fingerprint
                actual=[endpoint_fingerprint(mentions[m]['body']) for m in p.get('member_ids',[]) if m in mentions]
                if actual!=p['aggregation']['endpoint_fingerprints']:
                    p['status']='pending_input_changed';p.setdefault('issues',[]).append('aggregation_endpoint_changed_requires_review');continue
            modes={mentions[m]['body'].get('status',{}).get('mode') for m in p.get('member_ids',[]) if m in mentions}
            aggregation=p.get('aggregation',{})
            checked_node=(aggregation.get('contract_version')=='event-aggregation-v2' and
                          aggregation.get('decision_status')=='validated' and aggregation.get('node_relation')=='same' and
                          bool(aggregation.get('endpoint_fingerprints')))
            if not checked_node and p['raw']['k']=='same_event' and 'planned' in modes and ('asserted' in modes or 'occurred' in modes):
                p['original_binding_status']=p['status'];p['status']='pending_phase_scope_review';p.setdefault('issues',[]).append('plan_and_occurrence_need_identity_scope_review_before_merge')
            elif documents is not None:
                guard=product_phase_scope.check(p,mentions,owners,documents)
                if guard:
                    p['original_binding_status']=p['status'];p['status']='pending_phase_scope_review'
                    p['phase_scope_review']=guard;p.setdefault('issues',[]).append(guard['code'])
    from event_workbench.aggregation_v2 import guard_graph
    guard_graph([p for ss in values.values() for p in (ss['relations']['result'] or {}).get('proposals',[])])
    return values
