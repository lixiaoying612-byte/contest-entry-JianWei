"""Finite, source-bound stage guard; never infers event identity from numbers.

Recognizes literal Chinese/English ordinal phases and explicit initial scope.
Numbers only align endpoint fields with a selected phase clause, or detect a
relation rationale borrowing another phase's quantity. No general NLP claim.
"""
import re
import hashlib
from decimal import Decimal
from event_workbench import current_event_dossiers as dossiers

VERSION = 'selected-phase-and-asset-scope-v2'
ORDINAL = {'一':'1','二':'2','三':'3','四':'4','五':'5','六':'6','七':'7','八':'8','九':'9','十':'10',
           'first':'1','second':'2','third':'3','fourth':'4','fifth':'5','sixth':'6'}
CN_NUMERALS = '零〇一二三四五六七八九十百千万亿兆两壹贰叁肆伍陆柒捌玖拾佰仟点'
# Capture the whole numeral before interpreting it. A supported suffix inside
# an unsupported numeral must never become a smaller, seemingly certain phase.
PHASE = re.compile(r'(?<![第\d'+CN_NUMERALS+r'])第?(['+CN_NUMERALS+r']+|\d+)\s*(?:阶段|期)|\b(first|second|third|fourth|fifth|sixth)\s+phase\b|\bphase\s+(\d+)\b', re.I)
INITIAL = re.compile(r'初始阶段|初期|\binitial\s+phase\b|\bbegin\s+operations\b', re.I)
NUMBER = re.compile(r'(?<![\d.])(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*(?:万|亿|million\b|thousand\b)?', re.I)


def phases(text):
    result = set()
    for match in PHASE.finditer(text):
        value = next(v for v in match.groups() if v).lower()
        if value in ORDINAL:tag=ORDINAL[value]
        elif value.isdecimal():tag=str(int(value))
        elif re.fullmatch(r'[一二三四五六七八九]?十[一二三四五六七八九]?',value):
            tens,ones=value.split('十');tag=str(int(ORDINAL.get(tens,'1'))*10+int(ORDINAL.get(ones,'0')))
        else:tag='unrecognized:'+value
        result.add(tag)
    if INITIAL.search(text):result.add('initial')
    return result


def numbers(text):
    result = set()
    for match in NUMBER.finditer(PHASE.sub('',text)):
        value = match.group().strip().lower().replace(',', '')
        factor = Decimal(1)
        for suffix, multiplier in (('万',10000),('亿',100000000),('million',1000000),('thousand',1000)):
            if value.endswith(suffix):value=value[:-len(suffix)].strip();factor=Decimal(multiplier);break
        result.add(str((Decimal(value)*factor).normalize()))
    return result


def endpoint(mention, jid, document):
    body = mention['body']
    text = (body.get('summary') or '')+' '+(body.get('status',{}).get('phase') or '')
    claimed = phases(text)
    proofs, issues, _ = dossiers.selected_proofs(document,jid,mention,'summary',body.get('summary'))
    selected_numbers = numbers(text)
    for detail in body.get('details',[]):selected_numbers |= numbers(detail.get('value_text',''))
    # The borrowed-quantity rule is deliberately capacity-scoped. Employment,
    # investment and dates in the same paragraph cannot stand in for capacity.
    capacity_numbers=set()
    for match in re.finditer(r'(?:产能|capacity)[^\d，。;；]{0,35}('+NUMBER.pattern+r')',text,re.I):
        capacity_numbers |= numbers(match.group(1))
    for i,detail in enumerate(body.get('details',[])):
        if not re.search(r'产能|capacity',detail.get('name',''),re.I):continue
        selected,errors,_=dossiers.selected_proofs(document,jid,mention,f'details[{i}]',detail)
        if not errors:
            capacity_numbers |= numbers(detail.get('value_text','')) & {n for p in selected for n in numbers(p['quote'])}
    clauses = []
    for proof in proofs:
        # Keep decimal points and thousands separators inside a number.
        text=proof['quote'];begin=0
        ends=[(m.start(),m.end()) for m in re.finditer(r'(?<!\d)[,.]|[,.](?!\d)|[，；;。!?]',text)]+[(len(text),len(text))]
        for end,next_begin in ends:
            quote=text[begin:end];tags=phases(quote);start=proof['start']+begin;begin=next_begin
            if tags:
                clauses.append(dict(phases=sorted(tags),numbers=sorted(numbers(quote)),
                    proof=dict(proof,start=start,end=start+len(quote),quote=quote,quote_sha256=hashlib.sha256(quote.encode()).hexdigest())))
    bound = [c for c in clauses if not any(p.startswith('unrecognized:') for p in claimed) and claimed.intersection(c['phases']) and
             (not selected_numbers or selected_numbers.intersection(c['numbers']))]
    return dict(member_id=mention['id'],original_index=mention['original_index'],summary=body.get('summary'),
        claimed_phases=sorted(claimed),bound_phases=sorted({p for c in bound for p in c['phases'] if p in claimed}),
        endpoint_numbers=sorted(selected_numbers),capacity_numbers=sorted(capacity_numbers),selected_phase_clauses=bound,all_phase_clauses=clauses,
        proofs=proofs,issues=issues,scope='explicit_selected_phase' if bound and not issues else 'unspecified_or_unbound')


