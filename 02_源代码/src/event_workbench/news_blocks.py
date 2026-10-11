"""Pure block adapters for the existing news processor; no worker, storage or transport."""
import copy
import json

from event_workbench import block_execution as blocks
from event_workbench import development_request as requests

VERSION = 'news-parent-blocks-v1'
ADMISSION_VERSION = 'news-item-admission-v1'


def admit_items(raw, document, content, mapping=None):
    """Recheck untouched A items. A readable projection or a broken old B grants nothing."""
    joint = requests.compact.common.strict_json(raw)
    if not isinstance(joint, dict) or set(joint) != {'a', 'b'} or not isinstance(joint['a'], dict) or not isinstance(joint['b'], dict):
        raise ValueError('joint_root')
    a = joint['a']
    if set(a) - {'events', 'document_notes'} or not isinstance(a.get('events'), list):
        raise ValueError('named_A_root')
    notes = requests.named_event_wire.named_to_compact(dict(events=[], document_notes=a.get('document_notes', [])))
    if mapping is None:
        rep, positions = requests.compact.source_representation(document)
        if content['u'] != rep['u'] or content['meta'] != rep['meta']:
            raise ValueError('full_document_source_binding_changed')
        expected_roles=dict(core=[r for r,_ in rep['u'] if r.startswith('B')], context_only=[r for r,_ in rep['u'] if r.startswith('T')])
        if content['unit_roles']!=expected_roles:raise ValueError('full_document_core_roles_changed')
    items = []; accepted = []
    for index, event in enumerate(a['events']):
        item = dict(original_index=index, original_named_event=copy.deepcopy(event), accepted=False, confirmed=False)
        try:
            wire = requests.named_event_wire.named_to_compact(dict(events=[event]))['e'][0]
            if mapping is None:
                decoded = requests.compact.decode_a(json.dumps(dict(e=[wire]), ensure_ascii=False), document)
                item.update(checks=decoded, projection=(decoded.get('projection') or {}).get('events', [None])[0])
                valid = bool(decoded.get('ready_for_review'))
            else:
                projected = blocks.project_event(wire, index, document, content, mapping)
                item.update(projected)
                check = projected.get('checks', {})
                valid = projected.get('status') == 'projected' and check.get('schema_status') == 'passed' and check.get('evidence_status') == 'passed' and not projected.get('field_warnings')
            core_ok = bool(set(content['unit_roles']['core']).intersection(wire['q']))
            item.update(accepted=bool(valid and core_ok), core_supported=core_ok)
            if item['accepted']:accepted.append(wire)
        except (ValueError, TypeError, KeyError, IndexError) as error:
            item['error'] = str(error)
        item['status'] = 'admitted_for_scope_check' if item['accepted'] else 'quarantined'
        items.append(item)
    indices = [item['original_index'] for item in items if item['accepted']]
    return dict(version=ADMISSION_VERSION, root_valid=True, usable=bool(indices) or not items,
                items=items, accepted_original_indices=indices, isolated_original_indices=[i['original_index'] for i in items if not i['accepted']],
                strict_a=dict(ready_for_review=bool(indices) or not items, compact_parsed=dict(e=accepted, n=notes.get('n', [])), semantic_review_status='pending'),
                original_b=copy.deepcopy(joint['b']), old_b_inherited=False, confirmed=False)
