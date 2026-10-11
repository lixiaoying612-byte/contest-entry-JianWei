"""Saved impact proposal projections used by current publication and history views."""
from event_workbench.unified_pipeline import digest

RULE='eng051-scoped-v1'


def proposals(result,index,facts,member):
    checked=(result or {}).get('record',{}).get('checked',{})
    rows=[r for r in checked.get('assessments',[]) if r.get('original_index')==index]
    effects=[e for r in rows for e in r.get('valid_effects',[]) if e.get('proofs') and not e.get('issues')]
    invalid=[e for r in rows for e in r.get('invalid_effects',[])]
    out=[];slots={}
    for effect in effects or [None]:
        raw=effect['original'] if effect else {};target=raw.get('target',{}).get('name')
        slot=slots.get(target,0);slots[target]=slot+1
        scope=dict(claim=raw.get('pathway') or facts.get('summary'),mention=facts.get('summary'),
                   phase=facts.get('status',{}).get('phase'),mode=facts.get('status',{}).get('mode'),
                   mention_mode=facts.get('status',{}).get('mode'),effect_modality='conditional' if raw.get('origin')=='conditional_inference' else 'source_statement' if effect else 'unknown',times=facts.get('times',[]),horizon=raw.get('horizon'),conditions=raw.get('conditions',[]))
        grade=raw.get('grade','unknown');dimensions=raw.get('dimensions',{})
        level={'high':'高','medium':'中','low':'低','unknown':'待定'}.get(grade,'待定')
        proposed=level
        if not dimensions or any(dimensions.get(k,'unknown')=='unknown' for k in ('scope','materiality','urgency')):level='待定'
        out.append(dict(id='decision-'+digest([member,target,slot]),member_id=member,target=target,scope=scope,
            effects=[effect] if effect else [],invalid_effects=invalid,targets=[target] if target else [],
            level=level,proposed_level=proposed,route='important_pending' if grade in ('high','medium') else 'observation',
            confirmed=False,credibility='未独立复核',factors=dimensions,target_status='单对象效果提案；范围与条件待独立复核' if target else '影响对象未确定；不以参与方代替',
            reasons=['缺少绑定本说法、对象、口径及版本的独立复核'] if effect else ['没有通过引用检查的对象效果，继续观察'],
            missing=['独立范围复核','核验说法与本效果的明确对应','对象关联与影响分级依据','冲突及来源依赖审查'],rule_version=RULE,external_notification=False))
    return out


def summary(items):
    if len(items)==1:return dict(items[0],decisions=items)
    return dict(level='分对象判断',proposed_level='分对象判断',route='observation',targets=list(dict.fromkeys(t for d in items for t in d['targets'])),
        target_status='各效果按对象与口径分别判断',reasons=['不同对象或口径不得共享最高级别'],factors={},confirmed=False,
        effects=[e for d in items for e in d['effects']],invalid_effects=[e for d in items for e in d['invalid_effects']],decisions=items,rule_version=RULE)