def check(proposal, mentions, owners, documents):
    ids=proposal.get('member_ids',[])
    if len(ids)!=2 or any(m not in mentions or owners[m] not in documents for m in ids):return None
    endpoints=[endpoint(mentions[m],owners[m],documents[owners[m]]) for m in ids]
    # Source-bound product/drug/bond codes are stronger identity constraints than
    # a shared company. Formatting hyphens do not create a different code.
    assets=[]
    for mid in ids:
        body=mentions[mid]['body']
        selected=(body.get('summary') or '')+' '+ ' '.join(d.get('value_text','') for d in body.get('details',[]))
        codes={re.sub(r'[\s-]','',x).upper() for x in re.findall(r'(?<![A-Za-z])[A-Z]{2,6}[- ]?\d{3,5}[A-Z]?(?![A-Za-z0-9])',selected)}
        bonds={re.sub(r'\s','',x).upper() for x in re.findall(r'\d{2}[\u3400-\u9fff]{1,10}(?:MTN|SCP|CP|PPN)\d{3}',selected,re.I)}
        assets.append(dict(codes=sorted(codes),bonds=sorted(bonds)))
    if any(a and b and a!=b for a,b in ((set(assets[0][k]),set(assets[1][k])) for k in ('codes','bonds'))):
        return dict(version=VERSION,code='different_explicit_asset_identity',reason='两端所选原文的药物、产品或债券身份不一致，不能仅凭同公司合并为同事件或同项目进展。',endpoints=endpoints,asset_identities=assets,confirmed=False)
    if proposal.get('raw',{}).get('k')=='same_project_new_progress':
        mvp=all(mentions[m].get('monitoring_scope',{}).get('request_revision')=='news-mvp-v1' for m in ids)
        phases_=[mentions[m]['body'].get('status',{}).get('phase') for m in ids]
        if mvp and not proposal.get('fields') and not (all(phases_) and phases_[0]!=phases_[1]):
            return dict(version=VERSION,code='progress_change_not_bound',reason='未指出有引用依据的变化字段，也未形成两端明确不同的阶段；新增报道细节不能自动当作业务进展。',endpoints=endpoints,confirmed=False)
        return None
    reason=None;code=None
    if any(p.startswith('unrecognized:') for e in endpoints for p in e['claimed_phases']):
        code='unsupported_complete_phase_token'
        reason='端点包含超出有限支持范围的完整阶段词，未识别其期次；保留原词待审，不能截取尾部当作较小阶段。'
    elif any(len(e['claimed_phases'])>1 for e in endpoints):
        code='multiple_endpoint_phases_need_scope_review'
        reason='端点同时包含多个明确阶段，未可靠拆定本次身份范围；暂不合并，也不自动改绑其中一期。'
    elif any(e['claimed_phases'] and (e['issues'] or not e['bound_phases']) for e in endpoints):
        code='explicit_phase_not_bound_to_endpoint'
        reason='端点提及含明确阶段，但无法可靠绑定到所选原句的具体范围；暂不合并，未推定未知端点属于一期。'
    elif all(e['bound_phases'] and not e['issues'] for e in endpoints) and not set(endpoints[0]['bound_phases']).intersection(endpoints[1]['bound_phases']):
        code='different_explicit_endpoint_phases'
        reason='两端所选原句绑定的明确阶段不同；同项目不自动等于同一阶段事件，暂不合并，也未判定先后方向。'
    else:
        why_numbers=numbers(proposal.get('raw',{}).get('why',''))
        for own,peer in (endpoints,endpoints[::-1]):
            if own['issues'] or peer['issues'] or not own['bound_phases'] or peer['claimed_phases']:continue
            own_values={v for c in own['selected_phase_clauses'] for v in c['numbers']} & set(own['capacity_numbers'])
            if not own_values or not peer['capacity_numbers']:continue
            for clause in own['all_phase_clauses']:
                if set(clause['phases']).intersection(own['bound_phases']):continue
                borrowed=set(clause['numbers']) & set(peer['capacity_numbers']) & why_numbers
                if borrowed-own_values:
                    code='relation_borrows_other_phase_scope'
                    reason='关系理由使用了所选原文另一明确阶段的数量，而本端点绑定不同阶段；该数量不能替代端点范围。未知端点仍未定期次，暂不合并。'
                elif borrowed:
                    code='equal_capacity_cannot_resolve_unknown_phase'
                    reason='所选原文多个明确阶段数量相同，另一端点未指明阶段；相同数量不足以定位同一期次，暂不合并。'
    if not reason:return None
    return dict(version=VERSION,code=code,reason=reason,endpoints=endpoints,quantity_policy='数量仅定位阶段范围；差值本身不否决同事件',confirmed=False)
