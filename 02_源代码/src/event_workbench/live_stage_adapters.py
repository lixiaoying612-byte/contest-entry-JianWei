"""Actual stage composition: pure prepared requests, one owner, original validators."""
import copy
import json
from pathlib import Path
from event_workbench import unified_pipeline as core
from event_workbench import compatible_document_pipeline as compatible
from event_workbench import verification_materials as vm
from event_workbench import current_verification as cv
from event_workbench import auto_impact as impact
from event_workbench import incremental_relation_binding as binding
trial=core.documents.trial;save=core.documents.save;read=core.documents.read;digest=core.digest


class Adapters:
    MODEL={'extract','verification','relations','impact'}
    def __init__(self,root,history,policy,prompts,send=None,key=None,local=None):
        self.root=Path(root);self.history=history;self.policy=policy;self.prompts=prompts;self.send=send;self.key=key;self.local=local
    def engine(self):raise RuntimeError('legacy_news_retrieval_removed_use_current_analysis')
    def folder(self,stage,ctx):return self.root/'prepared'/ctx['job']/stage/ctx['input_signature']
    def job(self,ctx):
        # Stage dependencies carry the original extraction view, not fixed document branches.
        m=ctx['dependencies'].get('mentions')
        return dict(id=ctx['job'],document=ctx['document'],prepared=m['prepared'],preview=m['preview'])
    def model_request(self,stage,content):
        r=trial.payload('J',content if stage=='extract' else {})
        if stage!='extract':r.update(max_tokens=4096,messages=[dict(role='system',content=self.prompts[stage]),dict(role='user',content=json.dumps(content,ensure_ascii=False,separators=(',',':')))])
        trial.client.preflight(r)
        if len(trial.wire_bytes(r))>48000:raise ValueError('request_over_48000_no_truncation')
        return r
    def prepare(self,stage,ctx):
        folder=self.folder(stage,ctx);d=ctx['dependencies'];batch=None;result=None
        if stage=='extract':
            retrieved=self.engine().official(ctx['document']);rep,pos=trial.compact.source_representation(ctx['document'])
            batch=dict(local_id=ctx['job'],document=ctx['document'],representation=rep,source_positions=pos,candidates=retrieved['candidates'],retrieval=retrieved,coverage='full_input',metadata_warnings=[])
            request=self.model_request(stage,dict(rep,c=batch['candidates']))
        elif stage=='mentions':
            e=d['extract'];preview=e['preview'];ms=[dict(id=digest([ctx['job'],e['original_index']]),document_id=ctx['job'],original_index=e['original_index'],body=e['facts'],field_warnings=e['field_warnings']) for e in preview['events']]
            result=dict(mentions=ms,prepared=e['prepared'],preview=preview)
        elif stage=='retrieval':
            job=self.job(ctx);retrieved=self.engine().materials(job);batch=vm.batch_input(job,retrieved)
            result=dict(materials=batch['mapping']['materials'],batch=batch,retrieved=retrieved)
        elif stage=='verification':
            batch=d['retrieval']['batch'];request=self.model_request(stage,batch['content'])
        elif stage=='relation_candidates':
            incoming=d['mentions']['mentions'];history=[m for m in self.history['mentions'] if m['document_id']!=ctx['job']]
            documents=dict(self.history['documents']);documents[ctx['job']]=dict(document=ctx['document'],source_family=None)
            result=self.engine().relations(incoming,history,documents)
        elif stage=='relations':
            batch=d['relation_candidates']['batch'];request=self.model_request(stage,batch['content'])
        elif stage=='impact':
            job=self.job(ctx);job['verification']=d['verification']['verification']
            # Keep existing local effect checker/original own units. Replace its ambiguous
            # cross-document quote lookup with the source-aware claim package.
            batch=impact.prepare(job);aware=d['verification']['source_aware_impact_input']
            for m in batch['content']['mentions']:
                oi=batch['mapping'][str(m['mention'])]['original_index']
                m['verification']['claims']=[c for c in aware['claims'] if c.get('mention')==oi]
            batch['content']['verification_source_units']=[dict(id=u['id'],document_id=u['proof']['document_id'],document_sha256=u['proof']['document_sha256'],section=u['proof']['section'],start=u['proof']['start'],end=u['proof']['end'],text=u['proof']['quote'],source_family=u['source_family']) for u in aware['units']]
            batch['source_aware_mapping']=aware;request=self.model_request(stage,dict(batch['content'],policy=self.policy))
        if stage not in self.MODEL:request=dict(local_stage=stage,input_signature=ctx['input_signature'],result_sha256=digest(result))
        envelope=dict(request=request,batch=batch,result=result,input_signature=ctx['input_signature'],dependency_signatures=ctx['dependency_signatures'],document_sha256=digest(ctx['document']))
        save(folder/'prepared.json',envelope)
        if stage in self.MODEL:
            with (folder/'request.bin').open('xb') as f:f.write(trial.wire_bytes(request))
        save(folder/'binding.json',dict(envelope_sha256=digest(envelope),wire_sha256=trial.digest_bytes(trial.wire_bytes(request)),wire_bytes=len(trial.wire_bytes(request)),input_signature=ctx['input_signature'],dependency_signatures=ctx['dependency_signatures']))
        return envelope

    def transmit(self,stage,ctx,envelope):
        raise RuntimeError('legacy_news_execution_removed_use_current_analysis')

    def execute(self,stage,ctx,envelope):
        if stage not in self.MODEL:return dict(status='ready',result=envelope['result'],reason='实际本地派生/检索完成')
        r=self.transmit(stage,ctx,envelope);batch=envelope['batch'];result={}
        if r['status']=='unknown_no_retry':return dict(status='unknown_no_retry',reason='传输未知，本单停止后续发送',result=r)
        if not r['complete']:return dict(status='failed',reason='返回不完整或模式异常，不重试',result=r)
        try:
            if stage=='extract':
                wrapper=dict(raw_final_answer=r['raw_answer'],response_complete=r['complete'],identity=r['identity'],stage='J',finish_reason=r['finish_reason'])
                view,preview=compatible.compatible_view(wrapper,batch,dict(generation_identity=r['identity']))
                result=dict(original=wrapper,official_matching=preview['matching'],prepared=batch,view=view,preview=preview)
            elif stage=='verification':
                checked=cv.check(r['raw_answer'],batch,r['complete']);v=dict(r,checked=checked);job=self.job(ctx);job['verification']=v
                catalog={m['document']['local_id']:m['document'] for m in batch['mapping']['materials'].values()}
                result=dict(verification=v,source_aware_impact_input=core.source_aware_impact(job,catalog))
            elif stage=='relations':result=self.relation_check(r,batch)
            else:
                checked=impact.check(r['raw_answer'],batch,self.policy,r['complete']);result=dict(status='current',record=dict(checked=checked,original=r),confirmed=False)
            save(self.folder(stage,ctx)/'checked.json',result)
            return dict(status='partial',reason='实际模型原答及局部检查已保存；语义待复核',result=result)
        except (ValueError,TypeError,KeyError,IndexError) as e:return dict(status='failed',reason='完整原答局部检查失败，不重试：'+type(e).__name__,result=dict(original=r,error_type=type(e).__name__))

    @staticmethod
    def relation_check(r,batch):
        raw=trial.common.strict_json(r['raw_answer'])
        if not isinstance(raw,dict) or set(raw)!={'pairs'} or not isinstance(raw['pairs'],list):raise ValueError('relation_root')
        from event_workbench import aggregation_v2 as agg
        pairs={p['p']:p for p in batch['content']['pairs']};converted=[];isolated=[];semantics={}
        for index,row in enumerate(raw['pairs']):
            if not isinstance(row,dict) or set(row) not in ({'p','k','qa','qb','why'},{'p','k','qa','qb','why','aggregation'}) or type(row['p']) is not int or row['p'] not in pairs:
                isolated.append(dict(index=index,original=row,issue='unknown_candidate_or_fields'));continue
            if sum(isinstance(x,dict) and x.get('p')==row['p'] for x in raw['pairs'])!=1:
                isolated.append(dict(index=index,original=row,issue='duplicate_candidate_opinion'));continue
            p=pairs[row['p']]
            if 'aggregation' in row:
                try:semantics[row['p']]=agg.validate(row['aggregation'],p,batch,row)
                except (ValueError,TypeError,KeyError,IndexError) as error:
                    isolated.append(dict(index=index,original=row,issue=str(error)));continue
            elif batch.get('method_version')==agg.VERSION:
                isolated.append(dict(index=index,original=row,issue='aggregation_v2_required_for_new_batch'));continue
            converted.append(dict(p=row['p'],r=[dict(k=row['k'],qa=row['qa'],qb=row['qb'],why=row['why'],a=p['ai'],b=p['bi'])]))
        internal=dict(r,raw_answer=json.dumps(dict(pairs=converted),ensure_ascii=False),checked=dict(issues=[],proposals=[]))
        checked=binding.derive(internal,batch)
        for p in checked['outcomes']:
            p['original_compact_answer']=copy.deepcopy(raw);p['program_bound_endpoints']=True
            if p['pair'] in semantics:p['aggregation']=semantics[p['pair']]
        if batch.get('method_version')==agg.VERSION:agg.guard_assignments(checked['outcomes'],batch)
        per_mention=[]
        for coverage in batch.get('coverage',[]):
            ps=[p for p in checked['outcomes'] if p.get('member_ids',[None])[0]==coverage['mention']]
            complete=len(ps)==len(coverage['candidates']) and all(p['status']=='model_tentative' for p in ps)
            per_mention.append(dict(mention=coverage['mention'],status='complete' if complete else 'pending',reasons=[issue for p in ps for issue in p['issues']]))
        pending=bool(isolated or checked['missing_candidates'] or any(p['status']!='complete' for p in per_mention))
        return dict(proposals=checked['outcomes'],missing_candidates=checked['missing_candidates'],isolated=isolated,original=r,confirmed=False,
                    per_mention=per_mention,processing_status='partial' if pending and any(p['status']=='complete' for p in per_mention) else 'pending' if pending else 'complete')
