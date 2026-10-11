"""Shared admission and projection for the confirmed claim-level V3 index."""
import copy
import json
import re
from datetime import datetime, timezone, timedelta
from event_workbench.case_replay import proof
from event_workbench import credibility_rules as rules, training_corpus as corpus
from event_workbench.news_materials import moment

VERSION = rules.VERSION
CONTRACT = 'credibility-features-v3'
CONTENT = ('subject', 'action', 'item', 'time', 'modality')
MEDIA = {'北京商报','北京商报网','中国经济网','中国证券报','上海证券报·中国证券网','上海证券报','财联社','第一财经','每日经济新闻','每经网','证券时报','证券日报','新华社','新华网','人民网','澎湃新闻','界面新闻','中国新闻网','中新网','北极星输配电网','北极星电力网','动脉网','医药观澜','格隆汇','上观新闻','贵州日报','贵阳日报','中国工业报'}
PLATFORMS = {'新浪财经','新浪网','网易财经','网易科技','腾讯网','同花顺','同花顺app','东方财富网','搜狐网','搜狐财经','今日头条','凤凰网','凤凰财经','东方财富号','证券之星'}
DISCLOSURE = {'公司公告','上海证券交易所','深圳证券交易所','深交所','国家药品监督管理局药品审评中心'}
FIELDS = ('title','text','source','publication_time')


def check_currency(text, quote):
    if '$' not in quote:return
    symbols=re.findall(r'(?<![A-Za-z])((?:US|HK|NZ|A|C))?\s*\$',quote,re.I)
    # ponytail: mixed bare/prefixed dollar amounts retain source symbols; no financial amount parser.
    mixed='' in symbols and any(symbols)
    for name,code,symbol in (('美元','USD','US'),('澳元','AUD','A'),('澳币','AUD','A'),('港元','HKD','HK'),('加元','CAD','C'),('新西兰元','NZD','NZ')):
        if (name in text or re.search(r'(?<![A-Za-z])'+code+r'(?![A-Za-z])',text,re.I)) and (mixed or not re.search(name+r'|\b'+code+r'\b|(?<![A-Za-z])'+symbol+r'\s*\$',quote,re.I)):
            raise ValueError('recognition_currency_not_established:'+name)



def check_source_text(text, quote):
    check_currency(text,quote)
    # ponytail: explicit action negatives only; not general semantic truth.
    actions={'开工':r'(?:start\w*|beg[iau]n|commenc\w*)',
             '投产':r'(?:enter\w*|start\w*|commenc\w*)',
             '签署':r'sign\w*','批准':r'approv\w*','完成':r'complet\w*',
             '交付':r'deliver\w*','生效':r'(?:becom\w*\s+effective|take\w*\s+effect)',
             '启动':r'(?:launch|start|commenc)\w*','支付':r'(?:pay|paid)',
             '业务障碍':r'(?:business|operating)\s+(?:obstacle|disruption)\w*'}
    def conditional(value,start):
        clause=re.split(r'[。！？；\n"：:，,;.!?]',value[:start])[-1]
        return bool(re.search(r'若|如果|假如|倘若|一旦|假设|(?i:\bif\b|\bunless\b)',clause))
    for match in re.finditer(r'(?:尚未|还未|并未|未曾|没有|不存在|尚无|无|未)(?:实际|正式|到)?('+ '|'.join(actions)+')',text):
        if conditional(text,match.start()):continue
        phrase,action=match.group(0),match.group(1)
        if any(not conditional(quote,m.start()) for m in re.finditer(re.escape(phrase),quote)):continue
        verb=actions[action]
        english=r'(?i)\b(?:has|have|had|is|are|was|were|do|does|did)\s+not\s+(?:yet\s+)?(?:been\s+)?'+verb
        if action=='业务障碍':english+=r'|\bno\s+'+verb
        matches=[m for m in re.finditer(english,quote) if not conditional(quote,m.start())]
        # Start/commence must be about the named operation, not an unrelated action.
        topic=r'construction|development' if action=='开工' else r'production|operation' if action=='投产' else None
        if matches and (topic is None or any(re.search(topic,quote[max(0,m.start()-60):m.end()+60],re.I) for m in matches)):continue
        raise ValueError('joint_negative_action_not_established:'+phrase)
    # ponytail: bounded explicit window negatives; absence of a report is not absence of a window.
    window=r'(?:没有|不存在|尚无|无)(?:近期(?:待执行)?|短期|临近|[一二三四五六七八九十0-9]+个?(?:交易|工作|自然)?日内)?(?:具有)?(?:业务后果的?|紧急|紧迫)?(?:截止|实施|生效|执行|时间)?窗口'
    for match in re.finditer(window,text):
        if conditional(text,match.start()) or re.match(r'的?(?:披露|说明|报道|信息|证据)',text[match.end():]):continue
        if any(not conditional(quote,m.start()) for m in re.finditer(re.escape(match.group(0)),quote)):continue
        english=r'(?i)\bno\s+(?:(?:near[- ]term|short[- ]term|imminent|deadline|implementation|execution|time)\s+)*window\b'
        if any(not conditional(quote,m.start()) for m in re.finditer(english,quote)):continue
        raise ValueError('joint_negative_window_not_established:'+match.group(0))


