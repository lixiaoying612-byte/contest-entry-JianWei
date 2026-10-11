"""Offline, claim-scoped credibility rules. No network or runtime database writes.

Inputs are reviewed evidence features, not model confidence. Callers must retain
the underlying documents and verify identity, semantic fit, time and independence.
This calculator validates the feature contract; it does not authenticate sources.
"""
import argparse
import json
from pathlib import Path

VERSION = 'credibility-20261004-v3-confirmed-rules'
BASE = 50
SOURCES = {
    'unknown': (0, '来源身份未明'),
    'platform': (5, '平台转载，原发布者待核'),
    'identified': (10, '可识别的媒体或机构来源'),
    'professional': (15, '已核身份、采编责任与适用领域的专业媒体'),
    'first_party': (15, '已核当事方原始发布身份'),
    'formal': (20, '已核正式披露或主管机构文件身份'),
}
EVIDENCE = {
    'none': (0, '当前只有报道陈述'),
    'attributed': (5, '间接引述或匿名线索，支持有限'),
    'report': (10, '已核报道依据及适用范围'),
    'disclosure_report': (15, '具体转述具名披露或一手发布，原件尚未核对'),
    'direct': (15, '完整披露副本、直接采访或当事方说明支持'),
    'primary': (20, '适用原始记录直接支持限定事实'),
}
PATHS = {
    'unknown': (0, '上游出处不明'),
    'named': (3, '上游已明确，原始链条尚未闭合'),
    'traced': (5, '已核原始出处、引用链及材料时间'),
}
PROBLEMS = {
    'qualification': (10, 59, '关键条件或口径不符'),
    'conflict': (20, 49, '核心事实存在未解决冲突'),
    'refuted': (40, 19, '适用原始记录直接反驳'),
}


def check_record(record):
    for key in ('id', 'quote', 'reason'):
        if not isinstance(record.get(key), str) or not record[key].strip():
            raise ValueError('missing_' + key)
    s = SOURCES[record['source']][0]
    e = EVIDENCE[record['evidence']][0]
    p = PATHS[record['path']][0]
    if record['relation'] not in ('reported', 'supports', 'refutes', 'related'):
        raise ValueError('invalid_relation')
    for key in ('applicable', 'time_eligible', 'identity_checked', 'evidence_checked', 'path_checked'):
        if type(record.get(key)) is not bool:
            raise ValueError('missing_boolean_' + key)
    if s >= 15 and not record['identity_checked']:
        raise ValueError('source_identity_not_checked')
    if record['source'] == 'professional':
        for key in ('publisher_name', 'source_basis', 'source_reference'):
            if not isinstance(record.get(key), str) or not record[key].strip():
                raise ValueError('professional_source_needs_' + key)
    is_disclosure_report = record['evidence'] == 'disclosure_report'
    if is_disclosure_report:
        if record['relation'] != 'reported':
            raise ValueError('disclosure_report_is_not_verified_direct_support')
        if record.get('attribution_checked') is not True:
            raise ValueError('attribution_not_reviewed')
        attribution = record.get('attribution', {})
        if not isinstance(attribution, dict):
            raise ValueError('attribution_must_be_object')
        for key in ('publisher', 'object', 'action'):
            value = attribution.get(key)
            if not isinstance(value, str) or not value.strip() or value not in record['quote']:
                raise ValueError('attribution_not_bound_' + key)
        if p < 3:
            raise ValueError('disclosure_report_needs_named_origin')
    if e >= 10 and not is_disclosure_report and not record['evidence_checked']:
        raise ValueError('evidence_not_checked')
    if e == 20 and not record['identity_checked']:
        raise ValueError('primary_identity_not_checked')
    if p == 5 and not record['path_checked']:
        raise ValueError('path_not_checked')
    if p and not record.get('origin'):
        raise ValueError('path_needs_named_origin')
    if record['relation'] == 'reported' and e > 5 and not is_disclosure_report:
        raise ValueError('reporting_is_not_direct_support')
    if record.get('independent_of'):
        if not isinstance(record['independent_of'], list) or any(not isinstance(x, str) for x in record['independent_of']):
            raise ValueError('independence_must_reference_origins')
        if (not record.get('origin') or not record.get('independence_reason') or e < 10
                or record['relation'] != 'supports' or not record['evidence_checked']):
            raise ValueError('independence_without_reviewed_basis')
    return s, e, p


def score_label(score):
    return next(label for low, label in [(90, '高'), (80, '较高'), (65, '中等'),
                                        (50, '依据有限'), (0, '偏低')] if score >= low)


