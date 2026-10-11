"""Publish guarded pipeline results into persistent event and reminder versions.

The model remains an initial assessment. This module supplies identity/version
storage and explicit reminder rules; it does not turn quotations into truth.
"""
import copy
import json
from contextlib import closing
from datetime import datetime
from event_workbench import unified_pipeline as core
from event_workbench import current_event_dossiers as dossiers
from event_workbench import product_downstream as downstream
from event_workbench import product_decisions as decisions
from event_workbench import training_corpus as corpus
from event_workbench import news_notifications, news_outcomes

RULE = 'news-event-reminders-v2'
SCHEMA = news_notifications.SCHEMA+'''
CREATE TABLE IF NOT EXISTS news_event_drafts(run_id TEXT,event_id TEXT,base_body_sha TEXT,body TEXT,PRIMARY KEY(run_id,event_id));
CREATE TABLE IF NOT EXISTS news_event_aliases(run_id TEXT,old_id TEXT,event_id TEXT,created TEXT,PRIMARY KEY(run_id,old_id));
CREATE TABLE IF NOT EXISTS news_event_members(run_id TEXT,mention_id TEXT,event_id TEXT,
 PRIMARY KEY(run_id,mention_id));
CREATE TABLE IF NOT EXISTS news_event_versions(run_id TEXT,event_id TEXT,version INTEGER,
 material_version INTEGER,signature TEXT,body TEXT,created TEXT,
 PRIMARY KEY(run_id,event_id,material_version));
CREATE TABLE IF NOT EXISTS news_reminder_versions(run_id TEXT,id TEXT,version INTEGER,
 event_id TEXT,signature TEXT,body TEXT,created TEXT,PRIMARY KEY(run_id,id,version));
'''


def scope_matches(mention):
    scope = mention.get('monitoring_scope', {})
    return [m for m in scope.get('matches', []) if m.get('decision') in ('matched', 'm')]


def recognition_key(body):
    """Same-document factual identity; reply position is not an identity constraint."""
    value=dossiers.clean({k:body[k] for k in ('participants','status','times','details') if k in body})
    value.get('status',{}).pop('context',None)
    return core.digest(value)


def current_member_owner(c,run_id,mention):
    """A saved positional mapping is usable only for its saved factual content."""
    row=c.execute('SELECT e.id,e.body FROM news_event_members m JOIN replay_events e ON e.run_id=m.run_id AND e.id=m.event_id WHERE m.run_id=? AND m.mention_id=?',(run_id,mention.get('id'))).fetchone()
    if not row:return None
    event=json.loads(row['body'])
    if event.get('identity_update_pending') or event.get('current_status')=='background':return None
    members=event.get('evidence_sources',{}).get('members',[])
    for i,m in enumerate(members):
        if m['mention_id']!=mention.get('id'):continue
        facts=event.get('event_facts',[])
        key=m.get('recognition_key') or (recognition_key(facts[i]) if i<len(facts) else None)
        if key!=recognition_key(mention['body']):return None
        summary=m.get('recognition_summary')
        if summary is not None and summary!=mention['body'].get('summary'):return None
        return row['id']
    return None


def reconcile_members(previous, owners, by, states):
    """Rebind uniquely unchanged facts; hold ambiguous replacements for review.

    This maps current reply-local references to archives, not model references to
    different model references. Claims and relations keep their current binding.
    """
    old_by_doc={};new_by_doc={};mapping={};blocked=set()
    for eid,event in previous.items():
        members=event.get('evidence_sources',{}).get('members',[])
        for i,m in enumerate(members):
            if owners.get(m['mention_id'])!=eid:continue
            facts=event.get('event_facts',[])
            key=m.get('recognition_key') or (recognition_key(facts[i]) if i<len(facts) else None)
            summary=m.get('recognition_summary')
            if summary is None and len(members)==1:summary=event.get('event_summary',{}).get('text')
            old_by_doc.setdefault(m['document_id'],[]).append((m['mention_id'],eid,key,summary))
    for mid,(jid,m) in by.items():
        new_by_doc.setdefault(jid,[]).append((mid,recognition_key(m['body']),m['body'].get('summary')))
    for jid,old in old_by_doc.items():
        if any(states.get(jid,{}).get(s,{}).get('status') not in core.GOOD for s in ('extract','mentions')):continue
        new=new_by_doc.get(jid,[]);matches={}
        for mid,eid,key,summary in old:
            candidates=[n for n in new if key is not None and n[1]==key]
            peers=[o for o in old if o[2]==key]
            if len(candidates)!=1 or len(peers)!=1:
                candidates=[n for n in candidates if summary is not None and n[2]==summary]
                peers=[o for o in peers if summary is not None and o[3]==summary]
            if len(candidates)==1 and len(peers)==1:matches[mid]=candidates[0][0]
        if len(matches)!=len(old) or len(set(matches.values()))!=len(matches):
            blocked.add(jid)
        else:mapping.update(matches)
    result={mid:eid for mid,eid in owners.items() if mid not in mapping}
    result.update({new:owners[old] for old,new in mapping.items()})
    # Only the local comparison copy changes; persisted historical snapshots do not.
    for event in previous.values():
        for m in event.get('evidence_sources',{}).get('members',[]):
            m['mention_id']=mapping.get(m['mention_id'],m['mention_id'])
        if 'anchor_mention_id' in event:event['anchor_mention_id']=mapping.get(event['anchor_mention_id'],event['anchor_mention_id'])
        source=event.get('assessment_source',{})
        if source.get('mention_id') in mapping:source['mention_id']=mapping[source['mention_id']]
        for node in event.get('event_timeline',{}).get('business_nodes',[]):
            node['members']=[mapping.get(mid,mid) for mid in node['members']]
    return result,blocked