def publication_moment(value):
    # Existing train documents store local wall time; cutoffs still require an offset.
    from event_workbench.news_intake import parse_publication_time
    parsed=parse_publication_time(value)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone(timedelta(hours=8)))


def source_entry(name):
    tier = 'identified' if name in MEDIA | DISCLOSURE else 'platform' if name in PLATFORMS else 'unknown'
    return dict(name=name or '来源未提供', tier=tier, points=rules.SOURCES[tier][0], reason='仅识别来源字段，未据媒体名称认定专业身份或已核原件')


def source_attribution(document):
    # Only explicit publisher credits, not media names appearing in article claims.
    text=document.get('text') or '';signals=[]
    for pattern in (r'(?m)^[ \t]*(?:转自|来源)[：:]\s*([^\s，。,；;：:（）()<>]{2,40})',r'由([^\s，。,；;：:（）()<>]{2,40})为您提供'):
        for match in re.finditer(pattern,text):
            signals.append(dict(name=match.group(1),quote=match.group(0),section='body',start=match.start(),end=match.end()))
    names=sorted({s['name'] for s in signals})
    return dict(status='multiple_unresolved' if len(names)>1 else 'single_unverified' if names else 'not_identified',candidates=names,signals=signals)


def proof_at(document, quote, unit=None):
    if unit is None:return proof(document,quote)
    if not isinstance(unit,dict) or unit.get('section') not in ('body','title'):raise ValueError('scoring_original_unit_position_mismatch')
    start,end=unit.get('start'),unit.get('end');text=document.get('text' if unit['section']=='body' else 'title') or ''
    if not isinstance(quote,str) or not quote or unit.get('quote')!=quote or type(start) is not int or type(end) is not int or not 0<=start<end<=len(text) or text[start:end]!=quote:
        raise ValueError('scoring_original_unit_position_mismatch')
    return dict(section=unit['section'],start=start,end=end,quote=quote)


