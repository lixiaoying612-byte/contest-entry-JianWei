"""Saved checked prediction projection; offline scoring stays in frozen tools."""








def checked_mentions(states):
    """Keep mention identity, including checked blocks preceding a later failure."""
    stage=states.get('mentions',{})
    result=stage.get('result') or {}
    if stage.get('status') in ('ready','cached','partial'):
        # all_mentions is an earlier projection in old records. Prefer each
        # current checked pool, otherwise later saved emotion/scope is lost.
        merged={}
        for name in ('all_mentions','pending_mentions','not_applicable_mentions','mentions'):
            for index,m in enumerate(result.get(name,[])):
                merged[m.get('id',m.get('original_index',index))]=m
        return list(merged.values())
    return [dict(original_index=part_index*100000+index,monitoring_scope=scope)
            for part_index,part in enumerate(result.get('completed_parts',[]))
            for index,scope in enumerate(part.get('items',[]))]


def prediction_from_states(document_id, states):
    """Article × mention × object × definition; only checked matches and emotion."""
    from event_workbench.product_research import object_emotion, business_matches
    from event_workbench.news_outcomes import outcome
    stages = {s: states.get(s,dict(status='waiting')) for s in ('extract','mentions')}
    good = {'ready','cached','partial'}
    labels=[];excluded=[]
    for ordinal,m in enumerate(checked_mentions(states)):
        index=m.get('original_index',ordinal);body=m.get('body',{})
        if body.get('status',{}).get('context')=='background' or body.get('status',{}).get('mode')=='question':
            excluded.append(dict(mention_id=m.get('id'),original_index=index,reason='background_or_question'));continue
        seen=set()
        scope=m.get('monitoring_scope',{})
        if m.get('classification_review_id') and not any(x.get('decision') in ('matched','m') for x in scope.get('matches',[])):
            excluded.append(dict(mention_id=m.get('id'),original_index=index,reason='category_not_confirmed',
                classification_review_id=m['classification_review_id'],judgment=scope.get('unmatched'),business_objects=scope.get('business_objects',[])))
        for match in business_matches(m):
            if match.get('decision') not in ('matched','m'):continue
            d=match.get('definition') or match
            target=('stock' if d['target_type']=='company' else 'industry')+':'+str(d['target_code'])
            key=(target,d['definition_id'])
            if key in seen:continue
            seen.add(key)
            emotion=object_emotion(m.get('emotions',[]),d['definition_id'],d.get('object_id'))
            labels.append(dict(target_id=target,definition_id=d['definition_id'],emotion=emotion['label'],
                mention_id=m.get('id'),original_index=index,emotion_status=emotion['status']))
    if all(s['status'] in good for s in stages.values()):
        status = 'partial_success' if any(s['status']=='partial' for s in stages.values()) else 'success'
    elif any(s['status'] in ('failed','unknown_no_retry') for s in stages.values()):
        status = 'partial_success' if labels else 'failed'
    elif any(s['status'] in good for s in stages.values()):
        status = 'partial_success'
    else:
        status = 'unprocessed'
    return dict(document_id=document_id, labels=labels, status=status,
                prediction_stage='NewsProcessor mentions: accepted monitoring_scope.matches only',
                emotion_status='saved_object_emotion_only',excluded_mentions=excluded,disposition=outcome(states),
                stages={k:{f:s.get(f) for f in ('status','reason')} for k,s in stages.items()})




