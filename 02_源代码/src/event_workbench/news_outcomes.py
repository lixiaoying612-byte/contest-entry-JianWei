"""Material disposition from saved stages; no semantic guesses or new processing."""
import json

VERSION='material-outcome-20261007-v4'

STAGE_LABELS=dict(extract='事项识别',mentions='对象分析',retrieval='证据查找',verification='主张核验',relation_candidates='关联查找',relations='关系判断',impact='影响分析')


def matter_items(c, job, run_id, visible_event_ids):
    """Explain saved mentions without turning retrieval candidates into event ownership."""
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='stages'").fetchone():return []
    states={r['stage']:json.loads(r['result']) for r in c.execute("SELECT stage,result FROM stages WHERE job=? AND result IS NOT NULL AND stage IN ('extract','mentions')",(job,))}
    mentions=states.get('mentions',{});pool={}
    row=c.execute("SELECT json_extract(result,'$.joint.failed_groups') FROM stages WHERE job=? AND stage='verification'",(job,)).fetchone()
    failures={i for g in json.loads(row[0] or '[]') for i in g['original_indices']} if row else set()
    for name in ('all_mentions','pending_mentions','supporting_mentions','mentions'):
        for mention in mentions.get(name,[]):pool[mention['id']]=mention
    result=[];qualified={m['id'] for m in mentions.get('mentions',[])}
    for mention in pool.values():
        body=mention.get('body',{});scope=mention.get('monitoring_scope',{});status=body.get('status',{});summary=body.get('summary','')
        code,label,reason='scope_pending','类别待核',(scope.get('unmatched') or {}).get('reason') or '尚未确认适用类别，不等于没有具体事件。'
        admission=scope.get('business_admission',{})
        if admission and not admission['independent']:
            code=admission['disposition'];label={'background':'背景材料','insufficient':'事项信息待补','out_of_scope':'无证券相关独立事项'}.get(code,'待处理');reason=admission['reason']
        owner=None
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='news_event_members'").fetchone():
            from event_workbench.news_events import current_member_owner
            owner=current_member_owner(c,run_id,mention)
        if status.get('context')=='background' or status.get('mode')=='question':
            code,label,reason='background','背景或问答线索','保留作背景，不单独形成当前事件。'
        elif owner in visible_event_ids:
            code,label,reason='archived','已形成档案','本事项已有可查看的档案；整篇其他事项仍可能待处理。'
            saved=c.execute('SELECT body FROM replay_events WHERE run_id=? AND id=?',(run_id,owner)).fetchone()
            event=json.loads(saved[0]) if saved else {}
            if event.get('identification_review') and event.get('assessment_source',{}).get('mention_id')==mention['id']:
                summary=event.get('event_summary',{}).get('text') or summary
        elif mention['id'] in qualified:
            code,label,reason='pending_archive','尚待完成分析','已识别事项，核验、影响或归档条件尚未满足。'
        if mention.get('original_index') in failures:
            label='原档保留；本次更新未完成' if owner in visible_event_ids else '本组尚未完成'
            reason='本组的主张、引用或影响判断未通过检查，整组尚未发布更新；原文和原有有效结果保留。其他独立事项按各自检查结果处理。'
        result.append(dict(index=mention.get('original_index'),summary=summary,code=code,label=label,reason=reason,event_id=owner if code=='archived' else None))
    known={(r['index'],r['summary']) for r in result}
    for part in states.get('extract',{}).get('admission',{}).get('parts',[]):
        for item in part.get('rejected',[]):
            index=item.get('original_index')
            original=item.get('original') or item.get('original_named_event') or {}
            identity=(index,original.get('summary','待检查的识别事项'))
            if identity in known:continue
            result.append(dict(index=index,summary=original.get('summary','待检查的识别事项'),code='needs_processing',label='需检查后采用',reason='该项答复未通过字段或引文检查，原答与具体原因保留在处理详情。',event_id=None))
            known.add(identity)
    return sorted(result,key=lambda r:r['index'] if isinstance(r['index'],int) else -1)


