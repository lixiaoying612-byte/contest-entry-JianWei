"""V3 impact and reminder rules. Semantic judgments enter only after scoped review."""
import copy
import json
import re
from datetime import date
from decimal import Decimal,InvalidOperation
from functools import lru_cache
from event_workbench import training_corpus as corpus

VERSION='alert-rules-20261004-v3'
CONTRACT='impact-features-v3'
DIMENSIONS={'K':'事件性质','M':'对象影响范围','S':'业务阶段','T':'时间敏感性'}
PRIORITIES=['grey','yellow','orange','red']
LABELS={'grey':'灰色·记录','yellow':'黄色·关注','orange':'橙色·重要','red':'红色·紧急'}
LEVELS={'grey':'记录','yellow':'低','orange':'中','red':'高'}
TRIGGERS={'R1':'首次达到提醒条件','R2':'关键业务阶段变化','R3':'关键事实得到确认','R4':'反转、恢复或范围改变','R5':'影响出现实质增量','R6':'更正先前判断','R7':'重要节点到期核查'}
HARD={
 'H1':dict(families={'F02'},conditions={'major_transaction_formal','major_transaction_closed','major_transaction_terminated','control_transfer_formal'},floor='orange'),
 'H2':dict(families={'F05','F06','F07','F09','F10'},conditions={'first_commercial_license','core_market_access','critical_operating_license'},floor='orange'),
 'H3':dict(families={'F01','F04','F07','F09','F10','F14','F15','F18'},conditions={'major_payment_failure','imminent_group_cash_crisis','operating_ban','severe_operating_obstacle'},floor='red')}

@lru_cache(maxsize=1)
def catalog():
    data=json.loads((corpus.ROOT/'data/alert-rules-v3.json').read_text('utf-8'))
    if data['version']!=VERSION:raise ValueError('alert_catalog_version_mismatch')
    return data

def priority(score,urgency):
    return 'red' if score>=80 and urgency==15 else 'orange' if score>=60 else 'yellow' if score>=40 else 'grey'

def maximum(*values):return max(values,key=PRIORITIES.index)

def refs(value,count):
    r=value.get('refs')
    if not isinstance(r,list) or not r or any(type(i)!=int or not 0<=i<count for i in r):raise ValueError('impact_evidence_refs_required')
    if not isinstance(value.get('reason'),str) or not value['reason'].strip():raise ValueError('impact_reason_required')

def pathway_text(value):
    """Display a saved explanation without discarding its structured evidence."""
    text=value.get('reason') if isinstance(value,dict) else value
    return text if isinstance(text,str) else ''


def scope_measurement(value):
    """Same-unit before-event denominator; never convert a qualitative bucket to a ratio."""
    for key in ('unit','period','equity_basis','scope','reason'):
        if not value.get(key):raise ValueError('measurement_context_missing:'+key)
    try:n,d=Decimal(str(value['numerator'])),Decimal(str(value['denominator']))
    except (InvalidOperation,KeyError):raise ValueError('measurement_number_invalid')
    if not n.is_finite() or not d.is_finite() or n<0 or d<=0:raise ValueError('measurement_number_invalid')
    if value.get('denominator_kind')=='after_including_increment':d-=n
    elif value.get('denominator_kind')!='before_event':raise ValueError('measurement_denominator_kind_required')
    if d<=0:raise ValueError('measurement_denominator_not_positive')
    ratio=n/d*100;points=5 if ratio<1 else 10 if ratio<5 else 20 if ratio<30 else 30
    return dict(numerator=str(n),denominator=str(d),percent=float(ratio),points=points,unit=value['unit'],method=value['denominator_kind'])

def retain_uncertain_scores(features):
    """A stated lack of evidence becomes a score range, never an extra model call."""
    for key,dimension in features.get('dimensions',{}).items():
        reason=dimension.get('reason','')
        if dimension.get('lower')!=dimension.get('upper') or key not in DIMENSIONS:continue
        if not re.search(r'未给出|未披露|未提供|未说明|缺少|未知|not disclosed|unknown',reason,re.I):continue
        if not re.search(r'默认|假定|采用.*档|按.*档|按正常|视为正常|assume.*normal|treat.*normal',reason,re.I):continue
        if key=='M' and features.get('measurement'):continue
        anchors=list(catalog()['anchors'][key])
        if key=='K' and features.get('family')=='F03':anchors=[v for v in anchors if v!=30]
        if key=='M' and features.get('object_relation',{}).get('kind')=='none':continue
        dimension.update(lower=min(anchors),upper=max(anchors),gap='评分依据不足，保留范围：'+reason)
        features['priority_rules']=[]


