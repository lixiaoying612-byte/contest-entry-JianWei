"""Current news input preparation and historical result decoding; no request execution."""
import hashlib
import json
from pathlib import Path
from event_workbench import compact_model_io as compact
from event_workbench.joint_extraction_trial import wire_bytes
from event_workbench import named_event_wire,monitoring_consistency

ROOT=Path(__file__).resolve().parents[2]
MAX_BYTES=48000
def wire(v):return wire_bytes(v)
def sha(b):return hashlib.sha256(b).hexdigest()
def immutable(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('xb') as f:f.write(data)
def json_save(path,v):immutable(path,json.dumps(v,ensure_ascii=False,indent=2).encode('utf-8'))

def extraction_request(*args, **kwargs):
    raise ValueError('legacy_request_execution_removed_use_current_news')


def plan_requests(plan, candidates, *, content_only=True):
    """Prepare one complete, unsent input with the selected definitions."""
    if not content_only:raise ValueError('legacy_request_execution_removed_use_current_news')
    from event_workbench.document_processing_plan import audit_plan
    from event_workbench.development_lexical_candidates import load_definitions, OFFICIAL_SHA
    audit_plan(plan)
    if candidates['plan_id']!=plan['id'] or candidates['catalog_sha256']!=OFFICIAL_SHA:
        raise ValueError('candidate_plan_or_catalog_changed')
    catalog=load_definitions();by={d['definition_id']:d for d in catalog}
    indices=list(range(len(candidates['windows'])))
    priority=[c['definition']['definition_id'] for c in candidates['candidates'] if any(m['window_index'] in indices for m in c['matches'])]
    weak={r['definition_id'] for w in candidates['windows'] for r in w['weak_matches']}
    decisions=[dict(definition_id=d['definition_id'],included=d['definition_id'] in priority,
                    reason='priority_hint' if d['definition_id'] in priority else
                    'weak_slot_limit_pending_review' if d['definition_id'] in weak else 'not_selected_by_window_candidates_pending_review') for d in catalog]
    rep,mapping=compact.source_representation(plan['document'])
    if compact.reconstruct(rep)!=plan['document']:raise ValueError('source_roundtrip_failed')
    rep=dict(rep,unit_roles={'core':[i for i,_ in rep['u'] if not i.startswith('T')],
                             'context_only':[i for i,_ in rep['u'] if i.startswith('T')]})
    record=dict(block_id=None,window_indices=indices,core=[0,len(plan['document']['text'])],
                content=dict(rep,c=[by[did] for did in priority]),source_mapping=mapping,definition_decisions=decisions,
                candidate_status='no_definitions_pending_review' if not priority else 'object_and_definition_scope_unverified',
                weak_policy='no weak-only definitions in current news input',
                source_truncated=False,send_authorized=False,model_calls=0)
    return dict(status='content_prepared',plan_id=plan['id'],catalog_sha256=OFFICIAL_SHA,requests=[record],send_authorized=False,model_calls=0)




def decode_named_joint(raw,document,content,complete=True):
    """Offline synthetic-compatible handoff: named A -> frozen A -> shared-c B."""
    result=dict(a=None,b=None,issues=[],usable=False,semantic_review_status='pending')
    try:
        joint=compact.common.strict_json(raw)
        if type(joint)!=dict or set(joint)!={'a','b'}:raise ValueError('invalid_joint_envelope')
        a=named_event_wire.named_to_compact(joint['a'])
        result['a']=compact.decode_a(compact.common.json_text(a),document)
        if not complete or not result['a'].get('ready_for_review'):raise ValueError('a_not_ready')
        core=set(content['unit_roles']['core'])
        if any(not core.intersection(e['q']) for e in a['e']):raise ValueError('event_missing_core_evidence')
        mentions=[dict(e,i=i,x=[]) for i,e in enumerate(a['e'])]
        result['b']=monitoring_consistency.check(compact.common.json_text(joint['b']),dict(u=content['u'],a=mentions,c=content['c']),candidate_scope='shared_c')
        result['usable']=result['b']['b_usable']
    except (ValueError,TypeError,KeyError) as exc:result['issues'].append(str(exc))
    return result
