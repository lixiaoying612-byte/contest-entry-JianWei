"""Finite reversible transport adaptations; original model bytes remain immutable."""
import copy
import json
import re
from event_workbench import event_relations as er

VERSION='eng012-compat-r1'
MARKER='[ENG012-R1 structured uncertainty JSON] '


def json_ordered(value):return json.dumps(value,ensure_ascii=False,separators=(',',':'))


def restore(view):
    value=copy.deepcopy(view['adapted_compact'])
    for change in reversed(view['changes']):
        parent=value
        for slot in change['slots'][:-1]:parent=parent[slot]
        parent[change['slots'][-1]]=er.common.strict_json(change['original_json'])
    return value


def derive(doc,original_bytes):
    result=er.common.strict_json(original_bytes.decode('utf-8'));raw=result['raw_final_answer']
    strict=er.compact.decode_a(raw,doc['document'],doc['document_id'])
    view=dict(version=VERSION,origin='program_adapter_view',document_id=doc['document_id'],document_sha=er.dossiers.fingerprint(doc['document']),source_result_sha256=er.dossiers.sha(original_bytes),
        original_model_content=raw,original_content_sha256=er.dossiers.sha(raw.encode('utf-8')),original_response_identity=result['identity'],original_interface_complete=result['response_complete'],
        original_strict_check=strict,changes=[],uncertainty_references=[],unresolved_fields=[],adapter_issues=[],semantic_review_status='semantic_review_pending',usable=False)
    try:original=er.common.strict_json(raw)
    except (ValueError,TypeError):
        view['adapter_issues'].append('strict_json_failed');view['decoded']=strict;return view
    candidate=copy.deepcopy(original);rep,positions=er.compact.source_representation(doc['document']);units=dict(rep['u']);position_by={p['ref']:p for p in positions}
    def change(slots,old,new,rule,lossless):
        node=candidate
        for slot in slots[:-1]:node=node[slot]
        node[slots[-1]]=new
        view['changes'].append(dict(slots=slots,path='$'+''.join(f'[{s}]' if isinstance(s,int) else '.'+s for s in slots),old=old,new=new,original_json=json_ordered(old),original_structure_sha256=er.dossiers.sha(json_ordered(old).encode()),rule=rule,rule_version=VERSION,structurally_reversible=True,semantic_lossless=lossless))
    def uncertainty_refs(value,slots):
        if isinstance(value,dict):
            for k,v in value.items():uncertainty_refs(v,slots+[k])
        elif isinstance(value,list):
            for i,v in enumerate(value):uncertainty_refs(v,slots+[i])
        elif isinstance(value,str):
            for ref in re.findall(r'(?<![A-Za-z0-9])[TB][1-9][0-9]*(?![A-Za-z0-9])',value):
                item=dict(slots=slots,ref=ref,status='located' if ref in units else 'unresolved',position=position_by.get(ref),quote=units.get(ref))
                view['uncertainty_references'].append(item)
                if ref not in units:view['adapter_issues'].append(dict(code='unknown_uncertainty_reference',slots=slots,ref=ref))
    events=candidate.get('e') if isinstance(candidate,dict) else None
    if isinstance(events,list):
        for i,event in enumerate(events):
            if not isinstance(event,dict):continue
            impacts=event.get('im',[])
            if isinstance(impacts,list):
                for j,impact in enumerate(impacts):
                    if isinstance(impact,list) and len(impact)==5 and isinstance(impact[4],list):
                        old=copy.deepcopy(impact[4]);slots=['e',i,'im',j,4]
                        if len(old)==1 and isinstance(old[0],str) and old[0] in units:change(slots,old,old[0],'single_valid_impact_reference',True)
                        else:view['adapter_issues'].append(dict(code='impact_reference_not_losslessly_unwrappable',slots=slots,original=old))
            us=event.get('u',[])
            if isinstance(us,list):
                for j,value in enumerate(us):
                    if isinstance(value,(list,dict)):
                        slots=['e',i,'u',j];uncertainty_refs(value,slots);change(slots,copy.deepcopy(value),MARKER+json_ordered(value),'structured_uncertainty_preserved',True)
                    elif not isinstance(value,str):view['adapter_issues'].append(dict(code='unexpected_uncertainty_type',slots=['e',i,'u',j]))
            st=event.get('st')
            if isinstance(st,list) and len(st)>1 and isinstance(st[1],str) and st[1] not in ('+','-','?'):
                slots=['e',i,'st',1];old=st[1]
                view['unresolved_fields'].append(dict(code='invalid_polarity',slots=slots,original_token=old,derived_value='unclear',resolution='unresolved_no_semantic_inference'))
                change(slots,old,'?','invalid_polarity_unresolved_downgrade',False)
    view['adapted_compact']=candidate;view['decoded']=er.compact.decode_a(json_ordered(candidate),doc['document'],doc['document_id'])
    view['replay_equal']=restore(view)==original
    decoded=view['decoded'];view['usable']=bool(view['original_interface_complete'] and view['replay_equal'] and not view['adapter_issues'] and not decoded['issues'] and decoded.get('projection') is not None and decoded['projection']['events'])
    view['adaptation_status']='usable_semantics_pending' if view['usable'] else 'blocked'
    view['identity']=er.dossiers.fingerprint(view)
    return view


