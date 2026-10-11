"""ENG036: shared-candidate reason consistency; offline, no repairs or A edits."""
import copy
import json
from event_workbench import compact_model_io as compact

SCOPE_ADMISSION_VERSION='named-scope-item-admission-v2-sourced-alias'


def admit_named_scope(raw, content, revision='named-monitoring-v4'):
    """Keep independently checked items, never repair a rejected item's content."""
    value=compact.common.strict_json(raw)
    if not isinstance(value,dict) or set(value)!={'items'} or not isinstance(value['items'],list):
        raise ValueError('named_scope_envelope')
    indices=[a['i'] for a in content['a']]
    returned=[row.get('index') if isinstance(row,dict) else None for row in value['items']]
    # Ambiguous identity/coverage is a shared failure; it cannot be safely split.
    if (any(type(i)!=int for i in indices+returned) or len(set(indices))!=len(indices)
            or len(set(returned))!=len(returned) or set(returned)!=set(indices)):
        raise ValueError('named_scope_coverage_or_identity')
    ids=[d['definition_id'] for d in content['c']]
    refs=[r for r,_ in content['u']]
    if len(set(ids))!=len(ids) or len(set(refs))!=len(refs) or 'per' in content:
        raise ValueError('named_scope_shared_binding')
    try:
        return named_scope(raw,content,revision=revision)
    except (ValueError,KeyError,TypeError,IndexError) as error:
        original_error=str(error)
    by={a['i']:a for a in content['a']};items=[];rejected=[];accepted=[]
    for row in value['items']:
        index=row['index'];subset=dict(content,a=[by[index]])
        try:
            checked=named_scope(json.dumps(dict(items=[row]),ensure_ascii=False),subset,revision=revision)
            # A matching row salvaged from a rejected response must cite its own
            # already checked body evidence, not just another object's paragraph.
            for match in row['matches']:
                if not any(r.startswith('B') and r in by[index]['q'] for r in match['source_refs']):
                    raise ValueError('named_scope_item_evidence_not_bound')
            items.extend(checked['items']);accepted.append(index)
        except (ValueError,KeyError,TypeError,IndexError) as error:
            rejection=dict(original_index=index,reason=str(error),original_named=copy.deepcopy(row))
            rejected.append(rejection)
            items.append(dict(original_index=index,status='quarantined',matches=[],unmatched=None,
                              confirmed=False,semantic_review_status='not_ready',rejection=rejection))
    return dict(version=revision,adapter_version=SCOPE_ADMISSION_VERSION,items=items,
                candidate_ids=ids,candidate_scope='supplied_candidates_only',confirmed=False,
                original_named=value,original_error=original_error,accepted_original_indices=accepted,
                rejected_items=rejected,usable=bool(accepted))

NAMED_RULES='''你判断固定新闻事项是否属于给定监测定义。输入u是编号原文，c是共享候选定义，a是固定事项（i是原序号，s摘要，q引用）。不得改写A，不核验真假。
仅输出JSON：{"items":[{"index":0,"matches":[],"unmatched":{"code":"not_applicable","reason":"结合该事项说明候选为什么不适用","source_refs":["B1"]}}]}。
每个a.i恰好一项。matches只含有依据的正向或可能匹配，每条完整字段为：{"definition_id":"实际c中ID","decision":"matched或possible","basis":"direct或indirect","source_refs":["B1"],"definition_quote":null,"reason":"对象联系、事项符合定义哪项条件、还缺什么"}。
有matches则unmatched为null。没有matches则unmatched必填code、reason、source_refs，code仅not_applicable/no_target/insufficient/no_candidate。只有c为空才no_candidate。对象明确但事项不同用not_applicable，主体不明no_target，依据不足insufficient。对当前c的否定不代表全目录无关。
direct指监测对象原名或代码直接出现在所选原文，definition_quote必须null；可引用明确指代的前文主体句，但不得拼接其他主体。indirect指原文和定义建立间接对象联系，definition_quote必须逐字摘录c.description中相关条件，不得概括、添字或加省略号。所有source_refs必须存在，必须包括当前事项依据，无法支持就待判断。
公司相同不代表所有事项匹配该公司定义。技术成果、升级及明确研发计划可按定义条件判断；承诺、宣传目标、招聘、背景数据不能仅因提及研发而当作研发成果。计划与否定照原文保留，不自动删去；定义举例不是唯一事项或统一阈值。仅输出有根据的可能匹配，不能把明确不符合的定义放入possible。不得输出逐定义否定矩阵。
每条理由都具体依据事项和候选，不用只有“无关”这类泛化文字。全部结果未经独立核验。'''