def validate(features,proof_count,calendar=None):
    if features.get('schema_version')!=CONTRACT or features.get('rule_version')!=VERSION:raise ValueError('impact_contract_mismatch')
    if any(k in features for k in ('score','total','level','priority','notification_state','alert','version','id')):raise ValueError('model_must_not_supply_impact_score_or_state')
    family=features.get('family')
    coverage=features.get('rule_coverage',{})
    if not isinstance(coverage,dict):raise ValueError('impact_rule_coverage_invalid')
    if coverage and (coverage.get('status')!='uncovered' or family is not None):raise ValueError('impact_rule_coverage_conflict')
    uncovered=family is None and coverage.get('status')=='uncovered'
    if not uncovered and family not in catalog()['families']:raise ValueError('unknown_impact_family')
    if features.get('direction') not in ('positive','negative','neutral','mixed','uncertain'):raise ValueError('impact_direction_invalid')
    relation=features.get('object_relation',{})
    if relation.get('kind') not in ('direct','transmitted','none','uncertain'):raise ValueError('impact_object_relation_invalid')
    refs(relation,proof_count)
    if not features.get('pathway') or not features.get('fact_state') or not features.get('next_observation'):raise ValueError('impact_explanation_or_facts_missing')
    if not pathway_text(features['pathway']).strip():raise ValueError('impact_pathway_reason_required')
    if isinstance(features['pathway'],dict):refs(features['pathway'],proof_count)
    fact=features['fact_state']
    if set(fact)!={'phase','status','values'} or not isinstance(fact['values'],dict) or fact['status'] not in ('planned','in_progress','occurred','terminated','restored','refuted','uncertain','unchanged') or not isinstance(fact['phase'],str) or not fact['phase'].isascii():raise ValueError('canonical_fact_state_required')
    if not isinstance(features.get('missing'),list):raise ValueError('impact_missing_list_required')
    dims=features.get('dimensions',{})
    if uncovered:
        refs(features['rule_coverage'],proof_count)
        if dims or features.get('measurement') or features.get('priority_rules'):raise ValueError('uncovered_rule_cannot_supply_scores')
        observation=features['next_observation']
        if not isinstance(observation,dict) or not observation.get('text'):raise ValueError('next_observation_required')
        if observation.get('date'):date.fromisoformat(observation['date']);refs(observation,proof_count)
        return
    if set(dims)!=set(DIMENSIONS):raise ValueError('four_impact_dimensions_required')
    retain_uncertain_scores(features)
    for key,value in dims.items():
        refs(value,proof_count)
        anchors=catalog()['anchors'][key]
        bounds=(value.get('lower'),value.get('upper'))
        if any(type(v)!=int or v not in anchors for v in bounds) or bounds[0]>bounds[1]:raise ValueError('impact_anchor_invalid:'+key)
        if bounds[0]!=bounds[1] and not value.get('gap'):raise ValueError('impact_range_requires_gap:'+key)
    if family=='F03' and dims['K']['upper']==30:raise ValueError('critical_funding_or_control_requires_F01_or_F02')
    if relation['kind']=='none' and dims['M']['upper']!=0:raise ValueError('unrelated_object_requires_M0')
    measurement=features.get('measurement')
    if measurement:
        refs(measurement,proof_count);calculated=scope_measurement(measurement)
        if dims['M']['lower']!=calculated['points'] or dims['M']['upper']!=calculated['points']:raise ValueError('M_does_not_match_comparable_ratio')
    urgency=features.get('urgency',{})
    if dims['T']['lower']==15:
        refs(urgency,proof_count)
        if urgency.get('kind')=='deadline':
            if not calendar or not calendar.get('reference') or not calendar.get('verified'):raise ValueError('verified_trading_calendar_required')
            first,last=date.fromisoformat(urgency['as_of']),date.fromisoformat(urgency['deadline'])
            days=[date.fromisoformat(x) for x in calendar['days']]
            if days!=sorted(set(days)) or not calendar.get('covers_from') or not calendar.get('covers_to'):raise ValueError('trading_calendar_coverage_required')
            if not date.fromisoformat(calendar['covers_from'])<=first<=last<=date.fromisoformat(calendar['covers_to']):raise ValueError('deadline_outside_calendar')
            if sum(first<d<=last for d in days)>5 or last<first:raise ValueError('deadline_not_within_five_trading_days')
        elif urgency.get('kind')!='ongoing_obstacle':raise ValueError('T15_requires_real_deadline_or_ongoing_obstacle')
    for item in features.get('priority_rules',[]):
        rule=HARD.get(item.get('rule'));refs(item,proof_count)
        if not rule or family not in rule['families'] or item.get('condition') not in rule['conditions']:raise ValueError('priority_rule_scope_invalid')
        if item['rule']=='H3' and (dims['K']['lower']!=30 or dims['T']['lower']!=15 or relation['kind']!='direct'):raise ValueError('H3_requires_direct_current_severe_obstacle')
        if item['rule']=='H2' and dims['S']['lower']<20:raise ValueError('H2_does_not_cover_ordinary_clinical_permission')
        if item['rule']=='H2' and family=='F07' and dims['S']['lower']!=25:raise ValueError('drug_commercial_license_requires_S25')
        if item['rule']=='H1' and dims['S']['lower']<15:raise ValueError('H1_does_not_cover_oral_intention')
        if item['rule']=='H1' and dims['K']['lower']!=30:raise ValueError('H1_requires_major_transaction_or_control_change')
    observation=features['next_observation']
    if not isinstance(observation,dict) or not observation.get('text'):raise ValueError('next_observation_required')
    if observation.get('date'):date.fromisoformat(observation['date']);refs(observation,proof_count)