def features(document, review, catalog, cutoff, *, basis=None):
    """The catalog is program-owned, scoped material, never supplied by a model."""
    if review.get('schema_version') != CONTRACT or review.get('rule_version') != VERSION:
        raise ValueError('credibility_contract_or_rule_mismatch')
    if any(k in review for k in ('score','score_label','raw_score','score_items','score_input','score_source_input','notification_state')):
        raise ValueError('model_must_not_supply_score_or_state')
    if review.get('role') not in ('current_core','additional','historical'):
        raise ValueError('explicit_claim_role_required')
    if not review.get('role_reason') or not review.get('claim_quote'):
        raise ValueError('claim_scope_and_role_basis_required')
    basis=proof_at(document,review['claim_quote'],basis)
    if not cutoff:raise ValueError('credibility_cutoff_required')
    if document.get('publication_time') and publication_moment(document['publication_time'])>moment(cutoff):raise ValueError('claim_after_cutoff')
    result=copy.deepcopy(review)
    result.update(body_available=bool((document.get('text') or '').strip()),basis=basis,article_source=source_entry(document.get('source'))['tier'])
    own=dict(document={k:document.get(k) for k in FIELDS},available_at=document.get('publication_time') or cutoff)
    for r in result.get('records',[]):
        mid=r.get('material_id')
        entry=catalog.get(mid) if mid!='own' else catalog.get('own',own)
        if not entry:raise ValueError('scoring_material_outside_catalog')
        doc=entry['document'];sha=corpus.digest(doc)
        if mid=='own' and doc!=own['document']:raise ValueError('own_catalog_document_changed')
        if r.get('document_sha')!=sha:raise ValueError('scoring_document_hash_mismatch')
        unit=entry.get('source_unit')
        if unit is not None and (not isinstance(unit,dict) or unit.get('document_id')!=sha):raise ValueError('scoring_original_unit_document_mismatch')
        located=proof_at(doc,r['quote'],unit);available=entry['available_at']
        if publication_moment(available)>moment(cutoff) or (doc.get('publication_time') and publication_moment(doc['publication_time'])>moment(cutoff)):raise ValueError('scoring_material_after_cutoff')
        checked=entry.get('source_review',{})
        attribution=source_attribution(doc)
        resolved=bool(checked.get('origin') and all(checked.get(k) for k in ('basis','reference','reviewed_at','reviewer')) and moment(checked['reviewed_at'])<=moment(cutoff))
        if attribution['status']=='multiple_unresolved' and not resolved and (r.get('path')!='unknown' or r.get('origin')):
            raise ValueError('source_attribution_unresolved_requires_unknown_path')
        r['source_attribution']=attribution

        if rules.SOURCES[r['source']][0]>=15 or r.get('evidence_checked') or r.get('path_checked'):
            if not all(checked.get(k) for k in ('basis','reference','reviewed_at','reviewer')):raise ValueError('material_review_provenance_required')
            if moment(checked['reviewed_at'])>moment(cutoff):raise ValueError('material_review_after_cutoff')
        if rules.SOURCES[r['source']][0]>=15:
            if checked.get('source')!=r['source'] or not all(checked.get(k) for k in ('basis','reference','reviewed_at','reviewer')):raise ValueError('source_identity_requires_catalog_review')
            if moment(checked['reviewed_at'])>moment(cutoff):raise ValueError('source_review_after_cutoff')
        if r['source']=='professional' and (r.get('publisher_name')!=checked.get('publisher_name',doc.get('source')) or r.get('source_basis')!=checked.get('basis') or r.get('source_reference')!=checked.get('reference')):
            raise ValueError('professional_publisher_not_bound_to_catalog')
        if r.get('evidence_checked') and not checked.get('evidence_checked'):raise ValueError('unreviewed_catalog_evidence')
        if r.get('path_checked') and not checked.get('path_checked'):raise ValueError('unreviewed_catalog_path')
        if checked.get('origin') and r.get('origin') and r['origin']!=checked['origin']:raise ValueError('source_origin_conflicts_with_catalog')
        if r.get('independent_of'):
            # Two URLs or two model-assigned origin names are not two sources.
            root=checked.get('origin');independence=checked.get('independence',{})
            if not root or r.get('origin')!=root:raise ValueError('independent_origin_not_bound_to_catalog')
            if not all(independence.get(k) for k in ('basis','reference','reviewed_at','reviewer')) or moment(independence['reviewed_at'])>moment(cutoff):raise ValueError('independence_catalog_review_required')
            if not set(r['independent_of'])<=set(independence.get('independent_of',[])) or root in r['independent_of']:raise ValueError('independence_outside_catalog')
            roots={e.get('source_review',{}).get('origin') for e in catalog.values()
                   if publication_moment(e['available_at'])<=moment(cutoff) and e.get('source_review',{}).get('reviewed_at') and moment(e['source_review']['reviewed_at'])<=moment(cutoff)}
            if not set(r['independent_of'])<=roots:raise ValueError('independent_counterpart_outside_catalog')
        r['proof']=dict(located,document_sha=sha,material_id=mid,available_at=available,url=entry.get('url'),source_row=entry.get('source_row'))
        r['material_title']=doc.get('title');r['material_source']=doc.get('source');r['material_available_at']=available
        r['source_review']=copy.deepcopy(checked)
        rules.check_record(r)
    represented={r.get('origin') for r in result.get('records',[]) if r.get('origin') and r['origin']==r.get('source_review',{}).get('origin')}
    if any(not set(r.get('independent_of',[]))<=represented for r in result.get('records',[])):raise ValueError('independent_counterpart_not_bound_in_claim')
    return result