NAMED_RULES_V5='''判断固定事项a是否适用共享定义c；u为原文，a.i为不可改变的原序号。只返回JSON，不重抽A、不核验事实。
唯一输出结构：{"items":[{"index":0,"matches":[],"unmatched":{"code":"not_applicable","reason":"本事项为什么不适用当前定义，说明对象或事项条件","source_refs":["B1"]}}]}。
有依据的匹配则matches为数组，每条恰有以下7个字段：{"definition_id":"当前c中的ID","decision":"matched或possible","basis":"direct或indirect","source_refs":["B1"],"target_refs":["B2"],"definition_quote":null,"reason":"对象联系、事项必要条件、尚缺依据"}。示例字符串只是字段说明，不能当作答案或照填占位ID。
允许字段仅为definition_id、decision、basis、source_refs、target_refs、definition_quote、reason共7个，严格只使用这7个字段；不要增加补充字段。items每项恰有index/matches/unmatched。每个a.i恰好一项，匹配非空时unmatched必须null。
source_refs支持当前具体事项；target_refs支持对象联系。direct要求target_refs至少一条原文逐字含候选target_name或target_code，definition_quote必须null；若只有产品简称，需要关系明确的主体句，否则不能猜对象。indirect要求原文与定义共同建立实际间接对象联系，definition_quote逐字摘录description中的对应条件；阅读或引用定义本身不等于间接联系。引用都来自u，不省略、拼接或改写定义原句。
没有匹配时unmatched恰有code/reason/source_refs；code只可not_applicable/no_target/insufficient/no_candidate。仅c为空才no_candidate；对象联系未建立no_target；证据不足insufficient；已知对象类型或事项必要条件不符合not_applicable。
possible需要对象联系及相关事项的正面依据，不能用于已明确相矛盾的目标类型、行业或事项条件，也不能以未来可能相关替代当前证据。理由若承认目标行业不符，或事项与定义没有关联依据，就不能返回possible。单纯同公司、出现价格/资本/技术等词不够。
按定义正文必要条件判断，不把示例当穷举或统一阈值。计划和否定按原文保留，不因为尚未发生就排除；普通用工、宣传承诺和规模背景不自动等同研发进展。每条reason说明原文与定义的具体关系。未匹配只限本次c，不能宣称全目录无关。所有判断待独立核验。'''

CATEGORY_GUIDANCE='''
类别边界补充20261005-v1：对象联系和事项类别是两项判断。同一文章中，门店调整的引文不能给另一项股权处置或物业出租证明零售策略调整；股权、项目、药物等每个具体事项分别对照定义必要条件。泛称优化资产、公司相同或所属行业相同，不能替代该事项的类别依据。
持续推广可以是具体业务事实，但不自动等于战略调整，也不等于已出货；须说明原文证明哪项技术路线、产品布局或市场行动，缺少该关联则保留待核。反过来，具体计划或早期行动已经符合定义时，不得增加必须先有营收或量产的条件。仅判断本次给定候选，不把未匹配解释为整篇新闻无用。
药物或产品名称中的Ⅱ等字符属于事项名称，不根据名称推断临床二期或研发阶段；阶段只由审批、试验等原文事实及限定确定。'''
NAMED_RULES_V5+=CATEGORY_GUIDANCE