def evidence_state(claims,cutoff=None):
    """Derive E/X from the bound claim's admitted V3 records, never its total score."""
    results=[]
    for claim in claims:
        from event_workbench.news_materials import moment
        from event_workbench.review_scoring import publication_moment
        eligible=[r for r in claim.get('score_input',{}).get('records',[]) if r.get('applicable') and r.get('time_eligible') and r.get('proof') and (cutoff is None or r.get('material_available_at') and publication_moment(r['material_available_at'])<=moment(cutoff))]
        supports=[r for r in eligible if r.get('relation') in ('supports','reported')]
        level=2 if any(r.get('evidence') in ('primary','direct') and r.get('evidence_checked') and r.get('identity_checked') for r in supports) else 1 if any(r.get('evidence')=='disclosure_report' and r.get('attribution_checked') or r.get('evidence')=='report' and r.get('source')=='professional' for r in supports) else 0
        conflict=any(r.get('relation')=='refutes' for r in eligible) or claim.get('verdict') in ('存在冲突','存在反驳依据')
        results.append(dict(claim_id=claim['claim_id'],claim=claim['claim'],level=level,conflict=conflict))
    level=min((r['level'] for r in results),default=0)
    return dict(level=level,label=['E0 · 待核线索','E1 · 具体报道依据','E2 · 适用原始依据'][level],conflict=any(r['conflict'] for r in results),claims=results)

def calculate(features,evidence):
    if features.get('rule_coverage',{}).get('status')=='uncovered':
        return dict(rule_version=VERSION,family=None,family_name='现有细则未覆盖',score=None,lower=None,upper=None,score_text='未评分',components={},
            base_priority='grey',potential_priority='grey',possible_priority='grey',priority='grey',label='规则未覆盖',level='待定',visible=False,route='observation',
            evidence=copy.deepcopy(evidence),priority_rules=[],measurement=None,direction=features['direction'],pathway=features['pathway'],
            missing=list(dict.fromkeys(features['missing']+['现有预警细则未覆盖：'+features['rule_coverage']['reason']])),next_observation=copy.deepcopy(features['next_observation']),
            rule_coverage=copy.deepcopy(features['rule_coverage']),fact_state=copy.deepcopy(features['fact_state']),fact_signature=corpus.digest(features['fact_state']))
    dims=copy.deepcopy(features['dimensions']);lower=sum(v['lower'] for v in dims.values());upper=sum(v['upper'] for v in dims.values())
    base=priority(lower,dims['T']['lower']);possible=priority(upper,dims['T']['upper'])
    floors=[HARD[r['rule']]['floor'] for r in features.get('priority_rules',[])]
    potential=maximum(base,*floors)
    related=features['object_relation']['kind'] in ('direct','transmitted') and dims['M']['upper']>0
    confirmed_scope=related and evidence['level']>0 and not evidence['conflict']
    actual=potential if confirmed_scope else 'grey'
    check=features['object_relation']['kind']!='none' and dims['M']['upper']>0 and not confirmed_scope and potential in ('orange','red')
    missing=features['missing']+[v['gap'] for v in dims.values() if v.get('gap')]
    if evidence['level']==0:missing.append('提醒核心前提缺少适用材料')
    if evidence['conflict']:missing.append('提醒核心前提存在实质冲突')
    return dict(rule_version=VERSION,family=features['family'],family_name=catalog()['families'][features['family']]['name'],
        score=lower if lower==upper else None,lower=lower,upper=upper,score_text=str(lower) if lower==upper else f'{lower}—{upper}',components=dims,
        base_priority=base,potential_priority=potential,possible_priority=maximum(possible,*floors),priority=actual,
        label=LABELS[actual] if confirmed_scope or features['object_relation']['kind']=='none' else '待核线索 · '+LABELS[potential],level=LEVELS[actual] if not check else '待定',
        visible=actual!='grey' or check,route='important_pending' if check else 'event_notice' if actual!='grey' else 'observation',
        evidence=copy.deepcopy(evidence),priority_rules=copy.deepcopy(features.get('priority_rules',[])),measurement=scope_measurement(features['measurement']) if features.get('measurement') else None,
        direction=features['direction'],pathway=features['pathway'],missing=list(dict.fromkeys(missing)),next_observation=copy.deepcopy(features['next_observation']),
        fact_state=copy.deepcopy(features['fact_state']),fact_signature=corpus.digest(features['fact_state']))

