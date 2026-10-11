"""Offline compatible projections with generation identity independent of checks."""
import copy
from event_workbench import document_pipeline as base
from event_workbench import extraction_compatibility as compat
from event_workbench import partial_extraction_view as partial

VERSION='eng019-compatible-r1'
read=base.read;save=base.save;digest=base.digest


def compatible_view(result,doc,lineage):
    original=partial.derive(result,doc,lineage)
    view=copy.deepcopy(original);view['version']=VERSION;view['original_strict_view']=original
    view['compatibility_version']=compat.VERSION;view['compatibility_changes']=0
    if original['document_status']=='document_blocked':return view,partial.preview(view,doc)
    raw=partial.common.strict_json(result['raw_final_answer']);a=raw['a'];events=[]
    for index,event in enumerate(a['e']):
        wrapper=dict(raw_final_answer=partial.common.json_text(dict(e=[event])),identity=digest([lineage,index,event]),response_complete=True)
        adapted=compat.derive(dict(document_id=doc['local_id'],document=doc['document']),partial.common.json_text(wrapper).encode('utf-8'))
        if not adapted.get('replay_equal') or compat.restore(adapted)!=dict(e=[event]):raise ValueError('compatibility_restore_mismatch')
        derived=adapted['adapted_compact']['e'][0]
        row=partial.event_view(derived,index,doc,dict(lineage,original_event_index=index))
        row['original_event']=copy.deepcopy(event);row['derived_event']=copy.deepcopy(derived)
        row['original_strict_check']=partial.event_view(event,index,doc,lineage)
        row['compatibility']=dict(version=compat.VERSION,changes=adapted['changes'],adapter_issues=adapted['adapter_issues'],unresolved_fields=adapted['unresolved_fields'],uncertainty_references=adapted['uncertainty_references'],restore_equal=True,local_index=0,original_index=index,source='existing_rules_no_semantic_repair')
        if adapted['adapter_issues']:
            row.update(status='quarantined',projection=None,source_check_status='compatibility_boundary_rejected')
        view['compatibility_changes']+=len(adapted['changes']);events.append(row)
    view['projected']=[e for e in events if e['status']=='projected'];view['quarantined']=[e for e in events if e['status']=='quarantined']
    view['projected_count']=len(view['projected']);view['quarantined_count']=len(view['quarantined'])
    view['document_status']='empty_extraction' if not events else 'all_events_quarantined' if not view['projected'] else 'partial_projection' if view['quarantined'] else 'projected_with_source_warnings' if any(e['field_warnings'] for e in events) else 'projected_semantics_unreviewed'
    view['matching']=partial.matching(raw['b'],events,doc)
    preview=partial.preview(view,doc)
    preview['compatibility_summary']=dict(version=compat.VERSION,changes=view['compatibility_changes'],restore_equal=all(e['compatibility']['restore_equal'] for e in events),original_strict_status=original['document_status'],original_strict_projected=original.get('projected_count',0),original_strict_quarantined=original.get('quarantined_count',0))
    for event,row in zip(preview['events'],view['projected']):
        event.update(original_event=row['original_event'],derived_event=row['derived_event'],original_strict_check=row['original_strict_check'],compatibility=row['compatibility'])
    return view,preview