def named_scope(raw, content, revision='named-monitoring-v3'):
    """Lossless named fields to the frozen B checker; missing fields never repaired."""
    from event_workbench.competition_candidates import target_evidence
    value=compact.common.strict_json(raw)
    if not isinstance(value,dict) or set(value)!={'items'} or not isinstance(value['items'],list):raise ValueError('named_scope_envelope')
    legacy=dict(m=[],n=[]);details={};units=dict(content['u']);indices={a['i'] for a in content['a']}
    def evidence(refs):
        if not isinstance(refs,list) or not refs or any(not isinstance(r,str) or r not in units for r in refs):raise ValueError('named_scope_evidence')
    for row in value['items']:
        if not isinstance(row,dict) or set(row)!={'index','matches','unmatched'}:raise ValueError('named_scope_item_fields')
        i=row['index']
        if type(i)!=int or i not in indices or i in details:raise ValueError('named_scope_index')
        details[i]=row
        if not isinstance(row['matches'],list):raise ValueError('named_scope_matches')
        if row['matches']:
            if row['unmatched'] is not None:raise ValueError('named_scope_conflict')
            for match in row['matches']:
                fields={'definition_id','decision','basis','source_refs','definition_quote','reason'}
                if revision=='named-monitoring-v4':fields.add('target_refs')
                elif revision!='named-monitoring-v3':raise ValueError('named_scope_version')
                if not isinstance(match,dict) or set(match)!=fields:raise ValueError('named_scope_match_fields')
                evidence(match['source_refs'])
                refs=match['source_refs']
                if revision=='named-monitoring-v4':
                    evidence(match['target_refs']);refs=list(dict.fromkeys(refs+match['target_refs']))
                    definition=next((d for d in content['c'] if d['definition_id']==match['definition_id']),None)
                    if match['basis']=='direct' and (not definition or not any(target_evidence(definition,units[r]) for r in match['target_refs'])):raise ValueError('named_scope_target_evidence')
                if match['decision'] not in ('matched','possible') or match['basis'] not in ('direct','indirect'):raise ValueError('named_scope_enum')
                legacy['m'].append([i,match['definition_id'],{'matched':'m','possible':'p'}[match['decision']],{'direct':'d','indirect':'i'}[match['basis']],refs,match['definition_quote'],match['reason']])
        else:
            n=row['unmatched']
            if not isinstance(n,dict) or set(n)!={'code','reason','source_refs'} or not isinstance(n['reason'],str) or not n['reason'].strip():raise ValueError('named_scope_unmatched')
            evidence(n['source_refs']);legacy['n'].append([i,n['code']])
    if set(details)!=indices:raise ValueError('named_scope_coverage')
    result=scope_decisions(json.dumps(legacy,ensure_ascii=False),content)
    for item in result['items']:
        n=details[item['original_index']]['unmatched']
        if n:
            item['unmatched'].update(reason=n['reason'],source_refs=n['source_refs'],evidence=[dict(ref=r,quote=units[r]) for r in n['source_refs']])
    result.update(version=revision,adapter_version='named-to-frozen-b-v1',checked_legacy=legacy,original_named=value)
    return result


