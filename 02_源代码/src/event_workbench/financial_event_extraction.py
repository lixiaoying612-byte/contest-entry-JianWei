"""ENG007 v2: document mentions, exact evidence and separate pending semantics.

The schema walker supports only keywords present in the frozen ENG007 contract.
It is not a general JSON Schema implementation. No network or credential reads here.
"""
import json
import math
import re
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path

from event_workbench import deepseek_extraction as common

VERSION = 'eng007-v2'
PROMPT_VERSION = 'eng007-p2.0'
LIMIT = 240000
CONFIG = dict(common.CONFIG, max_tokens=10000)
RATES = dict(version='DeepSeek official pricing checked 2026-09-07', currency='CNY', per_tokens=1000000,
             cache_hit='0.30', cache_miss='9.0', output='27.0', period='peak rates used as conservative upper bound',
             source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/')
ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'engineering/contracts/eng007_v2'


def digest(value):
    return common.sha256(common.json_text(value, sort_keys=True, separators=(',', ':'), allow_nan=False))


def read_json(path):
    return common.strict_json(Path(path).read_text(encoding='utf-8-sig'))


def schema_errors(value, schema, path='$'):
    """Contract-specific structural checks; preserve invalid candidates unchanged."""
    errors = []
    def bad(code):
        errors.append(common.issue(path, code, '不符合冻结的ENG007 v2接口。'))
    if isinstance(value, str) and any(0xD800 <= ord(c) <= 0xDFFF for c in value):
        errors.append(common.issue(path, 'invalid_unicode_scalar', '含孤立Unicode代理码点；保真转义留档，不当有效文本。'))
    expected = schema.get('type')
    types = {'object': dict, 'array': list, 'string': str, 'integer': int, 'null': type(None)}
    if expected and type(value) is not types[expected]:
        bad('type'); return errors
    if 'const' in schema and value != schema['const']: bad('const')
    if 'enum' in schema and value not in schema['enum']: bad('enum')
    if isinstance(value, str) and 'pattern' in schema and not re.search(schema['pattern'], value): bad('pattern')
    if type(value) is int and value < schema.get('minimum', value): bad('minimum')
    if isinstance(value, dict):
        properties = schema.get('properties', {})
        for key in schema.get('required', []):
            if key not in value: errors.append(common.issue(path+'.'+key, 'required', '缺少字段。'))
        if schema.get('additionalProperties') is False:
            for key in value.keys() - properties.keys():
                errors.append(common.issue(path+'.'+key, 'additional_property', '额外字段。'))
        for key in value.keys() & properties.keys():
            errors.extend(schema_errors(value[key], properties[key], path+'.'+key))
    if isinstance(value, list):
        if len(value) < schema.get('minItems', 0): bad('min_items')
        if schema.get('uniqueItems') and len({digest(x) for x in value}) != len(value): bad('duplicate_items')
        for i, item in enumerate(value):
            if 'items' in schema: errors.extend(schema_errors(item, schema['items'], f'{path}[{i}]'))
    if 'anyOf' in schema and all(schema_errors(value, s, path) for s in schema['anyOf']): bad('any_of')
    for s in schema.get('allOf', []): errors.extend(schema_errors(value, s, path))
    if 'if' in schema and not schema_errors(value, schema['if'], path):
        errors.extend(schema_errors(value, schema.get('then', {}), path))
    return errors


def excel_time(raw):
    """1900 date system; reject fictitious leap day; round half-up to seconds."""
    if raw is None or not str(raw).strip(): return None
    serial = Decimal(str(raw))
    if not serial.is_finite() or serial < 1 or 60 <= serial < 61:
        raise ValueError('invalid_excel_serial')
    seconds = int((serial * 86400).quantize(Decimal('1'), rounding=ROUND_HALF_UP))
    # Reject values whose rounding would land in the fictitious day too.
    if 60 * 86400 <= seconds < 61 * 86400: raise ValueError('invalid_excel_serial')
    adjusted = seconds - (86400 if seconds >= 61 * 86400 else 0)
    return (datetime(1899, 12, 31) + timedelta(seconds=adjusted)).isoformat()


def explicit_date(item, supported):
    result = dict(candidate=item.get('normalized'), normalized_value=None, status='pending')
    if not supported: result['status'] = 'unsupported'; return result
    if item.get('basis') != 'explicit': return result
    precision = item.get('precision')
    patterns = {'year': r'(\d{4})(?:年)?', 'month': r'(\d{4})(?:年|-)(\d{1,2})(?:月)?',
                'day': r'(\d{4})(?:年|-)(\d{1,2})(?:月|-)(\d{1,2})(?:日)?'}
    if precision not in patterns: result['status'] = 'unsupported'; return result
    match = re.fullmatch(patterns[precision], item.get('text', ''))
    if not match: result['status'] = 'unsupported'; return result
    parts = list(map(int, match.groups()))
    try: datetime(*(parts + [1] * (3-len(parts))))
    except ValueError: result['status'] = 'unsupported'; return result
    expected = '-'.join([f'{parts[0]:04d}'] + [f'{v:02d}' for v in parts[1:]])
    if item.get('normalized') == expected: result.update(status='passed', normalized_value=expected)
    elif item.get('normalized') is not None: result['status'] = 'unsupported'
    return result


def inspect_answer(raw, document, definitions):
    report = dict(parsed_response=None, schema_status='failed', evidence_status='not_checked',
                  semantic_review_status='pending', schema_issues=[], evidence_issues=[], field_evidence=[],
                  quote_locations=[], normalized_values=[], entity_links=[], semantic_review=[])
    def finite(value):
        if isinstance(value, float) and not math.isfinite(value): raise ValueError('nonfinite_number')
        if isinstance(value, dict):
            for item in value.values(): finite(item)
        if isinstance(value, list):
            for item in value: finite(item)
    try:
        parsed = common.strict_json(raw)
        finite(parsed)
    except (ValueError, TypeError, RecursionError):
        report['schema_issues'] = [common.issue('$', 'invalid_json', '非完整严格JSON；保留原答，不修补。')]; return report
    report['parsed_response'] = parsed
    schema = read_json(CONTRACT/'output.schema.json')
    report['schema_issues'] = schema_errors(parsed, schema)
    report['schema_status'] = 'issues' if report['schema_issues'] else 'passed'
    definitions_by_id = {d['definition_id']: d for d in definitions}
    events = parsed.get('events', []) if isinstance(parsed, dict) else []
    if not isinstance(events, list): events = []
    event_schema = schema['properties']['events']['items']['properties']
    def invalid(path):
        return any(e['field'] == path or e['field'].startswith(path+'.') or e['field'].startswith(path+'[') for e in report['schema_issues'])
    def error(path, code): report['evidence_issues'].append(common.issue(path, code, '本地引用/候选关系未通过；不派生为有效结果。'))
    def locate(q, path):
        if not isinstance(q, dict) or schema_errors(q, event_schema['evidence_quotes']['items']): return None
        section = q['section']; text = document.get('title' if section == 'title' else 'text') or ''
        loc = dict(field=path, section=section, **common.locate_quote(text, q['quote']))
        report['quote_locations'].append(loc)
        if not loc['candidates']: error(path, 'missing_quote')
        return loc
    for i, event in enumerate(events):
        ep = f'$.events[{i}]'
        if not isinstance(event, dict): continue
        quotes = event.get('evidence_quotes', [])
        if not isinstance(quotes, list): quotes = []
        located = {j: locate(q, f'{ep}.evidence_quotes[{j}]') for j, q in enumerate(quotes)}
        good = {j: q for j, q in located.items() if q and q['candidates']}
        def semantic(path, refs=None, extra=None):
            refs = list(good) if refs is None else refs
            report['semantic_review'].append(dict(field=path, evidence_refs=refs, status='pending', **(extra or {})))
        semantic(ep+'.summary'); semantic(ep+'.status')
        for group, key in [('participants', 'name'), ('times', 'text'), ('details', 'value_text')]:
            values = event.get(group, [])
            if not isinstance(values, list): continue
            for j, value in enumerate(values):
                vp = f'{ep}.{group}[{j}]'; fp = vp+'.'+key
                if not isinstance(value, dict) or not isinstance(value.get(key), str) or not value[key].strip(): continue
                supports = []
                for ref, q in good.items():
                    relative = common.locate_quote(q['quote'], value[key])['candidates']
                    if relative:
                        positions = [dict(section=q['section'], start=base['start']+p['start'], end=base['start']+p['end']) for base in q['candidates'] for p in relative]
                        supports.append(dict(evidence_ref=ref, positions=positions))
                report['field_evidence'].append(dict(field=fp, supports=supports, status='located' if supports else 'unsupported', semantic_support_verified=False))
                if not supports: error(fp, 'outside_event_evidence')
                semantic(vp)
                if group == 'times':
                    date_check = explicit_date(value, bool(supports) and not invalid(vp))
                    report['normalized_values'].append(dict(field=vp+'.normalized', **date_check))
        matches = event.get('matches', [])
        if not isinstance(matches, list): matches = []
        for j, value in enumerate(matches):
            mp = f'{ep}.matches[{j}]'
            if not isinstance(value, dict) or invalid(mp): continue
            definition = definitions_by_id.get(value['definition_id'])
            refs = value['evidence_refs']
            valid_refs = all(type(ref) is int and ref in good for ref in refs)
            if not valid_refs: error(mp+'.evidence_refs', 'invalid_evidence_ref')
            if definition is None: error(mp+'.definition_id', 'unknown_definition'); continue
            bridge = None
            if value['basis'] == 'definition_mediated':
                bridge = common.locate_quote(definition['description'], value['definition_quote'])
                if not bridge['candidates']: error(mp+'.definition_quote', 'missing_definition_quote')
            semantic(mp+'.reason', refs, dict(definition_id=definition['definition_id'], definition_evidence=bridge))
            if valid_refs and (bridge is None or bridge['candidates']):
                report['entity_links'].append(dict(field=mp, definition_id=definition['definition_id'],
                    target_type=definition['target_type'], target_code=definition['target_code'], target_name=definition['target_name'],
                    decision=value['decision'], evidence_status='located', semantic_review_status='pending',
                    meaning='monitoring_relation_candidate_not_participant_or_confirmed_impact'))
        impacts = event.get('impact_mentions', [])
        if not isinstance(impacts, list): impacts = []
        for j, value in enumerate(impacts):
            ip = f'{ep}.impact_mentions[{j}]'
            if not isinstance(value, dict): continue
            loc = locate(value.get('quote'), ip+'.quote')
            semantic(ip, extra=dict(impact_quote_location=loc))
    report['evidence_status'] = 'issues' if report['evidence_issues'] else 'passed' if report['schema_status'] == 'passed' else 'partial'
    return report
