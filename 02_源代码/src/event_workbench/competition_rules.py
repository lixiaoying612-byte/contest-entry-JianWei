"""Transparent competition rules; scores are ordinal, never probabilities.

Inputs must already be checked and bound to a particular claim/object/phase.
Source provenance cannot be inferred from a URL, source count or model text.
"""
VERSION = 'competition-rules-20260923-v1'


def claim_score(claim,executed=True):
    evidence=[]
    for assessment in claim.get('valid_assessments',[]):
        check=assessment.get('source_check',{});proof=assessment.get('proof')
        evidence.append(dict(judgment=assessment.get('proposal',{}).get('judgment'),
            applicable=bool(proof),located=bool(proof),
            primary_verified=check.get('source_kind')=='public_primary' and check.get('status') in ('original_page_checked','original_pdf_checked'),
            independence_verified=check.get('independence')=='verified_independent',
            origin_id=check.get('origin_id') or check.get('url') or assessment.get('source_row')))
    return support_score(executed=executed,evidence=evidence)


def support_score(*, executed, evidence):
    """Evidence rows include judgment, applicable, located, primary_verified,
    independence_verified and origin_id. Reprints must share origin_id.
    """
    rows = [e for e in evidence if e.get('applicable') is True and e.get('located') is True]
    support = [e for e in rows if e.get('judgment') == 'supports']
    refuted = any(e.get('judgment') == 'refutes' for e in rows)
    primary = [e for e in support if e.get('primary_verified') is True]
    independent = any(
        p.get('origin_id') and e.get('origin_id') and p['origin_id'] != e['origin_id']
        and e.get('independence_verified') is True
        for p in primary for e in support)
    score = None if not executed else 3 if primary and independent else 2 if primary else 1 if support else 0
    return dict(rule_version=VERSION, score=score, refuted=refuted if executed else False,
                conflict=bool(support and refuted) if executed else False,
                primary_supported=bool(executed and primary),
                label='未执行核验' if score is None else ['无适用支持依据','有可定位报道或自述','有适用原始材料','原始材料及独立交叉支持'][score])


def impact_score(dimensions, *, supported_dimensions, comparison_located=False):
    """Each dimension needs checked evidence, not just a model-supplied enum."""
    weights = {'materiality': {'minor': 0, 'substantive': 2},
               'scope': {'limited': 0, 'broad': 1},
               'urgency': {'long_term': 0, 'near_term': 1}}
    missing = [k for k, values in weights.items()
               if dimensions.get(k) not in values or k not in supported_dimensions]
    points = {k: values[dimensions[k]] for k, values in weights.items() if k not in missing}
    score = None if missing else sum(points.values())
    if score is not None and score >= 3 and not comparison_located:
        missing.append('comparison_basis')
        score = None
    level = 'unknown' if score is None else 'high' if score >= 3 else 'medium' if score == 2 else 'low'
    return dict(rule_version=VERSION, score=score, level=level, components=points, missing=missing)


def reminder(impact, claims):
    """All bound premises must have primary support; conflicts take precedence."""
    if impact['level'] not in ('medium', 'high'):
        return dict(rule_version=VERSION, kind=None, reason='影响低或待定，不自动提醒')
    if claims and all(c.get('primary_supported') and not c.get('refuted') and not c.get('conflict') for c in claims):
        return dict(rule_version=VERSION, kind='event_notice', reason='中高影响且所有绑定前提有适用原始材料支持')
    return dict(rule_version=VERSION, kind='check_notice', reason='中高影响但前提有冲突、反驳或原始依据缺口')
