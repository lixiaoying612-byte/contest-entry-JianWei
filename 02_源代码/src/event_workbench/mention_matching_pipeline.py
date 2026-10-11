"""ENG008: immutable document mentions, separate monitoring-match candidates."""
import copy
import json
import math
from pathlib import Path

from event_workbench import deepseek_extraction as common
from event_workbench import financial_event_extraction as v2

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT/'engineering/contracts/eng008'
VERSION = 'eng008-v1'


def schema(stage, direction='output'):
    return v2.read_json(CONTRACT/f'{stage.lower()}_{direction}.schema.json')


def report_base(raw, stage):
    report = dict(parsed_response=None,schema_status='failed',evidence_status='not_checked',
        semantic_review_status='pending',schema_issues=[],evidence_issues=[],quote_locations=[],field_evidence=[],
        normalized_values=[],monitoring_matches=[])
    def finite(value):
        if isinstance(value,float) and not math.isfinite(value):raise ValueError('nonfinite')
        if isinstance(value,dict):
            for child in value.values():finite(child)
        if isinstance(value,list):
            for child in value:finite(child)
    try:
        parsed=common.strict_json(raw)
        finite(parsed)
    except (ValueError,TypeError,RecursionError):
        report['schema_issues']=[common.issue('$','invalid_json','原答不是完整严格JSON；不修补。')]
        return report
    report['parsed_response']=parsed
    report['schema_issues']=v2.schema_errors(parsed,schema(stage))
    report['schema_status']='issues' if report['schema_issues'] else 'passed'
    return report


def issue(report, path, code):
    report['evidence_issues'].append(common.issue(path,code,'选定证据未通过；不当作已确认事实。'))


def locate(report, document, q, path):
    text=document.get('title' if q['section']=='title' else 'text') or ''
    loc=dict(field=path,section=q['section'],**common.locate_quote(text,q['quote']))
    report['quote_locations'].append(loc)
    if not loc['candidates']:issue(report,path,'missing_quote')
    return loc


def refs_check(report, refs, located, path):
    good=bool(refs) and all(type(i) is int and i in located and located[i]['candidates'] for i in refs)
    if not good:issue(report,path,'invalid_evidence_ref')
    return good


def finish_report(report):
    report['evidence_status']='issues' if report['evidence_issues'] else 'passed'
    return report


def inspect_a(raw, document):
    report=report_base(raw,'A')
    if report['schema_status']!='passed':return report
    for i,event in enumerate(report['parsed_response']['events']):
        ep=f'$.events[{i}]'
        located={j:locate(report,document,q,f'{ep}.evidence_quotes[{j}]') for j,q in enumerate(event['evidence_quotes'])}
        refs_check(report,event['status']['evidence_refs'],located,ep+'.status.evidence_refs')
        report['field_evidence'].append(dict(field=ep+'.status',evidence_refs=event['status']['evidence_refs'],semantic_support_verified=False))
        for group,key in [('participants','name'),('times','text'),('details','value_text')]:
            for j,value in enumerate(event[group]):
                path=f'{ep}.{group}[{j}]';refs=value['evidence_refs'];supports=[]
                valid=refs_check(report,refs,located,path+'.evidence_refs')
                for ref in refs:
                    if ref not in located or not located[ref]['candidates']:continue
                    loc=located[ref]
                    positions=common.locate_quote(event['evidence_quotes'][ref]['quote'],value[key])['candidates']
                    if positions:
                        supports.append(dict(evidence_ref=ref,positions=[dict(section=loc['section'],start=a['start']+b['start'],end=a['start']+b['end']) for a in loc['candidates'] for b in positions]))
                if not supports:issue(report,path+'.'+key,'outside_selected_evidence')
                report['field_evidence'].append(dict(field=path+'.'+key,evidence_refs=refs,supports=supports,
                    status='located' if valid and supports else 'unsupported',semantic_support_verified=False))
                if group=='times':report['normalized_values'].append(dict(field=path+'.normalized',**v2.explicit_date(value,bool(supports) and valid)))
        for j,impact in enumerate(event['impact_mentions']):locate(report,document,impact['quote'],f'{ep}.impact_mentions[{j}].quote')
    return finish_report(report)


def inspect_b(raw, document, a_result, definitions):
    report=report_base(raw,'B')
    if report['schema_status']!='passed':return report
    events=a_result['parsed_response']['events'];results=report['parsed_response']['results']
    indices=[x['mention_ref'] for x in results]
    if sorted(indices)!=list(range(len(events))):
        report['schema_issues'].append(common.issue('$.results','mention_coverage','每条提及必须恰好返回一次。'))
        report['schema_status']='issues';return report
    by_id={d['definition_id']:d for d in definitions}
    for i,row in enumerate(results):
        ep=f'$.results[{i}]';event=events[row['mention_ref']]
        if (not row['matches'] and not row['unmatched_reason']) or (row['matches'] and row['unmatched_reason'] is not None):
            issue(report,ep+'.unmatched_reason','unmatched_reason_required_or_conflicting')
        located={j:locate(report,document,q,f'{ep}.source_quotes[{j}]') for j,q in enumerate(event['evidence_quotes'])}
        seen=set()
        for j,match in enumerate(row['matches']):
            path=f'{ep}.matches[{j}]';before=len(report['evidence_issues'])
            refs=match['evidence_refs'];valid=refs_check(report,refs,located,path+'.evidence_refs')
            d=by_id.get(match['definition_id'])
            if d is None:issue(report,path+'.definition_id','unknown_definition');continue
            if d['definition_id'] in seen:issue(report,path,'duplicate_definition')
            seen.add(d['definition_id'])
            if match['basis']=='direct_text':
                tokens=[d.get('target_name'),d.get('target_code')]
                if not valid or not any(token and token in event['evidence_quotes'][ref]['quote'] for token in tokens for ref in refs if ref in located and located[ref]['candidates']):
                    issue(report,path,'target_not_in_selected_evidence')
            elif not common.locate_quote(d['description'],match['definition_quote'])['candidates']:
                issue(report,path+'.definition_quote','missing_definition_quote')
            report['monitoring_matches'].append(dict(mention_ref=row['mention_ref'],
                mention_id=v2.digest([a_result.get('original_document_id'),a_result['identity'],row['mention_ref']]),**copy.deepcopy(match),
                definition_metadata=copy.deepcopy(d),
                evidence_status='located' if len(report['evidence_issues'])==before else 'issues',
                a_schema_status=a_result['schema_status'],a_evidence_status=a_result['evidence_status'],
                semantic_review_status='pending',confirmed=False))
    return finish_report(report)


def immutable_json(path, value):
    path=Path(path)
    if path.exists():
        if v2.read_json(path)!=value:raise ValueError('immutable_artifact_conflict')
    else:common.atomic_json(path,value)