def legacy_features(document, review, catalog):
    """Retain admitted old evidence conservatively; completeness earns no V3 points."""
    review=review or {};name=document.get('source');override=review.get('publisher')
    if override:
        proof(document,override['quote'])
        if override['name'] not in override['quote']:raise ValueError('publisher_not_in_quote')
        name=override['name']
    if not set(review.get('checks',{}))<=set(CONTENT):raise ValueError('unknown_content_check')
    for q in review.get('checks',{}).values():proof(document,q)
    if review.get('independent_points',0):raise ValueError('independence_not_established')
    f=dict(schema_version=CONTRACT,rule_version=VERSION,claim=review.get('claim') or document.get('title') or '原文基础初评',role='current_core',role_reason='沿用已保存主张范围；未由旧分数推断新证据',body_available=bool((document.get('text') or '').strip()),article_source=source_entry(name)['tier'],records=[],problems=[],conversion='legacy_review_no_new_semantic_inference')
    ev=review.get('evidence',{});points=ev.get('points',0)
    if points not in (0,5,10,15):raise ValueError('evidence_tier')
    if points>=10:
        entry=catalog[ev['id']]
        if entry['kind'] not in ('primary_disclosure','company_statement','direct_interview','traceable_record') or not ev.get('reason'):raise ValueError('evidence_not_checked')
        if points==15 and entry['kind']!='primary_disclosure':raise ValueError('original_record_required')
        formal=entry['kind']=='primary_disclosure';source='formal' if formal else 'first_party' if entry['kind']=='company_statement' else 'identified'
        r=dict(id='legacy:'+ev['id'],source=source,evidence='primary' if formal else 'direct' if entry['kind'] in ('company_statement','direct_interview') else 'report',path='named',origin=entry['url'],quote=entry['quote'],reason=ev['reason'],relation=ev.get('judgment','supports'),applicable=True,time_eligible=True,identity_checked=True,evidence_checked=True,path_checked=False,independent_of=[],proof=dict(quote=entry['quote'],url=entry['url']),material_title=entry['title'])
        f['records'].append(r)
    elif points==5:
        q=proof(document,ev['quote'])
        f['records'].append(dict(id='own:'+corpus.digest(q),source=f['article_source'],evidence='attributed',path='unknown',origin=None,quote=ev['quote'],reason=ev.get('reason') or '旧审阅保留的间接引述，尚未按新条件审阅具体披露',relation='reported',applicable=True,time_eligible=True,identity_checked=False,evidence_checked=False,path_checked=False,independent_of=[],proof=q))
    for issue in review.get('problems',[]):
        q=proof(document,issue['quote'])
        if issue['points']!=10:raise ValueError('legacy_counterevidence_requires_v3_review')
        rid='condition:'+corpus.digest(q)
        f['records'].append(dict(id=rid,source=f['article_source'],evidence='none',path='unknown',origin=None,quote=q['quote'],reason=issue['reason'],relation='related',applicable=True,time_eligible=True,identity_checked=False,evidence_checked=False,path_checked=False,independent_of=[]))
        f['problems'].append(dict(kind='qualification',root=issue['root'],basis_material=rid,quote=q['quote'],reason=issue['reason']))
    return f