def _claims(ss, mention):
    state = ss['verification']
    value = copy.deepcopy(downstream.credibility(state.get('result') if state['status'] in core.GOOD else None,
                                               mention['original_index']))
    value['claims']=[c for c in value['claims'] if not c.get('article_only')]
    value['processing_status'] = state['status']
    record=state.get('result') or {}
    value['assessment_cutoff']=record.get('assessment_cutoff')
    value['assessed_at']=record.get('assessed_at')
    value['cutoff_context']=copy.deepcopy(record.get('cutoff_context'))
    value['status'] = {'failed': '核验处理失败', 'unknown_no_retry': '核验发送状态未知',
                       'waiting': '尚未核验', 'running': '正在核验'}.get(state['status'], value['status'])
    # Supports in the supplied material is not independent corroboration.
    for claim in value['claims']:
        claim['source_mention_id'] = mention['id']
        claim['source_document_id'] = mention['document_id']
        claim['assessment_cutoff'] = value['assessment_cutoff']
        opinions = claim.get('valid_assessments', [])
        for a in opinions:
            a['independent_confirmation'] = 'not_established'
            a['reviewer_type'] = record.get('reviewer_type','model_initial_assessment')
    return value


def claim_binding(mention, effect, credibility):
    """Bind one current claim by mention and exact original basis, not by supports count."""
    if 'premise_claim_ids' in effect:
        ids=effect['premise_claim_ids'];by={c.get('claim_id'):c for c in credibility.get('claims',[]) if c.get('claim_id')}
        if not ids or any(cid not in by for cid in ids):return []
        claims=[by[cid] for cid in ids]
        if any(c.get('claim_scope',{}).get('mention_id')!=mention['id'] or effect.get('premise_versions',{}).get(c['claim_id'])!=c.get('assessment_version') for c in claims):return []
        return claims
    def anchor(proof):
        if not isinstance(proof, dict):return None
        keys=('document_id','section','start','end','quote')
        if any(k not in proof for k in keys):return None
        if type(proof['start']) is not int or type(proof['end']) is not int or proof['end']<=proof['start'] or not proof['quote']:return None
        return tuple(proof[k] for k in keys)
    proofs={anchor(p) for p in effect.get('proofs',[]) if anchor(p) is not None}
    eligible=[]
    for claim in credibility.get('claims',[]):
        basis=anchor(claim.get('basis'))
        if claim.get('original_mention_index')!=mention['original_index'] or basis is None:continue
        if basis[0]!=mention.get('document_id') or basis not in proofs:continue
        if claim.get('source_mention_id',mention.get('id'))!=mention.get('id'):continue
        if claim.get('source_document_id',mention.get('document_id'))!=mention.get('document_id'):continue
        if not isinstance(claim.get('claim'),str) or not claim['claim'].strip():continue
        eligible.append(claim)
    # Multiple distinct claims in one quoted paragraph are not a unique effect premise.
    unique={core.digest([c['claim'],anchor(c['basis'])]):c for c in eligible}
    return list(unique.values()) if len(unique)==1 else []


def reminder_scope(item):
    scope=item.get('scope',{})
    return {key:copy.deepcopy(scope.get(key)) for key in ('claim','phase','mode','effect_modality','horizon','conditions')}


def auto_decisions(event_id, mention, ss, credibility):
    """A specific, supported impact rationale is required even for check reminders."""
    out = []
    impact=ss['impact']
    for item in decisions.proposals(impact.get('result') if impact['status'] in core.GOOD else None, mention['original_index'],
                                   mention['body'], event_id):
        item = copy.deepcopy(item)
        item.update(rule_version=RULE, review_type='AI初判', confirmed=False,
                    route='observation', reasons=[], missing=[], alert=False)
        effects = item.get('effects', [])
        matches = scope_matches(mention)
        objects = {m.get('target_name') or m.get('definition', {}).get('target_name') for m in matches}
        # Matching metadata must contain the official object, never substitute a participant.
        if not matches or not item.get('target') or item['target'] not in objects:
            item['missing'].append('本影响对象的监测范围依据')
        raw = effects[0]['original'] if effects else {}
        if (ss.get('mentions',{}).get('result') or {}).get('contract_revision') in ('news-competition-v1','news-competition-baseline-v1','news-business-v1'):
            from event_workbench import competition_rules as rules
            score=effects[0].get('rule_score') if effects else None
            score=score or rules.impact_score({},supported_dimensions=[])
            item.update(rule_version=rules.VERSION,rule_score=score,level={'unknown':'待定','low':'低','medium':'中','high':'高'}[score['level']])
            bound=claim_binding(mention,effects[0],credibility) if effects else []
            checks=[rules.claim_score(c) for c in bound]
            decision=rules.reminder(score,checks)
            if item['missing'] or ss['verification']['status'] not in core.GOOD:
                decision=dict(kind=None,reason='对象匹配或核验阶段尚未完成，保留待核状态')
            item.update(alert=decision['kind'] is not None,route={'event_notice':'event_notice','check_notice':'important_pending',None:'observation'}[decision['kind']],
                        reasons=[decision['reason']],missing=item['missing']+score['missing'],
                        bound_claims=[dict(claim=c['claim'],basis=copy.deepcopy(c['basis']),support=checks[i],claim_id=c.get('claim_id')) for i,c in enumerate(bound)])
            if decision['kind']=='check_notice':item['missing'].append('绑定前提的适用原始依据或冲突澄清')
            out.append(item);continue
        dims = raw.get('dimensions', {})
        specific = bool(effects and raw.get('pathway') and effects[0].get('proofs') and
                        all(dims.get(k) not in (None, '', 'unknown') for k in ('scope', 'materiality', 'urgency')))
        if not specific:item['missing'].append('具体影响路径及范围、重要性、时效依据')
        item['level'] = item['proposed_level'] if specific else '待定'
        if raw.get('grade') not in ('high','medium') or item['missing']:
            item['reasons'] = ['未满足明确提醒规则，继续观察']
            out.append(item)
            continue
        bound=claim_binding(mention,effects[0],credibility)
        item['bound_claims']=[dict(claim=c['claim'],basis=copy.deepcopy(c['basis']),
                                 document_id=mention.get('document_id'),original_index=mention['original_index']) for c in bound]
        kinds = {a['proposal']['judgment'] for c in bound
                 for a in c.get('valid_assessments', []) if a.get('proof')}
        if ss['verification']['status'] not in core.GOOD:
            item['reasons'] = ['核验尚未完成；程序状态不自动触发提醒']
        elif 'refutes' in kinds:
            item.update(alert=True, route='important_pending',
                        reasons=['存在潜在重要影响，相关说法出现反驳或冲突'], missing=['冲突口径的原始依据'])
        elif 'supports' in kinds and all(any(a.get('proof') and a['proposal']['judgment']=='supports' for a in c.get('valid_assessments',[])) for c in bound):
            item.update(alert=True, route='event_notice',
                        reasons=['监测事项有材料支持，且具备具体的重要影响依据'],
                        missing=['来源独立性尚未建立；提醒属于AI初判'])
        else:
            item.update(alert=True, route='important_pending',
                        reasons=['具体影响路径具有潜在重要性，但关键说法尚缺证据'],
                        missing=['支持关键说法的材料：'+raw['pathway']] if bound else ['未建立本影响路径与唯一原文主张的对应：'+raw['pathway']])
        out.append(item)
    return out