def score_claim(claim):
    if not isinstance(claim.get('claim'), str) or not claim['claim'].strip():
        raise ValueError('claim_required')
    if claim.get('body_available') is not True:
        raise ValueError('body_required_keep_in_intake')
    if claim.get('article_source', 'unknown') not in ('unknown', 'platform', 'identified'):
        raise ValueError('unreviewed_article_cannot_claim_formal_identity')
    records = claim.get('records', [])
    if len({r['id'] for r in records}) != len(records):
        raise ValueError('duplicate_material_id')
    values = {r['id']: check_record(r) for r in records}
    eligible = [r for r in records if r['applicable'] and r['time_eligible']]
    supports = [r for r in eligible if r['relation'] in ('reported', 'supports')]
    refutes = [r for r in eligible if r['relation'] == 'refutes' and values[r['id']][1] >= 10]
    # Two extra roots earn 10 only when they are also independent of each other.
    pairs = {frozenset((r['origin'], other)) for r in supports if r.get('origin')
             and r.get('independence_reason') and r['relation'] == 'supports'
             and r['evidence_checked'] and values[r['id']][1] >= 10
             for other in r.get('independent_of', []) if other != r['origin']}

    def corroboration(anchor):
        root = anchor.get('origin')
        if not root or values[anchor['id']][1] < 10:
            return []
        extra = sorted({r['origin'] for r in supports if r.get('origin') and r['origin'] != root
                        and r['relation'] == 'supports' and r['evidence_checked']
                        and values[r['id']][1] >= 10 and frozenset((root, r['origin'])) in pairs})
        for i, first in enumerate(extra):
            for second in extra[i + 1:]:
                if frozenset((first, second)) in pairs:
                    return [first, second]
        return extra[:1]

    def candidate_score(record):
        s, e, p = values[record['id']]
        total = BASE + s + e + p + 5 * len(corroboration(record))
        ceiling = (59 if s == 0 and e < 10 else 79 if e <= 5
                   else 89 if record['evidence'] == 'disclosure_report' else 95)
        return min(ceiling, total), total, e, record['id']

    best = max(supports, key=candidate_score, default=None)
    if best:
        s, e, p = values[best['id']]
        source_name = SOURCES[best['source']][1]
        evidence_name = EVIDENCE[best['evidence']][1]
        path_name = PATHS[best['path']][1]
    else:
        s, source_name = SOURCES[claim.get('article_source', 'unknown')]
        e = p = 0
        evidence_name, path_name = EVIDENCE['none'][1], PATHS['unknown'][1]
    independent_roots = set(corroboration(best)) if best else set()
    independent = min(10, 5 * len(independent_roots))
    problems = {}
    for issue in claim.get('problems', []):
        if not isinstance(issue,dict) or 'kind' not in issue:raise ValueError('problem_requires_structured_qualification')
        kind = issue['kind']
        if kind != 'qualification':
            raise ValueError('conflict_and_refutation_must_come_from_evidence')
        if not issue.get('root') or not issue.get('reason') or not issue.get('quote'):
            raise ValueError('problem_basis_required')
        basis = next((r for r in eligible if r['id'] == issue.get('basis_material')), None)
        if not basis or issue['quote'] not in basis['quote']:
            raise ValueError('problem_quote_not_bound')
        problems[issue['root']] = dict(issue, points=PROBLEMS[kind][0])
    # Counterevidence always participates, including when a caller omitted a flag.
    direct_refutation = any(values[r['id']][1] == 20 for r in refutes)
    substantive_support = any(values[r['id']][1] >= 10 and r['relation'] == 'supports' for r in supports)
    if refutes:
        kind = 'refuted' if direct_refutation and not substantive_support else 'conflict'
        # A shared root is one problem, even when it produced many articles.
        for r in refutes:
            root = r.get('problem_root') or 'claim_counterevidence'
            issue = dict(root=root, kind=kind, points=PROBLEMS[kind][0],
                         reason=r['reason'], quote=r['quote'])
            if issue['points'] > problems.get(root, {}).get('points', 0):
                problems[root] = issue
    penalty = sum(x['points'] for x in problems.values())
    items = [dict(name='基础分', points=BASE, reason='沿用原规则起点，不代表真实性概率'),
             dict(name='来源', points=s, reason=source_name),
             dict(name='适用证据', points=e, reason=evidence_name),
             dict(name='独立印证', points=independent,
                  reason=f'新增{len(independent_roots)}个已核独立源头；最多加10分' if independent
                  else '未建立独立交叉支持；转载不重复加分'),
             dict(name='传播溯源', points=p, reason=path_name)]
    items.extend(dict(name='问题扣分', points=-v['points'], reason=v['reason']) for v in problems.values())
    raw = sum(x['points'] for x in items)
    caps = [dict(maximum=95, reason='模型统一上限')]
    if e <= 5:
        caps.append(dict(maximum=79, reason='本条主张缺少充分适用依据'))
    if best and best['evidence'] == 'disclosure_report':
        caps.append(dict(maximum=89, reason='具体披露转述可以较高，未核原件不进入高档'))
    if s == 0 and e < 10:
        caps.append(dict(maximum=59, reason='来源身份未明且缺少可核证据'))
    for issue in problems.values():
        caps.append(dict(maximum=PROBLEMS[issue['kind']][1], reason=PROBLEMS[issue['kind']][2]))
    score = max(0, min(raw, *(x['maximum'] for x in caps)))
    kinds = {x['kind'] for x in problems.values()}
    directly_checked = bool(best and best['relation'] == 'supports' and best['evidence_checked'])
    status = ('存在反驳依据' if 'refuted' in kinds else '存在冲突' if 'conflict' in kinds
              else '关键条件需核对' if 'qualification' in kinds
              else '已核原始记录' if directly_checked and e == 20
              else '有直接材料支持' if directly_checked and best['evidence'] == 'direct'
              else '有适用材料支持' if directly_checked and e >= 10
              else '依据当前报道初评')
    label = score_label(score)
    if problems:
        short_reason = max(problems.values(), key=lambda x: x['points'])['reason']
    elif best and best['evidence'] == 'disclosure_report':
        name = best.get('publisher_name', '报道')
        short_reason = f'{name}具体转述具名披露，主体与事项明确；原件尚未核对'
    elif directly_checked and e == 20:
        short_reason = '已核适用原始记录，主体、事项与关键条件一致'
    elif directly_checked and best['evidence'] == 'direct':
        short_reason = '已核直接采访、披露副本或相关材料，支持本条限定主张'
    elif directly_checked and e >= 10:
        short_reason = '已核报道依据及适用范围，支持本条限定主张'
    else:
        short_reason = '已明确上游线索，具体结论依据尚待核实' if p else '依据当前报道初评，具体说法缺少可核依据'
    active_caps = [x for x in caps if raw > x['maximum']]
    return dict(rule_version=VERSION, score=score, label=label, status=status, raw_score=raw,
                short_reason=short_reason, score_items=items, score_caps=caps, applied_caps=active_caps,
                display=dict(score_text=f'{score}分', label=label, reason=short_reason,
                             calculation=' '.join(('+' if i['points'] >= 0 else '−') + str(abs(i['points']))
                                                  for i in items if i['points']).lstrip('+'),
                             raw_score=raw, final_score=score,
                             adjustment='；'.join(x['reason'] for x in active_caps)),
                selected_material=best['id'] if best else None,
                additional_independent_roots=sorted(independent_roots),
                score_scope=claim['claim'], probability=False)