def calculated(f):
    v=rules.score_claim(f);items=copy.deepcopy(v['score_items'])
    chosen=next((r for r in f.get('records',[]) if r['id']==v['selected_material']),None)
    if chosen:
        for item in items:
            if item['name']=='适用证据':
                item['quotes']=[chosen.get('proof',dict(quote=chosen['quote'],url=chosen.get('url')))]
                item['reason']+='；'+chosen['reason']
            if item['name']=='来源' and chosen.get('source_review'):
                item['reason']+='；'+chosen['source_review'].get('basis','')
                item['reference']=chosen['source_review'].get('reference')
    for item in items:
        if item['points']<0:
            ids={p['basis_material'] for p in f.get('problems',[]) if p['reason']==item['reason']}
            item['quotes']=[r.get('proof',dict(quote=r['quote'],url=r.get('url'))) for r in f.get('records',[])
                            if r['applicable'] and r['time_eligible'] and (r['id'] in ids or (r['relation']=='refutes' and r['reason']==item['reason']))]
    return dict(score=v['score'],score_label=v['label'],score_rule=VERSION,score_max=95,score_status=v['status'],short_reason=v['short_reason'],score_items=items,score_limits=[f"{c['reason']}，最高{c['maximum']}分" for c in v['score_caps']],score_caps=v['score_caps'],applied_caps=v['applied_caps'],raw_score=v['raw_score'],display=v['display'],score_scope=v['score_scope'],score_input=copy.deepcopy(f),score_selected_material=v['selected_material'],score_independent_roots=v['additional_independent_roots'],probability=False)


def score(document, review=None, evidence_catalog=None, *, cutoff=None, basis=None):
    if not (document.get('text') or '').strip():return dict(score=None,score_label='缺少正文',score_rule=VERSION,score_max=95,score_items=[],score_limits=[],short_reason='正文缺失，保留在输入处理环节')
    f=features(document,review,evidence_catalog or {},cutoff,basis=basis) if review and review.get('schema_version') else legacy_features(document,review,evidence_catalog or {})
    result=calculated(f)
    if not review:result.update(score_scope='来源基础初评；原文尚未逐主张审阅',short_reason='仅评价已保存来源信息；具体主张与外部依据尚待审阅')
    return result