def scope_decisions(raw_b, content):
    """Retain checked model judgments, never turn failed/absent matching into relevance."""
    checked=check(raw_b,content,candidate_scope='shared_c')
    if not checked['b_usable']:raise ValueError('scope_requires_checked_matching')
    decoded=checked['original_decoded'];units=dict(content['u']);items=[]
    for mention in content['a']:
        index=mention['i'];matches=[copy.deepcopy(m) for m in decoded['matches'] if m['mention_ref']==index]
        negative=next((n for n in decoded['unmatched'] if n['mention_ref']==index),None)
        for match in matches:
            match['evidence']=[dict(ref=r,quote=units[r]) for r in match['source_refs']]
            aliases=[a for a in checked.get('alias_derivations',{}).get('items',[]) if a['definition_id']==match['definition']['definition_id'] and a['source_ref'] in match['source_refs']]
            if aliases:match['target_aliases']=copy.deepcopy(aliases)
        state='monitoring_candidate' if any(m['decision']=='matched' for m in matches) else 'pending'
        if negative and negative['code']=='not_applicable':state='not_applicable_to_supplied_candidates'
        items.append(dict(original_index=index,status=state,matches=matches,unmatched=copy.deepcopy(negative),
                          confirmed=False,semantic_review_status='pending'))
    result=dict(version='monitoring-scope-v1',candidate_ids=[d['definition_id'] for d in content['c']],
                candidate_scope='supplied_candidates_only',items=items,confirmed=False)
    if checked.get('alias_derivations'):
        result.update(alias_derivations=checked['alias_derivations'],frozen_decoder_issues=checked['original_decoded']['issues'])
    return result


def check(raw_b, content, *, candidate_scope=None):
    """Wrap frozen B decoding. A is carried unchanged, never revalidated/released.

    Only explicitly shared_c is supported. A per key is rejected even when empty:
    its absence cannot be interpreted as an empty per-mention candidate set.
    b_usable means ready for semantic review, never verified or an alert.
    """
    result = dict(raw_b=copy.deepcopy(raw_b),
                  a=copy.deepcopy(content.get('a')) if isinstance(content, dict) else None,
                  candidate_scope=candidate_scope, original_decoded=None,
                  consistency_issues=[], b_usable=False,
                  semantic_review_status='not_ready', confirmed=False)
    def reject(code, field):
        result['consistency_issues'].append(dict(code=code, field=field))
        return result
    if candidate_scope != 'shared_c':
        return reject('unsupported_scope', 'candidate_scope')
    if not isinstance(content, dict):
        return reject('invalid_decode_input', 'content')
    if 'per' in content:
        return reject('unsupported_scope', 'content.per')
    candidates = content.get('c')
    if not isinstance(candidates, list):
        return reject('invalid_candidates', 'content.c')
    ids = [d.get('definition_id') if isinstance(d, dict) else None for d in candidates]
    if any(not isinstance(i, str) or not i.strip() for i in ids) or len(set(ids)) != len(ids):
        return reject('invalid_candidates', 'content.c')
    try:
        decoded = compact.decode_b(raw_b, content)
    except (ValueError, TypeError, KeyError, IndexError, RecursionError):
        return reject('invalid_decode_input', 'content')
    result['original_decoded'] = decoded
    # Keep the frozen decoder and its diagnostics unchanged. Only its literal
    # target rule may be extended by the sourced identity registry below.
    if decoded['issues']:
        if any(issue['code']!='target_evidence_invalid' for issue in decoded['issues']):return result
        from event_workbench.competition_candidates import target_evidence,target_aliases
        definitions={d['definition_id']:d for d in candidates};units=dict(content['u']);aliases=[]
        for row in decoded['compact_parsed']['m']:
            _,did,_,basis,refs,dq,_=row
            if basis!='d':continue
            if dq is not None or not any(target_evidence(definitions[did],units[r]) for r in refs):return result
            aliases.extend(dict(a,definition_id=did,source_ref=r) for r in refs for a in target_aliases(definitions[did],units[r]))
        if not aliases:return result
        result['alias_derivations']=dict(version=SCOPE_ADMISSION_VERSION,items=aliases,meaning='object_identity_only; category_and_impact_still_require_evidence')
    for index, (mention, reason) in enumerate(decoded['compact_parsed']['n']):
        if reason == 'no_candidate' and candidates:
            result['consistency_issues'].append(dict(code='candidate_reason_conflict',
                field=f'$.n[{index}][1]', mention_ref=mention,
                reason=reason, effective_candidate_count=len(candidates)))
    result['b_usable'] = not result['consistency_issues']
    if result['b_usable']:
        result['semantic_review_status'] = 'pending'
    return result
