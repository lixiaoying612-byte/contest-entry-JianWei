"""One guarded news entry point for chronological batches and incremental arrivals."""
import copy
import json
from time import perf_counter
from datetime import datetime
from pathlib import Path
from event_workbench import development_request as requests
from event_workbench import product_downstream as downstream, unified_pipeline as core
from event_workbench import news_mvp, news_competition
from event_workbench import competition_enrichment,baseline_emotion,competition_candidates
from event_workbench import news_receivers_v3

from event_workbench import news_business
from event_workbench import news_joint
from event_workbench import training_corpus as corpus
V3_CONTRACTS=(baseline_emotion.VERSION,news_business.VERSION,news_joint.VERSION)
COMPETITION_CONTRACTS=(news_competition.VERSION,*V3_CONTRACTS)
MVP_CONTRACTS=(news_mvp.VERSION,*COMPETITION_CONTRACTS)
from event_workbench.product_workbench import Product


def _time(value):
    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
    if parsed.tzinfo is None:raise ValueError('available_at_requires_timezone')
    return parsed


def _save(path,value):
    """Write once; a restart may reproduce an identical local preparation."""
    path=Path(path);data=json.dumps(value,ensure_ascii=False,sort_keys=True,indent=2).encode()
    if path.exists():
        if json.loads(path.read_text('utf-8'))!=value:raise ValueError('artifact_binding_changed')
        return
    requests.immutable(path,data)






class _NotSent(ValueError):pass


def _send(budget,stage,request,folder):
    started=perf_counter()
    try:return budget.send(stage,request,folder)
    except ValueError as error:
        if str(error) in ('model_preflight_failed_not_sent','budget_or_batch_limit'):
            raise _NotSent(str(error)) from None
        raise
    finally:
        path=Path(folder)/'transport-timing.json'
        if path.parent.exists() and not path.exists():
            try:path.write_text(json.dumps(dict(stage=stage,elapsed_ms=round((perf_counter()-started)*1000),scope='transport_or_saved_response_lookup')),encoding='utf-8')
            except OSError:pass  # Optional timing must not mask a saved response or its real error.


class _BudgetAdapters:
    def __init__(self,*args,**kwargs):
        raise ValueError('legacy_news_execution_removed_use_current_analysis')
    def transmit(self,stage,ctx,envelope):
        raise ValueError('legacy_news_execution_removed_use_current_analysis')


class _NewsPipeline(downstream.Pipeline):
    def _signature(self,c,jid,stage,states):
        signature=super()._signature(c,jid,stage,states)
        row=c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()
        binding=json.loads(row[0]) if row else {}
        revision=binding.get('contract_revision')
        if revision==news_joint.VERSION:
            signature=core.digest([signature,news_joint.VERSION,news_joint.METHOD,news_joint.RECOGNITION,news_joint.JOINT,
                binding.get('identity_directory_version'),binding.get('identity_reviewed_at')])
        signature=core.digest([signature,revision]) if revision else signature
        if stage=='extract' and revision in MVP_CONTRACTS and binding.get('core_retention_version'):
            signature=core.digest([signature,binding['core_retention_version']])
        if stage=='extract' and revision in V3_CONTRACTS:
            prompt=news_business.RULES if binding.get('identity_contract_revision')==2 else news_business.LEGACY_RULES
            signature=core.digest([signature,competition_enrichment.VERSION,prompt if revision==news_business.VERSION else news_mvp.RULES])
        if stage=='extract' and revision==news_business.VERSION:
            signature=core.digest([signature,binding.get('identity_directory_version'),binding.get('identity_reviewed_at')])
        if stage=='extract' and revision in (news_mvp.VERSION,baseline_emotion.VERSION):
            signature=core.digest([signature,news_mvp.REQUEST_OPTIONS])
        if stage=='mentions' and revision in V3_CONTRACTS:
            signature=core.digest([signature,news_business.EMOTION_RULES if revision==news_business.VERSION else baseline_emotion.RULES])
        if stage=='extract' and revision in (baseline_emotion.VERSION,news_competition.VERSION):
            signature=core.digest([signature,competition_candidates.VERSION])
        if stage=='mentions' and revision=='news-contract-v3':
            signature=core.digest([signature,requests.monitoring_consistency.SCOPE_ADMISSION_VERSION])
        if stage=='mentions' and revision==news_competition.VERSION:
            signature=core.digest([signature,competition_enrichment.VERSION])
        if stage=='verification':signature=core.digest([signature,self.verification_method])
        if stage=='impact' and revision in MVP_CONTRACTS:signature=core.digest([signature,self.impact_method])
        if stage=='relations' and revision in V3_CONTRACTS:signature=core.digest([signature,news_receivers_v3.VERSION])
        retry=binding.get('not_sent_continuations',{}).get(stage)
        return core.digest([signature,retry]) if retry else signature

    def _write(self,c,jid,stage,status,reason,input_sha,result=None,provenance=None):
        if status=='failed' and (result or {}).get('processing_status')=='not_sent' and provenance:
            # Keep the attempt and its original reservation in output; count zero real sends.
            c.execute('UPDATE attempts SET cost=0 WHERE id=? AND owner=?',(provenance.get('request_identity'),provenance.get('owner')))
        return super()._write(c,jid,stage,status,reason,input_sha,result,provenance)