def event_score(claims, current_mentions=None, *, claim_groups=()):
    """Merge evidence per current claim; never select a report's old numeric score."""
    groups={};reviewed={}
    active={c.get('claim_id') for c in claims if c.get('claim_role')!='historical' and not c.get('superseded_by') and (current_mentions is None or c.get('source_mention_id') in current_mentions)}
    for group in claim_groups:
        ids=group['claim_ids']
        if not set(ids)<=active or set(ids)&reviewed.keys():raise ValueError('claim_group_scope_changed')
        reviewed.update({cid:group['id'] for cid in ids})
    for c in claims:
        historical=(current_mentions is not None and c.get('source_mention_id') not in current_mentions) or c.get('claim_role')=='historical' or bool(c.get('superseded_by'))
        c['score_role']='已被更正' if c.get('superseded_by') else '历史阶段' if historical else '附属主张' if c.get('claim_role')=='additional' else '同一主张的其他报道'
        if historical:continue
        # Start from this report's inputs; an earlier grouping must not leak evidence.
        if c.get('score_source_input'):c['score_input']=copy.deepcopy(c['score_source_input'])
        cid=c.get('claim_id');key=('reviewed',reviewed[cid]) if cid in reviewed else ('literal',' '.join(c['claim'].split()))
        groups.setdefault(key,[]).append(c)
    chosen=[]
    for key,group in groups.items():
        primary=next((c for c in group if c.get('claim_role')!='additional'),group[0]);role='additional' if all(c.get('claim_role')=='additional' for c in group) else 'current_core'
        primary['score_role']='附属主张' if role=='additional' else '当前核心主张'
        ready=[c for c in group if c.get('score_rule')==VERSION and c.get('score_input')]
        if len(ready)!=len(group):primary.update(score=None,score_label='主张评分依据待补齐',score_rule=VERSION,score_max=95,score_status='评分特征待补齐',score_items=[],score_limits=['当前核心主张缺少可重算特征'],short_reason='缺少可重算特征，不能直接沿用旧数值')
        else:
            if len(group)>1:primary.setdefault('score_source_input',copy.deepcopy(primary['score_input']))
            f=copy.deepcopy(primary['score_input']);records={};issues=[]
            for c in ready:
                own=c['score_input'];renames={}
                for r in own.get('records',[]):
                    entry=copy.deepcopy(r);entry.pop('id');rid=corpus.digest(entry);renames[r['id']]=rid;entry['id']=rid;records[rid]=entry
                for p in own.get('problems',[]):issues.append(dict(p,basis_material=renames[p['basis_material']]))
            f.update(claim=' '.join(primary['claim'].split()),role=role,records=list(records.values()),problems=issues,article_source=max((c['score_input']['article_source'] for c in ready),key=lambda x:rules.SOURCES[x][0]))
            primary.update(calculated(f))
            primary['verdict']=reviewed_verdict(f,primary)
        if role=='current_core':chosen.append(primary)
    if not chosen:return {}
    lowest=min(chosen,key=lambda c:(-1 if c.get('score') is None else c['score'],c.get('claim_id','')))
    keys=('short_reason','raw_score','display','applied_caps','probability')
    result={k:copy.deepcopy(v) for k,v in lowest.items() if (k.startswith('score') and k not in ('score_role','score_input','score_source_input')) or k in keys}
    from event_workbench.news_mvp import verdict
    conclusions={c.get('verdict') or verdict(c) for c in chosen}
    result.update(status=next(iter(conclusions)) if len(conclusions)==1 else '分主张判断',needs_followup=conclusions!={'有材料支持'},history_pending_count=sum(c['score_role']=='历史阶段' and (c.get('verdict') or verdict(c))!='有材料支持' for c in claims),score_scope='当前核心主张取最低分；同一主张先合并证据，转载不增加独立印证；历史与附属主张分别列示',score_aggregation=VERSION,score_claim_ids=[c.get('claim_id') for c in chosen],score_determining_claim=lowest.get('claim_id'))
    if lowest.get('score') is None:result['needs_followup']=True
    result['summary']='；'.join(c['claim']+'：'+(c.get('verdict') or verdict(c)) for c in chosen)
    return result


def reviewed_verdict(review, result=None):
    if review.get('schema_version')==CONTRACT:
        state=result['score_status']
        return state if state in ('存在冲突','存在反驳依据') else '有材料支持' if state in ('已核原始记录','有直接材料支持','有适用材料支持') else '证据不足'
    if any(p['points']==20 for p in review.get('problems',[])):return '存在冲突'
    ev=review.get('evidence',{})
    return ('存在反驳依据' if ev.get('judgment')=='refutes' else '有材料支持') if ev.get('points',0)>=10 else '证据不足'


def case_claim(document, claim, source_check, *, available_at=None):
    quote=claim['quote'];proof(document,quote)
    saved=corpus.ROOT/'data/showcase-credibility-v3.json'
    if saved.exists():
        record=json.loads(saved.read_text('utf-8'))
        own=next((i for i in record['items'] if i['document_sha']==corpus.digest(document) and i['claim']==claim['text']),None)
        if own:return score(document,own['features'],{},cutoff=corpus.now())
    review=dict(claim=claim['text'],checks=dict(action=quote),evidence=dict(points=0));catalog={}
    if source_check.get('source_kind')=='public_primary' and source_check.get('status') in ('original_page_checked','original_pdf_checked'):
        catalog['original']=dict(title=document['source'],url=source_check['url'],quote=quote,kind='company_statement')
        review['evidence']=dict(points=10,id='original',reason='已核业务主体原页或投资者活动记录，只支持本条限定事实，不外推预测实现或资金到账')
    result=score(document,review,catalog)
    if catalog:
        f=result['score_input'];f['records'][0].update(path='traced',path_checked=True)
        if available_at:
            f['records'][0].update(material_available_at=available_at,document_sha=corpus.digest(document),
                proof=dict(proof(document,quote),available_at=available_at,document_sha=corpus.digest(document),url=source_check['url']))
        return calculated(f)
    return result