class EventPublisher:
    def __init__(self, app, scope):
        self.app, self.scope = app, scope
        self.run_id = scope.run_id
        with closing(app.connect()) as c,c:c.executescript(SCHEMA)

    def sync(self, pipeline, *, job_ids=None, include_drafts=False, accepted_mentions=None):
        def saved_states(jid):
            # Frozen replay adapters expose only get(); live pipelines can read
            # stage rows without rebuilding all cross-document relations.
            if not hasattr(pipeline,'_states'):return pipeline.get(jid)['stages']
            with pipeline.connect() as c:return pipeline._states(c,jid)
        pipeline_rows=pipeline.list()
        if job_ids is not None:
            job_ids=set(job_ids)
            known={row['id'] for row in pipeline_rows}
            has_mentions=any((saved_states(jid)['mentions'].get('result') or {}).get('mentions') for jid in job_ids & known)
            if not has_mentions:
                with closing(self.app.connect()) as c:
                    saved=[json.loads(row[0]) for row in c.execute('SELECT body FROM replay_events WHERE run_id=?',(self.run_id,))]
                # A re-analysis of an existing archive still needs preservation/withdrawal checks.
                if not any(m.get('document_id') in job_ids for e in saved for m in e.get('evidence_sources',{}).get('members',[])):return []
        documents, states, entries = {}, {}, {}
        # get() also rebuilds the whole relation graph; only stage rows are needed
        # here. Read them once per job and derive the guarded graph once below.
        for row in pipeline_rows:
            try:
                entry = self.scope.entry(row['id'])
                document = {key:entry[key] for key in ('title','text','source','publication_time')}
            except (KeyError, ValueError):continue
            if core.digest(document) != row['id']:raise ValueError('pipeline_document_changed')
            documents[row['id']] = document
            entries[row['id']] = entry
            states[row['id']] = saved_states(row['id'])
        guarded = downstream.guarded_states(states, documents)
        # Refuse a partial pipeline that would silently remove existing archives.
        with closing(self.app.connect()) as c:
            for row in c.execute('SELECT body FROM replay_events WHERE run_id=?',(self.run_id,)):
                saved=json.loads(row[0])
                if saved.get('record_kind')=='news' or saved.get('case_id'):continue
                required={m['document_id'] for m in saved.get('evidence_sources',{}).get('members',[]) if m.get('document_id') and not m.get('repeated_material_of')}
                if not required<=documents.keys():raise ValueError('existing_event_history_missing_from_pipeline')
        derived = dossiers.derive(documents, guarded)
        by = {m['id']:(j,m) for j,ss in guarded.items()
              if ss['mentions']['status'] in core.GOOD and ss['extract']['status'] in core.GOOD
              for m in (ss['mentions'].get('result') or {}).get('mentions',[])}
        groups = [set(d['members']) for d in derived['dossiers']]
        # Only the checked explicit same-project progress relation extends an event.
        # A common company name never creates an edge here.
        progress = []
        proposals=[p for ss in guarded.values() if ss['relations']['status'] in core.GOOD
                   for p in (ss['relations'].get('result') or {}).get('proposals',[])]
        graph=core.relations.current_graph(list(by),proposals)
        different=[set(p['member_ids']) for p in graph['proposals'] if p.get('raw',{}).get('k')=='different_event'
                   and p.get('status') in ('model_tentative','pending_closure_conflict')]
        for p in graph['proposals']:
            if p.get('status')!='model_tentative' or p.get('raw',{}).get('k')!='same_project_new_progress':continue
            if len(p.get('member_ids',[]))!=2 or not p.get('original_proofs'):continue
            mids=set(p['member_ids'])
            if not mids<=by.keys():continue
            try:
                for proof in p['original_proofs']:
                    dossiers.verify_relation_proof(proof,{key:dict(document=doc) for key,doc in documents.items()})
                if not {by[mid][0] for mid in mids}<={proof['document_id'] for proof in p['original_proofs']}:continue
            except (ValueError,KeyError,TypeError):continue
            linked=[g for g in groups if g&mids]
            merged=set.union(*linked)
            if any(pair<=merged for pair in different):continue
            if len(linked)==2:groups=[g for g in groups if g not in linked]+[merged]
            progress.append(p)
        published=[];publication_pending={}
        with closing(self.app.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            groups.sort(key=lambda group:min((entries[by[m][0]]['available_at'],m) for m in group))
            old_members={r['mention_id']:r['event_id'] for r in c.execute('SELECT * FROM news_event_members WHERE run_id=?',(self.run_id,))}
            previous_events={};published_identities=set()
            from event_workbench.product_research import publication_gaps
            for r in c.execute('SELECT * FROM news_event_versions WHERE run_id=? ORDER BY material_version DESC',(self.run_id,)):
                body=json.loads(r['body'])
                # Same-body attachments have no new recognized facts or mention.
                body.get('evidence_sources',{})['members']=[m for m in body.get('evidence_sources',{}).get('members',[]) if not m.get('repeated_material_of')]
                if body.get('record_kind')!='news' and not body.get('case_id'):previous_events.setdefault(r['event_id'],body)
                # Legacy archives keep their previous reconciliation semantics.
                current_business=all((states.get(m['document_id'],{}).get('extract',{}).get('result') or {}).get('version')=='news-business-v1' for m in body.get('evidence_sources',{}).get('members',[]))
                if not current_business or not publication_gaps(body):published_identities.add(r['event_id'])
            published_identities.update(r[0] for r in c.execute('SELECT target FROM replay_actions WHERE run_id=?',(self.run_id,)))
            # Keep unrelated saved archives byte-for-byte intact during a new input.
            untouched=set()
            if job_ids is not None:
                affected=set(job_ids)
                components=[{by[m][0] for m in g} for g in groups]
                old_jobs={eid:{m['document_id'] for m in old.get('evidence_sources',{}).get('members',[]) if m.get('document_id')} for eid,old in previous_events.items()}
                components.extend(old_jobs.values())
                while True:
                    expanded=affected.union(*(part for part in components if part & affected))
                    if expanded==affected:break
                    affected=expanded
                untouched={eid for eid,jobs in old_jobs.items() if not jobs & affected}
                groups=[g for g in groups if any(by[m][0] in affected for m in g)]
            if accepted_mentions is not None:
                accepted_mentions=set(accepted_mentions)
                rejected=[g for g in groups if not g<=accepted_mentions]
                untouched.update(old_members[m] for g in rejected for m in g if m in old_members)
                groups=[g for g in groups if g<=accepted_mentions]
            saved_owners=old_members.copy()
            # A never-published failed draft cannot freeze a rejected extraction.
            # Its versions remain in the audit; usable history and user actions
            # still require unambiguous identity reconciliation.
            old_members={mid:eid for mid,eid in old_members.items() if eid in published_identities}
            old_members,identity_pending=reconcile_members({eid:e for eid,e in previous_events.items() if eid in published_identities},old_members,by,states)
            rebound_jobs={by[mid][0] for mid,eid in old_members.items() if mid in by and saved_owners.get(mid)!=eid}
            rebound_jobs.update(jid for old in previous_events.values() for jid in old.get('judgment_history_gaps',[]))
            # Pending or failed re-analysis is not a withdrawal of the last valid archive.
            pending={jid:news_outcomes.pending_reanalysis(c,jid,ss) for jid,ss in states.items()}
            held={eid for eid,old in previous_events.items() if any(m['document_id'] in identity_pending for m in old.get('evidence_sources',{}).get('members',[]))}
            for eid,old in previous_events.items():
                members=old.get('evidence_sources',{}).get('members',[])
                connected=any({m['mention_id'] for m in members}<=g for g in groups)
                for m in members:
                    jid=m.get('document_id');ss=states.get(jid)
                    if not ss:continue
                    identity_missing=any(ss[s]['status'] not in core.GOOD|{'withdrawn'} for s in ('extract','mentions')) or (ss['extract']['status']=='partial' and m.get('mention_id') not in by)
                    claim_pending=ss['verification']['status'] in ('waiting','running','failed','unknown_no_retry','stale') and any(q.get('source_document_id')==jid for q in old.get('credibility',{}).get('claims',[]))
                    relation_pending=len(members)>1 and (ss['relations']['status'] in ('failed','unknown_no_retry','stale') or 'relations' in pending[jid] and not connected)
                    if identity_missing or claim_pending or relation_pending:held.add(eid);break
            # Partial background judgments cannot withdraw a previously accepted event.
            for eid,old in previous_events.items():
                for member in old.get('evidence_sources',{}).get('members',[]):
                    mid=member['mention_id'];jid=member['document_id']
                    if mid in by and by[mid][1]['body'].get('status',{}).get('context')=='background' and any(states[jid][s]['status']=='partial' for s in ('extract','mentions')):
                        held.add(eid)
            # A pending merge/split must not erase a second archive connected to a held one.
            while True:
                held_members={m for m,eid in old_members.items() if eid in held}
                connected={old_members[m] for g in groups if g & held_members for m in g if m in old_members}
                if connected<=held:break
                held.update(connected)
            groups=[g for g in groups if not g & held_members and not any(by[m][0] in identity_pending for m in g)]
            # Background alone supplies context, never a new radar row. A checked relation
            # may attach its dated history to a focal event or an existing archive.
            background_members={m for g in groups if all(by[m][1]['body'].get('status',{}).get('context')=='background' for m in g) for m in g}
            retired={eid for eid,old in previous_events.items() if eid not in held|untouched and old.get('evidence_sources',{}).get('members') and all(m['mention_id'] in background_members for m in old['evidence_sources']['members'])}
            groups=[g for g in groups if not g<=background_members]
            history={}
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='product_stage_versions'").fetchone():
                for jid in {j for j,m in by.values()}:
                    history[jid]=[]
                    # Old per-index judgments cannot be rebound to a different reply order.
                    # Their original versions remain accessible in the historical dossier.
                    if jid in rebound_jobs:continue
                    for row in c.execute("SELECT seq,body FROM product_stage_versions WHERE job=? AND stage='verification' ORDER BY seq",(jid,)):
                        old=json.loads(row['body'])
                        if not old.get('result') or old['status'] not in core.GOOD|{'failed','unknown_no_retry'}:continue
                        old['result']=json.loads(old['result']) if isinstance(old['result'],str) else old['result']
                        if not old['result'].get('assessment_cutoff'):
                            identity=(json.loads(old['provenance']) if isinstance(old.get('provenance'),str) else old.get('provenance') or {}).get('request_identity')
                            attempt=c.execute('SELECT request FROM attempts WHERE id=?',(identity,)).fetchone()
                            if attempt:
                                request=json.loads(attempt[0])
                                old['result']['assessment_cutoff']=(request.get('batch') or request.get('semantic_envelope',{}).get('batch',{})).get('cutoff')
                        history[jid].append(dict(version=row['seq'],state=old))
            # An old identity belongs to exactly one resulting group after a split.
            owners={}
            for eid,old in previous_events.items():
                members=old.get('evidence_sources',{}).get('members',[])
                anchor=old.get('anchor_mention_id') or (members[0]['mention_id'] if members else None)
                candidates=[i for i,g in enumerate(groups) if any(old_members.get(m)==eid for m in g)]
                if candidates:owners[eid]=next((i for i in candidates if anchor in groups[i]),candidates[0])
            used=set(held)|untouched
            for group_index,group in enumerate(groups):
                mids=sorted(group,key=lambda m:(entries[by[m][0]]['available_at'],m))
                prior=list(dict.fromkeys(old_members[m] for m in mids if m in old_members))
                # First persisted member stays the event identity as progress is added.
                owned=[eid for eid in prior if owners.get(eid)==group_index and eid not in used]
                event_id=owned[0] if owned else 'news-'+core.digest([self.run_id,mids[0]])[:24]
                if not owned and event_id in previous_events:
                    event_id='news-'+core.digest([self.run_id,mids,[recognition_key(by[m][1]['body']) for m in mids],'new-identity'])[:24]
                if event_id in used:event_id='news-'+core.digest([self.run_id,mids,'split'])[:24]
                used.add(event_id)
                linked=[d for d in derived['dossiers'] if set(d['members'])&group]
                dto=self._project(event_id,mids,by,documents,entries,guarded,linked,progress,history,previous_events.get(event_id))
                gaps=sorted({by[m][0] for m in mids}&rebound_jobs)
                if gaps:dto['judgment_history_gaps']=gaps
                previous=c.execute('SELECT * FROM news_event_versions WHERE run_id=? AND event_id=? ORDER BY material_version DESC LIMIT 1',(self.run_id,event_id)).fetchone()
                old=json.loads(previous['body']) if previous else None
                anchor=previous_events.get(event_id,{}).get('anchor_mention_id')
                dto['anchor_mention_id']=anchor if anchor in group else mids[0]
                dto['identity_history']=copy.deepcopy((old or {}).get('identity_history',[]))
                split_from=[eid for eid in prior if owners.get(eid)!=group_index]
                if split_from:
                    transition=dict(kind='split',from_events=sorted(split_from),members=mids)
                    if transition not in dto['identity_history']:dto['identity_history'].append(transition)
                # Reprints add materials; substantive state excludes quote/arrival bookkeeping.
                substance=self._substance(dto)
                sig=core.digest(substance)
                changed=not previous or previous['signature']!=sig
                if changed and old and self._only_related_material_added(old,dto,linked):changed=False
                dto['event_version']=(previous['version']+(1 if changed else 0)) if previous else 1
                # Carry saved repeated material across later progress, without adding facts.
                for eid in owned:
                    head=c.execute('SELECT body FROM replay_events WHERE run_id=? AND id=?',(self.run_id,eid)).fetchone()
                    if not head:continue
                    saved=json.loads(head[0]);sources=dto['evidence_sources']['source_rows']
                    for member in saved.get('evidence_sources',{}).get('members',[]):
                        if member.get('repeated_material_of') in sources and member['source_row'] not in sources:
                            dto['evidence_sources']['members'].append(copy.deepcopy(member));sources.append(member['source_row'])
                            dto['event_timeline']['items'].extend(copy.deepcopy([i for i in saved['event_timeline']['items'] if i.get('source_row')==member['source_row']]))
                            dto.setdefault('source_relations',dict(nodes=copy.deepcopy(dto['evidence_sources']['members'][:-1]),edges=[]))
                            dto['source_relations']['nodes'].append(copy.deepcopy(member))
                            dto['source_relations']['edges'].extend(copy.deepcopy([i for i in saved['source_relations']['edges'] if i.get('material_a')==member['source_row']]))
                dto['material_count']=len(dto['evidence_sources']['members'])
                from event_workbench import dossier_review
                dossier_review.restore(c,self.run_id,dto)
                from event_workbench.company_metadata import resolve as company_metadata
                from event_workbench.product_research import business_matches
                metadata=company_metadata(business_matches(dto),corpus.now())
                if metadata:dto['industry_metadata']=metadata
                from event_workbench import credibility_review
                credibility_review.restore(c,self.run_id,dto)
                from event_workbench import alert_review
                alert_review.restore(c,self.run_id,dto)
                alert_review.require_current_review(c,dto)
                from event_workbench.product_research import publication_gaps
                gaps=publication_gaps(dto)
                if gaps and old and not publication_gaps(old):
                    # A failed refresh does not publish half an assessment over a usable snapshot.
                    head=json.loads(c.execute('SELECT body FROM replay_events WHERE run_id=? AND id=?',(self.run_id,event_id)).fetchone()[0])
                    head['publication_update_pending']=gaps
                    draft=copy.deepcopy(dto)
                    draft.update(material_version=previous['material_version'],assessment_version=old.get('assessment_version',0))
                    c.execute('INSERT OR REPLACE INTO news_event_drafts VALUES(?,?,?,?)',(self.run_id,event_id,corpus.digest(head),corpus.js(draft)))
                    if include_drafts:published.append(dict(draft,analysis_draft=True))
                    held.update(prior or [event_id]);used.update(prior or [event_id])
                    publication_pending.update({eid:gaps for eid in prior or [event_id]})
                    continue
                dto['material_version']=previous['material_version'] if previous else 0
                if not gaps:dto['warning_reasons']['decisions']=self._reminders(c,dto,changed)
                assessment_sig=dossier_review.assessment_signature(dto) if dto.get('identification_review') or dto.get('classification_review') or dto.get('industry_metadata') else core.digest(dict(credibility=dto.get('credibility'),emotions=dto.get('emotions'),impacts=dto.get('warning_reasons')))
                dto['assessment_signature']=assessment_sig
                dto['assessment_version']=(old or {}).get('assessment_version',0)+int(not old or old.get('assessment_signature')!=assessment_sig)
                if not gaps:news_notifications.record(c,self.run_id,dto)
                comparable=copy.deepcopy(dto);comparable.pop('material_version',None)
                old_comparable=copy.deepcopy(old or {});old_comparable.pop('material_version',None)
                if comparable!=old_comparable:
                    dto['material_version']+=1
                    c.execute('INSERT INTO news_event_versions VALUES(?,?,?,?,?,?,?)',(self.run_id,event_id,dto['event_version'],dto['material_version'],sig,corpus.js(dto),corpus.now()))
                else:dto=old
                for mid in mids:c.execute('INSERT OR REPLACE INTO news_event_members VALUES(?,?,?)',(self.run_id,mid,event_id))
                for alias in set(owned)-{event_id}:
                    c.execute('INSERT OR REPLACE INTO news_event_aliases VALUES(?,?,?,?)',(self.run_id,alias,event_id,corpus.now()))
                    # Preserve operation history; shared event owns current follow preference.
                    c.execute("INSERT OR IGNORE INTO replay_actions SELECT run_id,kind,?,body FROM replay_actions WHERE run_id=? AND target=? AND kind IN ('follow','task')",(event_id,self.run_id,alias))
                    c.execute('DELETE FROM replay_events WHERE run_id=? AND id=?',(self.run_id,alias))
                c.execute('INSERT OR REPLACE INTO replay_events VALUES(?,?,?,?,?)',(event_id,self.run_id,dto['job_id'],dto['available_at'],corpus.js(dto)))
                c.execute('DELETE FROM news_event_drafts WHERE run_id=? AND event_id=?',(self.run_id,event_id))
                published.append(dto)
            # Disappearing effects/events must revoke their old reminders, not silently leave active rows.
            absent={r[0] for r in c.execute('SELECT DISTINCT event_id FROM news_reminder_versions WHERE run_id=?',(self.run_id,))}-used
            for eid in absent & previous_events.keys():
                previous=previous_events.get(eid,{})
                self._reminders(c,dict(member_id=eid,event_version=previous.get('event_version',0),warning_reasons=dict(decisions=[])),True)
            retired_changed=False
            for row in c.execute('SELECT id,body FROM replay_events WHERE run_id=?',(self.run_id,)).fetchall():
                if row['id'] in untouched:continue
                if row['id'] in held:
                    body=json.loads(row['body'])
                    if row['id'] in publication_pending and body.get('publication_update_pending')!=publication_pending[row['id']]:
                        body['publication_update_pending']=publication_pending[row['id']]
                        c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?',(corpus.js(body),self.run_id,row['id']))
                        retired_changed=True
                    pending_identity=any(m['document_id'] in identity_pending for m in body.get('evidence_sources',{}).get('members',[]))
                    if bool(body.get('identity_update_pending'))!=pending_identity:
                        if pending_identity:body['identity_update_pending']=True
                        else:body.pop('identity_update_pending',None)
                        c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?',(corpus.js(body),self.run_id,row['id']))
                        retired_changed=True
                if row['id'] in retired:
                    body=json.loads(row['body'])
                    if body.get('current_status')!='background':
                        body['current_status']='background'
                        body['warning_reasons']['decisions']=self._reminders(c,dict(member_id=row['id'],event_version=body['event_version'],warning_reasons=dict(decisions=[])),True)
                        c.execute('UPDATE replay_events SET body=? WHERE run_id=? AND id=?',(corpus.js(body),self.run_id,row['id']))
                        retired_changed=True
                elif row['id'] in previous_events and row['id'] not in used:c.execute('DELETE FROM replay_events WHERE run_id=? AND id=?',(self.run_id,row['id']))
            if published or retired_changed:c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(self.run_id,))
        return published

    @staticmethod
    def _only_related_material_added(old,dto,linked):
        """A checked same-phase paraphrase is material, not a new business stage."""
        def facts_by_member(value):
            members=value.get('evidence_sources',{}).get('members',[])
            facts=value.get('event_facts',[])
            if len(members)!=len(facts):return {}
            return {m['mention_id']:dossiers.clean(f) for m,f in zip(members,facts)}
        before=facts_by_member(old);after=facts_by_member(dto)
        new_members=after.keys()-before.keys()
        v2=[p for p in dto.get('event_relations',[]) if p.get('aggregation') and p.get('status')=='model_tentative' and set(p.get('member_ids',[]))<=after.keys() and set(p.get('member_ids',[]))&new_members]
        if v2:
            if any(p['aggregation']['operation'] in ('ENRICH','CORRECT','CONFLICT') for p in v2):return False
            equivalent={m for p in v2 if p['aggregation']['operation']=='MATERIAL_ONLY' and set(p['member_ids'])&before.keys() for m in p['member_ids']}
            if not new_members<=equivalent:return False
        return bool(before) and before.keys()<after.keys() and all(after[m]==f for m,f in before.items()) and old.get('progress_count')==len(linked) and all(set(d['members'])&before.keys() for d in linked)

    @staticmethod
    def _substance(dto):
        # Deduplicate assertions before comparison; source count is never confidence.
        facts=sorted({core.digest(dossiers.clean(x)) for x in dto['event_facts']})
        from event_workbench.aggregation_v2 import fact_delta_signature
        delta=fact_delta_signature(dto)
        return dict(facts=facts,**({'claim_changes':sorted({core.digest(dossiers.clean(x)) for x in delta})} if delta else {}))


    def _project(self,event_id,mids,by,documents,entries,states,linked,progress,history=None,previous=None):
        newest=next((m for m in reversed(mids) if by[m][1]['body'].get('status',{}).get('context')!='background'),mids[-1])
        from event_workbench.aggregation_v2 import select_current
        saved=(previous or {}).get('assessment_source',{}).get('mention_id')
        if any(p.get('aggregation',{}).get('contract_version')=='event-aggregation-v2' for p in progress):
            from event_workbench.review_scoring import publication_moment
            dated=[m for m in mids if documents[by[m][0]].get('publication_time') and by[m][1]['body'].get('status',{}).get('context')!='background']
            if dated:
                # Latest disclosure selects the displayed assessment, not a
                # fabricated occurrence-order edge. Explicit BACKFILL still wins.
                saved=max(dated,key=lambda m:(publication_moment(documents[by[m][0]]['publication_time']),m))
        newest,time_order=select_current(mids,linked,progress,newest,saved if saved in mids else None)
        if time_order and time_order['status']=='parallel_pending':time_order['display_basis']='latest_disclosure_not_occurrence_order'
        # A refreshed judgment may belong to an older report of this same phase.
        # Predecessor phases remain history and cannot replace the latest progress.
        component=next((set(d['members']) for d in linked if newest in d['members']),{newest})
        replaced={p['member_ids'][1] for ss in states.values() for p in (ss.get('relations',{}).get('result') or {}).get('proposals',[])
                  if p.get('status')=='model_tentative' and p.get('aggregation',{}).get('operation') in ('ENRICH','CORRECT')
                  and set(p.get('member_ids',[]))<=component}
        def judgment_order(mid):
            owner=by[mid][0];ss=states[owner];source=entries[owner]['available_at']
            cutoff=(ss['verification'].get('result') or {}).get('assessment_cutoff')
            cutoff=cutoff or ((ss.get('retrieval',{}).get('result') or {}).get('batch') or {}).get('cutoff') or source
            published=datetime.fromisoformat(source.replace('Z','+00:00'));evaluated=datetime.fromisoformat(cutoff.replace('Z','+00:00'))
            return evaluated,evaluated>published,published,mid
        # Reassessing an earlier article must not restore its superseded summary.
        newest=max((m for m in mids if m in component-replaced and by[m][1]['body'].get('status',{}).get('context')!='background'),key=judgment_order,default=newest)
        jid,mention=by[newest];doc=documents[jid];entry=entries[jid]
        claims=[];quotes=[];timeline=[];members=[];facts=[];source_rows=set();judgment_history=[]
        for mid in mids:
            owner,m=by[mid];source=entries[owner];source_rows.add(source['source_row'])
            claims.extend(_claims(states[owner],m)['claims'])
            for row in dossiers.candidates(m,owner,documents[owner],[]):
                for origin in row['origins']:
                    for p in origin['quotes']:
                        p=dict(p,source_row=source['source_row'])
                        if p not in quotes:quotes.append(p)
            members.append(dict(mention_id=mid,source_row=source['source_row'],document_id=owner,
                                title=documents[owner]['title'],source=documents[owner]['source'],available_at=source['available_at'],
                                recognition_key=recognition_key(m['body']),recognition_summary=m['body'].get('summary')))
            body=m['body'];facts.append({k:body[k] for k in ('participants','status','times','details') if k in body})
            timeline.append(dict(time=documents[owner]['publication_time'],kind='材料发布时间（非发生时间）',summary=body.get('summary'),source_row=source['source_row']))
            if source.get('received_at'):
                timeline.append(dict(time=source['received_at'],kind='系统收到材料',summary=documents[owner]['title'] or '未填标题',source_row=source['source_row']))
            for i,t in enumerate(body.get('times',[])):
                proofs,issues,_=dossiers.selected_proofs(documents[owner],owner,m,f'times[{i}]',t)
                timeline.append(dict(time=t.get('normalized') or t.get('text'),precision=t.get('precision'),kind='原文时间（所属事实见引文）',summary='；'.join(dict.fromkeys(p['quote'] for p in proofs)) if not issues else '时间依据不完整，所属事实暂不确定',source_row=source['source_row'],evidence_refs=t.get('evidence_refs',[]),proofs=proofs if not issues else []))
            judgment=_claims(states[owner],m)
            if judgment.get('assessment_cutoff') and judgment['claims']:
                timeline.append(dict(time=judgment['assessment_cutoff'],kind='判断使用材料截至时间',summary=judgment.get('summary') or judgment['status'],source_row=source['source_row']))
            if judgment.get('assessed_at'):
                timeline.append(dict(time=judgment['assessed_at'],kind='系统形成判断',summary=judgment.get('summary') or judgment['status'],source_row=source['source_row']))
            for past in (history or {}).get(owner,[]):
                old=_claims(dict(verification=past['state']),m)
                historical=dict(version=past['version'],source_row=source['source_row'],original_index=m['original_index'],
                                summary=m['body'].get('summary'),status=old['status'],processing_status=old['processing_status'],
                                assessment_cutoff=old['assessment_cutoff'],claims=old['claims'])
                try:source_rows.update(self._bind_claim_sources(historical['claims'],historical['assessment_cutoff']))
                except (ValueError,KeyError):
                    historical.update(claims=[],status='历史判断的证据当前不可访问',evidence_unavailable=True)
                judgment_history.append(historical)
        credibility=_claims(states[jid],mention)
        for p in progress:
            if set(p.get('member_ids',[]))<=set(mids):
                timeline.append(dict(time=None,kind='有据进展或更正（日期见引用）',summary=p.get('raw',{}).get('why') or p.get('raw',{}).get('k'),proofs=copy.deepcopy(p.get('original_proofs',[]))))
        objects=scope_matches(mention)
        competition=(states[jid]['mentions'].get('result') or {}).get('contract_revision') in ('news-competition-v1','news-competition-baseline-v1','news-business-v1')
        if competition:source_rows.update(self._bind_claim_sources(credibility['claims']))
        impacts=auto_decisions(event_id,mention,states[jid],credibility)
        credibility['claims']=claims
        if credibility.get('claim_contract'):
            from event_workbench.news_mvp import verdict
            for claim in claims:claim['verdict']=verdict(claim)
            conclusions={c['verdict'] for c in claims}
            credibility['status']=next(iter(conclusions)) if len(conclusions)==1 else '分主张判断' if claims else credibility['status']
            credibility['summary']='；'.join(c['claim']+'：'+c['verdict'] for c in claims)
        source_rows.update(self._bind_claim_sources(claims))
        if competition:
            from event_workbench.competition_rules import claim_score
            for claim in claims:claim['support_score']=claim_score(claim)
        from event_workbench.review_scoring import event_score, score, VERSION as SCORE_VERSION
        from event_workbench.aggregation_v2 import apply_claim_changes
        apply_claim_changes(claims,[p for ss in states.values() for p in (ss.get('relations',{}).get('result') or {}).get('proposals',[]) if set(p.get('member_ids',[]))<=set(mids)])
        for claim in claims:
            mid=claim.get('source_mention_id')
            if mid in component and claim.get('score_rule')!=SCORE_VERSION:
                claim.update(score(documents[by[mid][0]],dict(claim=claim['claim'])))
                claim['short_reason']='本次答复未提供V3证据特征，当前仅作来源基础初评'
        credibility.update(event_score(claims,component))
        if not claims:credibility.update(score(doc))
        representative=min(component,key=lambda m:(entries[by[m][0]]['available_at'],m))
        representative_doc=documents[by[representative][0]]
        representative_entry=entries[by[representative][0]]
        event_relations=[p for ss in states.values() for p in (ss.get('relations',{}).get('result') or {}).get('proposals',[]) if set(p.get('member_ids',[])) & set(mids)]
        node_data={}
        if any(p.get('aggregation') and p.get('status')=='model_tentative' and set(p.get('member_ids',[]))<=set(mids) for p in event_relations):
            from event_workbench.aggregation_v2 import business_nodes
            node_data['business_nodes']=business_nodes(linked,by,entries,documents,previous)
            node_data['current_node']=next(n['id'] for n in node_data['business_nodes'] if newest in n['members'])
        participants=copy.deepcopy(mention['body'].get('participants',[]))
        for i,participant in enumerate(participants):
            proofs,issues,_=dossiers.selected_proofs(doc,jid,mention,f'participants[{i}]',participant)
            participant['evidence']=[dict(p,source_row=entry['source_row']) for p in proofs] if not issues else []
        return dict(event_identity='；'.join(d['value_text'] for d in mention['body'].get('details',[]) if d.get('name') in ('项目','药物','政策文件','交易标的')),record_kind='event',review_status='ai_assessed',material_count=len({by[m][0] for m in mids}),
                    progress_count=len(linked),updated_at=representative_doc['publication_time'] or representative_entry['available_at'],
                    update_time_basis='disclosure' if representative_doc['publication_time'] else 'received_date_unknown',
                    member_id=event_id,job_id=entry['source_row'],available_at=max(entries[by[m][0]]['available_at'] for m in mids),
                    candidate_only=False,confirmed=False,assessment_type='AI初判',original_index=mention['original_index'],
                    source_status=mention['body'].get('status',{}),event_summary=dict(text=mention['body'].get('summary')),
                    event_subjects=dict(participants=participants,standard_entity=objects,
                                        uncertainties=copy.deepcopy(mention['body'].get('uncertainties',[])),
                                        standardization_status='原文业务对象及可用官方分类的模型初判，保留引用与缺口'),
                    monitoring_scope=mention.get('monitoring_scope',{}),credibility=credibility,
                    emotions=copy.deepcopy(mention.get('emotions',[])),
                    judgment_history=judgment_history,assessment_source=dict(source_row=entry['source_row'],mention_id=mention['id'],
                        summary=mention['body'].get('summary'),assessment_cutoff=credibility['assessment_cutoff']),
                    evidence_sources=dict(original=representative_doc,original_quotes=quotes,members=members,source_rows=sorted(source_rows)),
                    event_facts=facts,event_timeline=dict(items=timeline,**node_data,**({'business_order':time_order} if time_order else {})),
                    event_relations=event_relations,
                    processing={s:states[jid][s]['status'] for s in ('extract','mentions','verification','relations','impact')},
                    warning_reasons=dict(decisions=impacts),saved_source=dict(sha256=core.digest([mids,states[jid]])))

    def _bind_claim_sources(self,claims,cutoff=None):
        sources=set()
        for claim in claims:
            for assessment in claim.get('valid_assessments',[]):
                if not assessment.get('proof'):continue
                entry=self.scope.entry(assessment['proof']['document_id'])
                if cutoff:self.scope.document(entry['source_row'],cutoff=cutoff)
                sources.add(entry['source_row']);assessment['source_row']=entry['source_row']
                assessment['source_nature']=entry.get('source_check',{}).get('nature','unknown')
                assessment['source_check']=copy.deepcopy(entry.get('source_check',dict(status='not_checked',independence='not_established')))
                assessment['source_url']=entry.get('source_url')
        return sources

    def _reminders(self,c,dto,changed):
        result=[]
        current_ids=set()
        for d in dto['warning_reasons']['decisions']:
            from event_workbench import alert_rules, alert_review
            if d.get('rule_version')==alert_rules.VERSION:
                item=alert_review.publish(c,self.run_id,dto,d,cause='review_update')
                if item.get('id'):current_ids.add(item['id'])
                result.append(item)
                continue
            item=copy.deepcopy(d)
            rid='notice-'+core.digest([self.run_id,dto['member_id'],d.get('target'),reminder_scope(d)])[:24]
            if rid in current_ids:continue
            current_ids.add(rid)
            old=c.execute('SELECT * FROM news_reminder_versions WHERE run_id=? AND id=? ORDER BY version DESC LIMIT 1',(self.run_id,rid)).fetchone()
            substance={k:item.get(k) for k in ('target','level','route','reasons','missing','alert')}
            substance['scope']=reminder_scope(item)
            sig=core.digest(substance)
            if not old and not d.get('alert'):result.append(item);continue
            if old and old['signature']==sig:
                result.append(json.loads(old['body']));continue
            item.update(id=rid,version=old['version']+1 if old else 1,event_id=dto['member_id'],
                        event_version=dto['event_version'],external_notification=False,
                        change='更正' if old else '首次提醒',active=bool(d.get('alert')))
            c.execute('INSERT INTO news_reminder_versions VALUES(?,?,?,?,?,?,?)',(self.run_id,rid,item['version'],dto['member_id'],sig,corpus.js(item),corpus.now()))
            result.append(item)
        latest={}
        for old in c.execute('SELECT * FROM news_reminder_versions WHERE run_id=? AND event_id=? ORDER BY version DESC',(self.run_id,dto['member_id'])):
            latest.setdefault(old['id'],old)
        for rid,old in latest.items():
            if rid in current_ids:continue
            item=json.loads(old['body'])
            if item.get('active'):
                item.update(version=old['version']+1,event_version=dto['event_version'],active=False,alert=False,
                            route='observation',change='更正',reasons=['原对象效果已移除、归属改变或当前阶段不可用'],
                            missing=['当前有效对象效果'],external_notification=False)
                signature=core.digest({k:item.get(k) for k in ('target','scope','route','reasons','missing','alert')})
                c.execute('INSERT INTO news_reminder_versions VALUES(?,?,?,?,?,?,?)',
                          (self.run_id,rid,item['version'],dto['member_id'],signature,corpus.js(item),corpus.now()))
            result.append(item)
        return result
