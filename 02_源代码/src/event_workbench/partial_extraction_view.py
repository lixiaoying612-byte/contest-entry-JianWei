"""Read-only, event-local projection with original indices and separate matching."""
import copy
import hashlib
import json
import re
from event_workbench import compact_model_io as compact

VERSION='eng014-r1-partial-view'
common=compact.common


def sha(data):return hashlib.sha256(data).hexdigest()


def remap(value,index):
    if isinstance(value,list):return [remap(x,index) for x in value]
    if isinstance(value,dict):return {k:re.sub(r'\$\.(e|events)\[0\]',lambda m:'$.'+m[1]+f'[{index}]',v) if k in ('field','path') and isinstance(v,str) else remap(v,index) for k,v in value.items()}
    return value


def event_view(event,index,doc,lineage):
    raw=common.json_text(dict(e=[event]));decoded=compact.decode_a(raw,doc['document'],doc['local_id'])
    record=dict(original_index=index,original_event=copy.deepcopy(event),lineage=lineage,confirmed=False,release_status='unreviewed',projection=None,check_issues=remap(decoded['issues'],index),field_warnings=[],binding=None)
    if decoded['issues'] or decoded.get('projection') is None:
        record.update(status='quarantined',source_check_status='not_passed_projection_unavailable');return record
    record['projection']=decoded['projection']['events'][0]
    check=decoded['checks']
    record['checks']=remap({k:v for k,v in check.items() if k not in ('parsed_response','raw_response')},index)
    record['field_warnings']=remap(check.get('evidence_issues',[]),index)
    record['binding']=dict(decoded['binding_map'][0],event_index=index)
    record.update(status='projected',source_check_status='warnings_not_passed' if record['field_warnings'] else 'locations_checked_semantics_unverified')
    return record


def matching(raw,events,doc,blocked=False):
    if blocked:return dict(status='extraction_blocked',original=raw,proposals=[],confirmed=False)
    schema=compact.v2.read_json(compact.CONTRACT/'b_wire.schema.json')
    structural=compact.structural_errors(raw,schema)
    valid={e['original_index']:e for e in events if e['status']=='projected'}
    isolated={e['original_index'] for e in events if e['status']=='quarantined'}
    content=dict(doc['representation'],c=doc['candidates'],a=[dict(i=i,x=e['field_warnings']) for i,e in valid.items()])
    checked=compact.decode_b(common.json_text(raw),content)
    units=dict(doc['representation']['u']);candidates={c['definition_id'] for c in doc['candidates']};proposals=[]
    rows=raw.get('m',[]) if isinstance(raw,dict) else []
    for row_index,row in enumerate(rows if isinstance(rows,list) else []):
        issues=compact.structural_errors(row,schema['properties']['m']['items'],f'$.b.m[{row_index}]')
        ref=row[0] if isinstance(row,list) and row else None
        ref_status='projected' if type(ref) is int and ref in valid else 'quarantined' if type(ref) is int and ref in isolated else 'unknown'
        candidate=row[1] if isinstance(row,list) and len(row)>1 else None
        refs=row[4] if isinstance(row,list) and len(row)>4 else None
        source_valid=isinstance(refs,list) and all(isinstance(x,str) and x in units for x in refs)
        proposals.append(dict(original_row_index=row_index,original_row=copy.deepcopy(row),original_event_index=ref,event_reference_status=ref_status,candidate_reference_valid=isinstance(candidate,str) and candidate in candidates,source_references_valid=source_valid,protocol_issues=issues,structurally_linkable=not structural and not checked['issues'] and ref_status=='projected',confirmed=False,release_status='unreviewed'))
    status='matching_protocol_failed' if structural else 'matching_reference_failed' if checked['issues'] else 'empty_matches' if not rows else 'candidates_unreviewed'
    return dict(status=status,original=raw,protocol_issues=structural,reference_check=checked,proposals=proposals,confirmed=False)


def derive(result,doc,lineage):
    view=dict(version=VERSION,document=doc['local_id'],stage=result['stage'],lineage=lineage,original_strict_result=copy.deepcopy(result.get('decoded')),document_status='document_blocked',blocking_reasons=[],original_count=None,projected=[],quarantined=[],confirmed=False,release_status='unreviewed',matching=dict(status='extraction_blocked' if result['stage']=='J' else 'not_run',proposals=[],confirmed=False))
    if not result.get('response_complete') or result.get('finish_reason')!='stop':view['blocking_reasons']=['response_incomplete_or_truncated'];return view
    raw=result.get('raw_final_answer')
    if not isinstance(raw,str) or not raw.strip():view['blocking_reasons']=['empty_content'];return view
    try:value=common.strict_json(raw)
    except (ValueError,TypeError):view['blocking_reasons']=['strict_json_failed_including_duplicate_keys'];return view
    if result['stage']=='J':
        if not isinstance(value,dict) or set(value)!={'a','b'} or not isinstance(value['a'],dict):view['blocking_reasons']=['unrecognized_joint_envelope'];return view
        a=value['a'];b=value['b'];view['matching']['original']=copy.deepcopy(b)
    elif result['stage']=='A':a=value;b=None
    else:view['blocking_reasons']=['unsupported_stage'];return view
    if not isinstance(a,dict) or set(a)-{'e','n'} or not isinstance(a.get('e'),list):view['blocking_reasons']=['unrecognized_extraction_envelope'];return view
    view['original_count']=len(a['e']);view['original_document_notes']=copy.deepcopy(a.get('n',[]));view['document_notes_present']='n' in a
    note_schema=compact.v2.read_json(compact.CONTRACT/'a_wire.schema.json')['properties']['n']
    view['document_note_issues']=compact.structural_errors(a.get('n',[]),note_schema,'$.n')
    events=[event_view(event,i,doc,dict(lineage,original_event_index=i)) for i,event in enumerate(a['e'])]
    view['projected']=[e for e in events if e['status']=='projected'];view['quarantined']=[e for e in events if e['status']=='quarantined']
    if not events:status='empty_extraction'
    elif not view['projected']:status='all_events_quarantined'
    elif view['quarantined']:status='partial_projection'
    elif any(e['field_warnings'] for e in events):status='projected_with_source_warnings'
    else:status='projected_semantics_unreviewed'
    view['document_status']=status;view['projected_count']=len(view['projected']);view['quarantined_count']=len(view['quarantined'])
    if result['stage']=='J':view['matching']=matching(b,events,doc)
    return view


def preview(view,doc):
    return dict(document=view['document'],stage=view['stage'],document_status=view['document_status'],source_document=doc['document'],source_units=doc['representation'],source_positions=doc['source_positions'],events=[dict(original_index=e['original_index'],facts=e['projection'],source_check_status=e['source_check_status'],field_warnings=e['field_warnings'],lineage=e['lineage'],confirmed=False,release_status='unreviewed') for e in view['projected']],quarantined=view['quarantined'],document_notes=view.get('original_document_notes'),document_note_issues=view.get('document_note_issues',[]),matching=view['matching'],B_request_created=False,confirmed=False,release_status='unreviewed')


