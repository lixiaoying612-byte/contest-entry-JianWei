"""Mechanical reference-index normalization, without inventing event content.

Only an absent aggregate evidence_refs is reconstructed from references already
present in checked core matches and returned fields. Explicit invalid values are
never replaced. The normal rich-field checker still validates every reference.
"""
import copy,json
from event_workbench import news_mvp
VERSION='competition-enrichment-aggregate-refs-v1'


def admit_baseline(raw,document,content,mapping=None,*,optional_catalogue=False):
    """Retain MVP recognition; derive only the missing aggregate reference index."""
    checked=news_mvp.admit(raw,document,content,mapping,optional_catalogue=optional_catalogue)
    value=news_mvp.requests.compact.common.strict_json(raw)
    envelope=dict(events=[dict(index=i,elements=e.get('elements')) for i,e in enumerate(value['events']) if isinstance(e,dict)])
    normalized,changes=normalize(json.dumps(envelope,ensure_ascii=False),checked)
    if changes:
        derived=json.loads(normalized)
        for change in changes:
            i=change['index']
            value['events'][i]['elements']=next(r['elements'] for r in derived['events'] if r['index']==i)
        checked=news_mvp.admit(json.dumps(value,ensure_ascii=False),document,content,mapping,optional_catalogue=optional_catalogue)
    checked.update(raw_answer=raw,projection_derivations=changes)
    return checked


def normalize(raw,part):
    value=news_mvp.requests.compact.common.strict_json(raw);changes=[]
    if not isinstance(value,dict) or not isinstance(value.get('events'),list):return raw,changes
    value=copy.deepcopy(value)
    known={i['original_index']:i for i in part.get('items',[]) if i.get('core')}
    def collect(node):
        if isinstance(node,dict):
            for k,v in node.items():
                if k=='evidence_refs' and isinstance(v,list):
                    for ref in v:
                        if isinstance(ref,str):yield ref
                else:yield from collect(v)
        elif isinstance(node,list):
            for v in node:yield from collect(v)
    for row in value['events']:
        if not isinstance(row,dict) or type(row.get('index')) is not int or row['index'] not in known:continue
        elements=row.get('elements')
        if not isinstance(elements,dict) or 'evidence_refs' in elements:continue
        core=known[row['index']]
        refs=list(dict.fromkeys([ref for m in core['original']['matches'] for ref in m['source_refs']]+list(collect(elements))))
        elements['evidence_refs']=refs
        changes.append(dict(index=row['index'],field='elements.evidence_refs',derived_value=refs,
                            method='union_of_existing_core_and_field_refs',version=VERSION))
    return json.dumps(value,ensure_ascii=False),changes