def transition(current,previous,notified,change,*,cause='new_material',followed=False,muted=False):
    """Select one version/action with multiple reasons; caller stores atomically."""
    kind=change.get('kind');valid={'initial','stage','confirmation','reversal','scope_change','correction','incremental','duplicate','observation_only'}
    if kind not in valid or not change.get('reason'):raise ValueError('reviewed_fact_change_required')
    changed=not previous or previous.get('fact_signature')!=current['fact_signature']
    triggers=[];delivery=current['priority'];action='仅记录'
    if cause in ('rule_recalculation','historical_backfill'):
        return dict(triggers=[],action='规则重算' if cause=='rule_recalculation' else '历史补录',notify=False,delivery_priority=delivery,reason='本次没有新的市场事实，不重复发送通知')
    if cause not in ('new_material','review_update'):raise ValueError('unknown_alert_update_cause')
    if current.get('rule_coverage',{}).get('status')=='uncovered' and kind!='correction':
        return dict(triggers=[],action='仅记录',notify=False,delivery_priority=delivery,reason='现有细则未覆盖，保存影响判断及待核信息，不生成数值等级')
    if kind in ('duplicate','observation_only'):changed=False
    if kind=='correction':
        if not previous or not change.get('old_statement') or not change.get('new_statement'):raise ValueError('correction_requires_old_and_new_claim')
        if change['old_statement']==change['new_statement']:raise ValueError('correction_has_no_changed_statement')
        if not changed:return dict(triggers=[],action='仅记录',notify=False,delivery_priority=delivery,reason='相同更正已经保存，不重复通知')
        triggers.append('R6');action='更正'
        if notified:delivery=maximum(delivery,notified.get('delivery_priority') or notified.get('priority','grey'))
    elif changed:
        if current['visible'] and not notified:triggers.append('R1')
        if previous and kind=='stage' and change.get('substantive') is True:triggers.append('R2')
        if previous and kind in ('reversal','scope_change'):triggers.append('R4')
        if notified and (current['priority']!=notified['priority'] or current.get('lower') is not None and notified.get('lower') is not None and abs(current['lower']-notified['lower'])>=15):triggers.append('R5')
    if kind=='confirmation' and previous and current['evidence']['level']>previous['evidence']['level'] and change.get('resolved_uncertainty'):
        if change['resolved_uncertainty'] not in previous.get('missing',[]):raise ValueError('confirmation_must_resolve_a_saved_gap')
        triggers.append('R3')
        if current['visible'] and not notified:triggers.append('R1')
    triggers=list(dict.fromkeys(triggers))
    if action!='更正':action='更新' if previous and triggers else '紧急提醒' if current['priority']=='red' and triggers else '重要提醒' if current['priority']=='orange' and triggers else '关注汇总' if current['priority']=='yellow' and triggers else '核查' if current['route']=='important_pending' else '仅记录'
    returning=bool(notified and any(r in triggers for r in ('R2','R3','R4','R5','R6')))
    deliver=bool(triggers and (current['priority'] in ('orange','red') or current['priority']=='yellow' and followed or returning))
    if 'R6' in triggers:deliver=bool(notified or followed)
    if current['evidence']['level']==0 and 'R6' not in triggers:deliver=False
    if current['evidence']['conflict'] and not returning and 'R6' not in triggers:deliver=False
    if muted:deliver=False
    why='已静音，仅更新档案' if muted else '规则重算或没有实质变化，不重复通知' if not triggers else '黄色关注未订阅，仅在中心保存' if current['priority']=='yellow' and not followed and not returning else '核心前提待核，保留核查事项' if not deliver and current['route']=='important_pending' else '应用内通知' if deliver else '只保存判断'
    return dict(triggers=triggers,action=action,notify=deliver,delivery_priority=delivery,reason=why)