def pending_reanalysis(c,job,states):
    """A previously completed stage is being replaced, not withdrawn."""
    pending={s for s,v in states.items() if v.get('status') in ('waiting','running')}
    if not pending or not c.execute("SELECT 1 FROM sqlite_master WHERE name='product_stage_versions'").fetchone():return set()
    return pending & {r[0] for r in c.execute("SELECT DISTINCT stage FROM product_stage_versions WHERE job=? AND json_extract(body,'$.status') IN ('ready','cached','partial') AND json_extract(body,'$.result') IS NOT NULL",(job,))}


def outcome(states, event_ids=(), rejected=False):
    extract=(states.get('extract') or {}).get('result') or {}
    mentions=(states.get('mentions') or {}).get('result') or {}
    parts=extract.get('admission',{}).get('parts',[])
    routes=[p.get('screening',{}) for p in parts]
    kinds={p.get('outcome','undetermined') for p in routes}
    failed=rejected or any(s.get('status') in ('failed','partial','unknown_no_retry','stale','withdrawn') for s in states.values()) or any(p.get('rejected') or p.get('status') in ('failed','partial') for p in parts)
    # Older saved mentions predate supporting_mentions; use their checked roles.
    pool={m.get('id',m.get('original_index')):m for name in ('supporting_mentions','all_mentions','pending_mentions','mentions') for m in mentions.get(name,[])}
    support=[m for m in pool.values() if m.get('body',{}).get('status',{}).get('context')=='background' or m.get('body',{}).get('status',{}).get('mode')=='question']
    support_ids={s.get('id') for s in support}
    pending=[m for m in mentions.get('pending_mentions',[]) if m.get('id') not in support_ids]
    business_pending=[m for m in pending if m.get('monitoring_scope',{}).get('business_admission')]
    qualified=[m for m in mentions.get('mentions',[]) if m.get('id') not in support_ids]
    ids=sorted(set(event_ids));reason='；'.join(dict.fromkeys(p['reason'] for p in routes if p.get('reason')))
    unfinished=bool(ids or qualified) and any(s.get('status') in ('waiting','running') for s in states.values())
    if ids:
        code='partial' if failed or pending or unfinished else 'archived'
        label=f'已关联 {len(ids)} 个事件'+('；部分分析尚未完成，保留已有判断' if code=='partial' else '')
    elif failed:code,label='needs_processing','分析需处理，原文已保留'
    elif any(s.get('status')=='running' for s in states.values()):code,label='processing','正在分析'
    elif qualified:code,label='pending_archive','已识别具体事项，等待关联与归档'
    elif pending:
        code,label=('insufficient','事项信息待补') if business_pending else ('scope_pending','已识别线索，事项分类待核')
        if business_pending:reason='；'.join(dict.fromkeys(m['monitoring_scope']['business_admission']['reason'] for m in business_pending))
        dispositions={m['monitoring_scope']['business_admission']['disposition'] for m in business_pending}
        if len(business_pending)==len(pending) and dispositions<={'background','out_of_scope'}:
            code='background' if 'background' in dispositions else 'out_of_scope'
            label='已分析，仅作背景材料' if code=='background' else '已分析，无证券相关独立事项'
    elif kinds and kinds<={'no_event','background'} and all(p.get('status')=='ready' for p in parts):
        code='background' if 'background' in kinds else 'no_event';label='已分析，仅作背景材料' if code=='background' else '已分析，未发现需入档的具体事件'
    elif 'insufficient' in kinds:code,label='insufficient','需补充材料后判断'
    elif 'out_of_scope' in kinds:code,label='out_of_scope','已分析，无证券相关独立事项' if extract.get('version')=='news-business-v1' else '存在具体事项，当前目录未覆盖'
    elif support:code,label='background','已分析，仅作背景或问答线索'
    elif states:code,label='undetermined','尚未形成明确的材料判断'
    else:code,label='saved','新闻已保存，尚未分析'
    blocked=[dict(stage=name,label=STAGE_LABELS.get(name,name),status=s['status']) for name,s in states.items() if s.get('status') in ('failed','partial','unknown_no_retry','stale','withdrawn')]
    if blocked:
        step='、'.join(s['label'] for s in blocked)
        stopped=not any(s.get('status')=='running' for s in states.values())
        if stopped and not ids:label='分析已停止 · '+step+'未完成；原文已保留'
        explanation=step+'尚未通过处理；原文及已完成结果已保留。'+('本次处理已停止，继续等待不会自动完成。' if stopped else '')
        account_reason=next((s.get('public_reason') for s in states.values() if s.get('public_reason')),None)
        if account_reason:explanation=account_reason+explanation
        elif any(s['status']=='unknown_no_retry' for s in blocked):explanation+='发送状态待核对，不能自动重发。' 
        reason='；'.join(filter(None,[explanation,reason]))
    return dict(version=VERSION,code=code,label=label,reason=reason,event_ids=ids,blocked_stages=blocked,
                recognized=len(qualified),pending=len(pending),supporting=len(support),
                excluded=code in ('no_event','background') or code=='out_of_scope' and extract.get('version')=='news-business-v1',processing_incomplete=bool(failed or unfinished))