class NewsProcessor:
    MODEL={'extract','mentions','verification','relations','impact'}
    CONTRACTS={'news-contract-v1':('literal-fields-short-codes-v3','named-monitoring-v4'),
               'news-contract-v2':('core-literal-v5','named-monitoring-v5'),
               'news-contract-v3':('core-literal-v5','named-monitoring-v5'),
               news_mvp.VERSION:('core-literal-v5','local-mvp'),
               news_competition.VERSION:('core-literal-v5','independent-enrichment'),
               baseline_emotion.VERSION:('core-literal-v5','baseline-independent-emotion'),
               news_business.VERSION:('core-literal-v5','business-object-emotion'),
               news_joint.VERSION:('core-literal-v5','recognition-and-joint-review')}
    def __init__(self,app,scope,budget,artifact_root,*,plan_id,send_model=False,contract_revision=news_joint.VERSION,allowed_stages=None,evidence_catalog=None,impact_cause='new_material'):
        if contract_revision!=news_joint.VERSION:raise ValueError('legacy_execution_retired_use_explicit_contract_upgrade')
        self.contract_revision=contract_revision
        self.timings=[]
        self.app=app;self.scope=scope;self.budget=budget;self.root=Path(artifact_root);self.send_model=send_model;self.plan_id=plan_id
        self.evidence_catalog=copy.deepcopy(evidence_catalog or {});self.impact_cause=impact_cause
        adapters={s:dict(cost=1 if s in self.MODEL else 0,
                         prepare=lambda ctx,s=s:self._prepare(s,ctx),execute=lambda ctx,envelope,s=s:self._execute_guarded(s,ctx,envelope)) for s in core.STAGES}
        if contract_revision in MVP_CONTRACTS:adapters['mentions']['cost']=0
        # Evaluation may stop at an existing stage without replacing its algorithm.
        stages=list(core.STAGES) if allowed_stages is None else list(allowed_stages)
        if not set(stages)<=set(core.STAGES) or any(not set(core.DEPS[s])<=set(stages) for s in stages):
            raise ValueError('evaluation_stages_require_dependencies')
        plan=dict(id=plan_id,max_new_calls=2,allowed_stages=stages)
        if contract_revision==news_joint.VERSION:
            plan.update(stage_order=news_joint.ORDER,max_new_calls=2)
            for stage in ('mentions','relations','impact'):adapters[stage]['cost']=0
        self.pipeline=_NewsPipeline(app.path,adapters,plan)
        self.pipeline.verification_method=dict(version=news_joint.VERSION,method=news_joint.METHOD,catalog_sha=core.digest(self.evidence_catalog))
        self.pipeline.impact_method=core.digest([news_joint.METHOD,news_receivers_v3.alert_rules.VERSION,impact_cause])

    def _binding(self,jid):
        with self.pipeline.connect() as c:
            row=c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()
        if not row:raise ValueError('missing_news_source_binding')
        value=json.loads(row[0])
        if value.get('processor')!='news-processing-v1':raise ValueError('foreign_job_no_news_binding')
        return value

    def process(self,identity):
        self.check_deadline()
        entry=self.scope.entry(identity);cutoff=entry['available_at'];_time(cutoff)
        document=self.scope.document(identity,cutoff=cutoff);document=downstream.validate_document(document);jid=core.digest(document)
        binding=dict(processor='news-processing-v1',source_row=entry['source_row'],available_at=cutoff)
        with self.pipeline.connect() as c:
            old=c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()
            if old:
                binding=json.loads(old[0])
                if binding.get('processor')!='news-processing-v1':raise ValueError('news_job_source_binding_changed')
            if not old:
                binding['contract_revision']=self.contract_revision
                if self.contract_revision==news_joint.VERSION:binding['method_revision']=news_joint.METHOD
                if self.contract_revision in (news_business.VERSION,news_joint.VERSION):binding['identity_contract_revision']=2
                if self.contract_revision in MVP_CONTRACTS:binding['core_retention_version']=news_mvp.CORE_RETENTION_VERSION
            if not old and c.execute('SELECT 1 FROM jobs WHERE id=?',(jid,)).fetchone():raise ValueError('existing_job_requires_explicit_migration')
            c.execute('INSERT OR IGNORE INTO product_dependencies VALUES(?,?)',(jid,json.dumps(binding,ensure_ascii=False)))
            source=dict(job=jid,source_row=entry['source_row'],available_at=cutoff,document_sha=jid)
            key='news-source:'+core.digest([jid,entry['source_row']]);saved=c.execute('SELECT sha FROM imports WHERE id=?',(key,)).fetchone()
            if saved and saved[0]!=core.digest(source):raise ValueError('source_row_metadata_changed')
            c.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?)',(key,core.digest(source),json.dumps(source,ensure_ascii=False)))
        self._resume_not_sent(jid)
        self.pipeline.submit(document,background=False)
        # Joint review already covers the affected archives; never start extra old-job model rounds.
        with self.pipeline.connect() as c:
            states=self.pipeline._states(c,jid)
            sources=[json.loads(r[0]) for r in c.execute("SELECT body FROM imports WHERE id LIKE 'news-source:%'") if json.loads(r[0]).get('job')==jid]
        return dict(id=jid,document=document,source_row=binding['source_row'],source_rows=sources,available_at=binding['available_at'],stages=states,
                    confirmed=False,processing_version='news-processing-v1',related_updates=[])

    def restart_incomplete(self, identity, attempt_id):
        """An explicit new attempt keeps good extraction and archives replaced stages."""
        entry=self.scope.entry(identity)
        jid=core.digest(self.scope.document(identity,cutoff=entry['available_at']))
        with self.pipeline.connect() as c:
            c.execute('BEGIN IMMEDIATE')
            row=c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()
            if not row:return
            key='news-explicit-retry:'+attempt_id
            if c.execute('SELECT 1 FROM imports WHERE id=?',(key,)).fetchone():return
            binding=json.loads(row[0]);states=self.pipeline._states(c,jid)
            prior=dict(binding=copy.deepcopy(binding),stages=states,attempt_id=attempt_id)
            c.execute('INSERT INTO imports VALUES(?,?,?)',(key,core.digest(prior),json.dumps(prior,ensure_ascii=False)))
            keep={'extract','mentions'} if states['extract']['status'] in ('ready','cached') else set()
            if states['mentions']['status'] not in ('ready','cached','not_applicable'):keep.discard('mentions')
            counters=binding.setdefault('not_sent_continuations',{})
            for stage in core.STAGES:
                if stage in keep:continue
                counters[stage]=counters.get(stage,0)+1
                self.pipeline._write(c,jid,stage,'waiting','explicit_manual_retry',None,None,dict(attempt_id=attempt_id))
            c.execute('UPDATE product_dependencies SET body=? WHERE job=?',(json.dumps(binding,ensure_ascii=False),jid))

    def upgrade_user_contract(self, identity, *, refresh_identity=False):
        """Explicit submit under the new service contract, retaining all old attempts."""
        if self.contract_revision not in (news_business.VERSION,news_joint.VERSION):return
        entry=self.scope.entry(identity);document=self.scope.document(identity,cutoff=entry['available_at'])
        jid=core.digest(document)
        with self.pipeline.connect() as c:
            row=c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()
            if not row:return
            binding=json.loads(row[0]);old=binding.get('contract_revision')
            method_changed=self.contract_revision==news_joint.VERSION and binding.get('method_revision')!=news_joint.METHOD
            if old==self.contract_revision and not refresh_identity and not method_changed:return
            states=self.pipeline._states(c,jid)
            if any(s['status'] in ('running','unknown_no_retry') for s in states.values()):raise ValueError('previous_send_requires_review_before_contract_upgrade')
            if method_changed:
                prior=dict(binding=binding,stages=states,explicit_submit_required=True)
                key='analysis-method-history:'+core.digest([jid,prior])
                c.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?)',(key,core.digest(prior),json.dumps(prior,ensure_ascii=False)))
            binding.update(contract_revision=self.contract_revision,previous_contract=old,core_retention_version=news_mvp.CORE_RETENTION_VERSION)
            if self.contract_revision==news_joint.VERSION:binding.update(method_revision=news_joint.METHOD,previous_method_revision=binding.get('method_revision'))
            binding['identity_contract_revision']=2
            if refresh_identity:
                from event_workbench import security_identity
                binding.update(identity_reviewed_at=corpus.now(),identity_directory_version=(security_identity.load() or {}).get('version'))
            c.execute('UPDATE product_dependencies SET body=? WHERE job=?',(json.dumps(binding,ensure_ascii=False),jid))
            for stage in core.STAGES:self.pipeline._write(c,jid,stage,'waiting','explicit_user_contract_upgrade',None,None,dict(previous_contract=old,contract_revision=self.contract_revision))


    def _related_groups(self,cutoff):
        """Only current, source-checked relation opinions may connect evidence."""
        documents={};states={};bindings={}
        with self.pipeline.connect() as c:
            rows=c.execute('SELECT j.id,j.document,d.body FROM jobs j JOIN product_dependencies d ON d.job=j.id').fetchall()
            withdrawn={r[0] for r in c.execute('SELECT id FROM withdrawals')}
            for row in rows:
                binding=json.loads(row['body'])
                if binding.get('processor')!='news-processing-v1' or _time(binding['available_at'])>_time(cutoff):continue
                try:document=self.scope.document(binding['source_row'],cutoff=cutoff)
                except (ValueError,KeyError):continue
                if document!=json.loads(row['document']):raise ValueError('related_source_changed')
                documents[row['id']]=document;bindings[row['id']]=binding;states[row['id']]=self.pipeline._states(c,row['id'])
        guarded=downstream.guarded_states(states,documents)
        mentions={m['id']:(jid,m) for jid,ss in guarded.items()
                  if ss['extract']['status'] in core.GOOD and ss['mentions']['status'] in core.GOOD
                  for m in (ss['mentions'].get('result') or {}).get('mentions',[])}
        proposals=[];catalog={jid:dict(document=doc) for jid,doc in documents.items()}
        for ss in guarded.values():
            if ss['relations']['status'] not in core.GOOD:continue
            for p in (ss['relations'].get('result') or {}).get('proposals',[]):
                if p.get('id') in withdrawn or p.get('status')!='model_tentative' or p.get('issues'):continue
                mids=p.get('member_ids',[])
                if len(mids)!=2 or not set(mids)<=mentions.keys() or not p.get('original_proofs'):continue
                try:
                    for proof in p['original_proofs']:downstream.dossiers.verify_relation_proof(proof,catalog)
                    if not {mentions[m][0] for m in mids}<={q['document_id'] for q in p['original_proofs']}:continue
                except (ValueError,KeyError,TypeError):continue
                proposals.append(p)
        graph=core.relations.current_graph(list(mentions),proposals)
        groups=[set(g['members']) for g in graph['groups']]
        different=[set(p['member_ids']) for p in graph['proposals'] if p['raw']['k']=='different_event']
        for p in graph['proposals']:
            if p['status']!='model_tentative' or p['raw']['k']!='same_project_new_progress':continue
            linked=[g for g in groups if g&set(p['member_ids'])];merged=set.union(*linked)
            if any(pair<=merged for pair in different):continue
            groups=[g for g in groups if g not in linked]+[merged]
        return documents,bindings,mentions,groups

    def _evaluation(self,jid,stage):
        binding=self._binding(jid);cutoff=binding['available_at'];context=None
        if binding.get('identity_reviewed_at') and _time(binding['identity_reviewed_at'])>_time(cutoff):cutoff=binding['identity_reviewed_at']
        if stage in ('retrieval','verification','impact'):
            with self.pipeline.connect() as c:row=c.execute('SELECT body FROM product_retrieval_context WHERE job=?',(jid,)).fetchone()
            if row:
                context=json.loads(row[0]);cutoff=max((cutoff,context['evaluation_time']),key=_time)
                if context.get('version')!='news-related-evidence-v1' or _time(cutoff)<_time(binding['available_at']):raise ValueError('invalid_news_evaluation_context')
                _,_,members,groups=self._related_groups(cutoff)
                for source in context['related_sources']:
                    document=self.scope.document(source['source_row'],cutoff=cutoff)
                    if core.digest(document)!=source['document_id'] or core.digest(document['text'])!=source['body_sha256']:raise ValueError('related_evidence_changed')
                    targets={mid for mid,(job,m) in members.items() if job==jid and m['original_index']==source['parent_original_index']}
                    if not any(g&targets and any(members[mid][0]==source['document_id'] for mid in g) for g in groups):raise ValueError('related_evidence_relation_no_longer_eligible')
        return cutoff,context

    def _resume_not_sent(self,jid):
        with self.pipeline.connect() as c:
            c.execute('BEGIN IMMEDIATE');states=self.pipeline._states(c,jid)
            if not states:return
            binding=json.loads(c.execute('SELECT body FROM product_dependencies WHERE job=?',(jid,)).fetchone()[0])
            for stage,state in states.items():
                not_sent=state['status'] in ('failed','partial') and (state.get('result') or {}).get('processing_status') in ('not_sent','not_sent_remaining')
                receiver_changed=stage=='impact' and state['status']=='failed' and (state.get('result') or {}).get('receiver') not in (None,news_receivers_v3.VERSION)
                # Repair previously frozen false-unknown records only when the budget ledger proves zero sends.
                if state['status']=='unknown_no_retry' and c.execute("SELECT 1 FROM sqlite_master WHERE name='news_model_failures'").fetchone():
                    aid=(state.get('provenance') or {}).get('request_identity')
                    attempt=c.execute('SELECT request FROM attempts WHERE id=?',(aid,)).fetchone()
                    request=json.loads(attempt[0]).get('request') if attempt else None
                    if request:
                        sha=core.digest(request)
                        sent=c.execute('SELECT 1 FROM news_model_calls WHERE request_sha=? AND stage=?',(sha,stage)).fetchone()
                        failure=c.execute("SELECT * FROM news_model_failures WHERE request_sha=? AND stage=? AND status IN ('preflight_failed_not_sent','budget_blocked_not_sent')",(sha,stage)).fetchone()
                        not_sent=bool(failure and not sent)
                        if not_sent:
                            original=dict(c.execute('SELECT * FROM attempts WHERE id=?',(aid,)).fetchone())
                            audit=dict(original_attempt=original,budget_failure=dict(failure),matching_budget_call_rows=0,actual_model_calls=0)
                            c.execute('INSERT OR IGNORE INTO imports VALUES(?,?,?)',('news-zero-send:'+aid,core.digest(audit),json.dumps(audit,ensure_ascii=False)))
                            c.execute('UPDATE attempts SET cost=0 WHERE id=?',(aid,))
                if not not_sent and not receiver_changed:continue
                if not_sent:
                    counters=binding.setdefault('not_sent_continuations',{});counters[stage]=counters.get(stage,0)+1
                self.pipeline._write(c,jid,stage,'waiting','explicit_receiver_revision_reassessment' if receiver_changed else 'proven_not_sent_continuation',None,None,
                                     dict(previous_attempt=state.get('provenance'),previous_result=state.get('result'),receiver=news_receivers_v3.VERSION) if receiver_changed else dict(previous_attempt=state.get('provenance'),actual_prior_model_calls=0))
            c.execute('UPDATE product_dependencies SET body=? WHERE job=?',(json.dumps(binding,ensure_ascii=False),jid))

    def _execute_guarded(self,stage,ctx,envelope):
        started=perf_counter()
        try:
            self.check_deadline()
            result=self._execute(stage,ctx,envelope)
            self.check_deadline()
            return result
        except _NotSent as error:
            return dict(status='failed',reason=str(error),result=dict(processing_status='not_sent',model_calls=0,
                        reserved_pipeline_cost=self.pipeline.adapters[stage]['cost'],error=str(error)))
        finally:self.timings.append(dict(stage=stage,phase='execute_and_validate',elapsed_ms=round((perf_counter()-started)*1000)))



    def batch(self,identities):
        ordered=sorted(identities,key=lambda i:(_time(self.scope.entry(i)['available_at']),self.scope.entry(i)['source_row']))
        return [self.process(identity) for identity in ordered]

    def _folder(self,stage,ctx):return self.root/ctx['job'][:16]/stage/ctx['input_signature'][:16]



    def _prepare(self,stage,ctx):
        started=perf_counter()
        try:
            self.check_deadline()
            envelope=self._prepare_stage(stage,ctx)
            self.check_deadline()
        finally:self.timings.append(dict(stage=stage,phase='prepare',elapsed_ms=round((perf_counter()-started)*1000)))
        continuation=self._binding(ctx['job']).get('not_sent_continuations',{}).get(stage)
        # All extraction routes must receive a new attempt identity after proven zero send.
        if continuation and envelope and not envelope.get('deferred_reason'):
            envelope=dict(envelope,not_sent_continuation=continuation)
        return envelope

    def check_deadline(self):
        if hasattr(self.budget, 'remaining'):self.budget.remaining()

    def _prepare_stage(self,stage,ctx):
        binding=self._binding(ctx['job']);cutoff,evaluation=self._evaluation(ctx['job'],stage)
        revision=binding.get('contract_revision','news-contract-v1')
        if revision!=news_joint.VERSION:raise ValueError('legacy_execution_retired_use_explicit_contract_upgrade')
        # Recheck the source guard on every stage, including resumed jobs.
        if self.scope.document(binding['source_row'],cutoff=cutoff)!=ctx['document']:raise ValueError('source_changed_before_stage')
        if revision==news_joint.VERSION and stage in ('extract','mentions','verification','relations','impact'):
            if stage=='extract':
                from event_workbench import competition_candidates
                prepared=self.scope.prepare(binding['source_row'],content_only=True)
                if prepared['plan']['document']!=ctx['document']:raise ValueError('joint_source_binding_changed')
                chunks=[dict(content=r['content'],mapping=r.get('source_mapping') if r.get('block_id') else None,block_id=r.get('block_id')) for r in prepared['extraction_input']['requests']]
                chunks=competition_candidates.literal_fallback(chunks)
                from event_workbench import security_identity
                chunks=security_identity.prepare(chunks,cutoff)
                envelope=news_joint.prepare_recognition(self,ctx,prepared,chunks)
                self.pipeline.adapters[stage]['cost']=len(chunks)
            elif stage=='verification':envelope=news_joint.prepare_joint(self,ctx,cutoff)
            else:envelope=dict(receiver=news_joint.VERSION,local_stage=stage)
            _save(self._folder(stage,ctx)/'prepared.json',envelope)
            if not self.send_model and stage in ('extract','verification'):return dict(deferred_reason='model_not_authorized_for_this_invocation')
            return envelope
        return dict(local_stage=stage,input_signature=ctx['input_signature'])

    def _execute(self,stage,ctx,envelope):
        binding=self._binding(ctx['job']);cutoff,evaluation=self._evaluation(ctx['job'],stage);folder=self._folder(stage,ctx)
        if binding.get('contract_revision')==news_joint.VERSION and stage in ('extract','mentions','verification','relations','impact'):
            return news_joint.execute(self,stage,ctx,envelope,cutoff)
        if stage=='retrieval':
            mentions=ctx['dependencies']['mentions']['mentions']
            retrieved=self.scope.retrieve(ctx['document'],mentions,cutoff)
            if retrieved.get('evaluation_time')!=cutoff:raise ValueError('retrieval_cutoff_changed')
            if evaluation:retrieved=dict(retrieved,cutoff=binding['available_at'],update_context=evaluation)
            def lookup(identity,source_row=None):
                return self.scope.document(source_row['row_key'] if source_row else identity,cutoff=cutoff)
            supplements=[dict(id=s['document_id'],document=self.scope.document(s['source_row'],cutoff=cutoff),parent_original_index=s['parent_original_index']) for s in (evaluation or {}).get('related_sources',[])]
            batch=downstream.material_batch(ctx,retrieved,lookup,supplements)
            if binding.get('contract_revision') in MVP_CONTRACTS:
                # Original units are already source checked, but the legacy candidate
                # lists omit them. Make them selectable without treating them as
                # independent corroboration or changing any received assessment.
                own=[ref for ref,m in batch['mapping']['materials'].items() if m.get('relation')=='own_document']
                for candidate in batch['content']['candidates']:
                    candidate['materials']=list(dict.fromkeys(candidate['materials']+own))
            result=dict(retrieved=retrieved,batch=batch,materials=batch['mapping']['materials'],assessment_cutoff=batch['cutoff'],cutoff_context=batch['content']['evidence_context'])
        else:
            history=[];documents={ctx['job']:dict(document=ctx['document'],source_family=None)}
            with self.pipeline.connect() as c:
                rows=c.execute("SELECT j.id,j.document,s.result,d.body FROM jobs j JOIN stages s ON s.job=j.id JOIN product_dependencies d ON d.job=j.id WHERE s.stage='mentions' AND s.status IN ('ready','cached','partial') AND j.id<>?",(ctx['job'],)).fetchall()
            for row in rows:
                old_binding=json.loads(row['body'])
                if old_binding.get('processor')!='news-processing-v1' or _time(old_binding['available_at'])>_time(cutoff):continue
                try:doc=self.scope.document(old_binding['source_row'],cutoff=cutoff)
                except (ValueError,KeyError):continue
                if doc!=json.loads(row['document']):raise ValueError('historical_source_changed')
                history.extend(json.loads(row['result'])['mentions']);documents[row['id']]=dict(document=doc,source_family=None)
            forced=[]
            related=self.scope.entry(binding['source_row']).get('related_event_id')
            if related:
                with self.pipeline.connect() as c:
                    event=c.execute('SELECT body FROM replay_events WHERE id=? AND run_id=?',(related,self.scope.run_id)).fetchone()
                if event:
                    saved=json.loads(event[0]);forced=[m['mention_id'] for m in saved.get('evidence_sources',{}).get('members',[]) if m.get('mention_id')]
            with self.pipeline.connect() as c:
                from event_workbench.news_events import current_member_owner
                matter_ids={}
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='news_event_members'").fetchone():
                    for mention in ctx['dependencies']['mentions']['mentions']+history:
                        owner=current_member_owner(c,self.scope.run_id,mention)
                        if owner:matter_ids[mention['id']]=owner
            result=downstream.relation_batch(ctx['dependencies']['mentions']['mentions'],history,documents,forced=forced,matter_ids=matter_ids)
        _save(folder/'checked.json',result)
        return dict(status='ready',result=result,reason='guarded_local_stage_completed')

