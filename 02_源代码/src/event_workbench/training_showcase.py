"""List projection for existing training and news records; no import side effects."""


def summary(event):
    """Keep only list/filter/action fields; prose is fetched with the dossier."""
    keep=('member_id','job_id','record_kind','event_version','material_version','available_at','updated_at','update_time_basis','change_summary','event_summary','monitoring_scope','emotions','event_timeline','warning_reasons','case_id','material_count','progress_count','review_status','assessment_version','belongs_to_events','event_identity','showcase_rank')
    result={k:event[k] for k in keep if k in event}
    result['warning_reasons']=dict(decisions=[dict({k:d[k] for k in ('id','target','level','active','alert','route','version','rule_version','priority','fact_summary','change') if k in d},effects=[dict(original={k:x.get('original',{}).get(k) for k in ('target','direction')}) for x in d.get('effects',[])]) for d in event.get('warning_reasons',{}).get('decisions',[])])
    result['event_timeline']=dict(items=[{k:t[k] for k in ('time','kind') if k in t} for t in event.get('event_timeline',{}).get('items',[])])
    result['credibility']={k:v for k,v in event.get('credibility',{}).items() if k in ('status','assessment_cutoff','score','score_label','score_scope','score_rule','score_max','score_status','short_reason','assessed_at')}
    claims=event.get('credibility',{}).get('claims',[])
    result['credibility']['needs_followup']=event.get('credibility',{}).get('needs_followup',not claims or any(c.get('verdict')!='有材料支持' for c in claims))
    source=event.get('evidence_sources',{})
    result['evidence_sources']=dict(original={k:v for k,v in source.get('original',{}).items() if k!='text'},source_rows=source.get('source_rows',[]))
    result['industries']=sorted(set(event.get('industries',[]))|{a['industry_name'] for a in event.get('annotations',[]) if a.get('industry_name')})
    result['analysis_available']=any(a.get('event_impact_analysis') for a in event.get('annotations',[]))
    return result