def score_event(claims):
    """Aggregate explicitly reviewed roles; this does not infer a headline's scope."""
    if len({c['id'] for c in claims}) != len(claims):
        raise ValueError('duplicate_claim_id')
    if any(c.get('role') not in ('current_core', 'additional', 'historical') for c in claims):
        raise ValueError('explicit_claim_roles_required')
    current = [c for c in claims if c['role'] != 'historical']
    core_text = [c['claim'].strip() for c in current if c['role'] == 'current_core']
    if len(set(core_text)) != len(core_text):
        raise ValueError('duplicate_current_claim_merge_records')
    scored = [dict(id=c['id'], role=c['role'], **score_claim(c)) for c in current]
    core = [c for c in scored if c['role'] == 'current_core']
    if not core:
        raise ValueError('current_core_claim_required')
    determining = min(core, key=lambda c: (c['score'], c['id']))
    return dict(rule_version=VERSION, score=determining['score'], label=determining['label'],
                short_reason=determining['short_reason'], determining_claim=determining['id'],
                score_scope=[c['score_scope'] for c in core], claims=scored,
                excluded_historical_claims=[c['id'] for c in claims if c['role'] == 'historical'],
                probability=False)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('inputs', type=Path)
    args = parser.parse_args()
    data = json.loads(args.inputs.read_text(encoding='utf-8-sig'))
    results = [dict(id=c['id'], title=c['claim'], sample_type=c['sample_type'],
                    input_basis=c.get('input_basis', ''), **score_claim(c)) for c in data['claims']]
    print(json.dumps(dict(rule_version=VERSION, status='confirmed_rules_offline_implementation',
                          results=results), ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