def saved(c, job, event_ids=(), source_row=None):
    exists=lambda name:bool(c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",(name,)).fetchone())
    states={}
    if job and exists('stages'):
        states={r['stage']:dict(status=r['status'],result=json.loads(r['result']) if r['result'] else None,public_reason=r['public_reason'])
                for r in c.execute("SELECT stage,status,CASE WHEN stage IN ('extract','mentions') THEN result END AS result,json_extract(result,'$.public_reason') AS public_reason FROM stages WHERE job=?",(job,))}
    rejected=False;note=''
    if source_row and exists('review_receipts'):
        row=c.execute("SELECT status,body FROM review_receipts WHERE json_extract(body,'$.source_row')=? ORDER BY rowid DESC LIMIT 1",(source_row,)).fetchone()
        rejected=bool(row and row['status']=='rejected')
        note=json.loads(row['body']).get('review_note','') if row else ''
    result=outcome(states,event_ids,rejected)
    pending=pending_reanalysis(c,job,states) if job else set()
    if pending:
        result['processing_incomplete']=True
        result['reason']='；'.join(filter(None,[result['reason'],'补充分析尚未完成，原有有效判断继续保留']))
        if result['event_ids']:
            result.update(code='partial',label=f'已关联 {len(result["event_ids"])} 个事件；补充分析尚未完成')
    if not note and job and exists('stages'):
        row=c.execute("SELECT provenance FROM stages WHERE job=? AND stage='extract'",(job,)).fetchone()
        if row and row[0]:note=json.loads(row[0]).get('original_request_identities',{}).get('raw_answer',{}).get('review_note','')
    result['review_note']=note
    if isinstance(note,dict):
        result['review_scope']=note.get('scope')
        result['partial_analysis']=note.get('scope')=='selected_matters'
        if result['partial_analysis']:
            result['code']='partial' if result['event_ids'] else 'pending_archive'
            result['label']=('已关联 '+str(len(result['event_ids']))+' 个事件；' if result['event_ids'] else '')+'已审阅选定事项，尚有内容未审阅'
        note=note.get('unreviewed','')
    if isinstance(note,str) and note:result['reason']='；'.join(filter(None,[result['reason'],note]))
    if event_ids and exists('replay_events'):
        rows=c.execute('SELECT body FROM replay_events WHERE id IN ('+','.join('?' for _ in event_ids)+')',tuple(event_ids))
        events=[json.loads(r['body']) for r in rows]
        gaps=sorted({gap for e in events for gap in e.get('publication_update_pending',[])})
        if gaps:
            result.update(code='partial',processing_incomplete=True,publication_update_pending=gaps,
                          label='原档案已保留；补充分析尚未完成')
            result['reason']='；'.join(filter(None,[result['reason'],'补充分析尚缺：'+'、'.join(gaps)]))
        if any(e.get('identity_update_pending') and (not source_row or source_row in e.get('evidence_sources',{}).get('source_rows',[])) for e in events):
            result.update(code='partial',processing_incomplete=True,identity_update_pending=True,
                          label='原档案已保留；新分析与旧事项的关联待核')
    return result
