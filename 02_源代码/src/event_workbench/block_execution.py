"""Parent-bound source units and event projection; legacy block execution is retired."""
import json
from event_workbench import unified_execution as atomic
from event_workbench import partial_extraction_view as partial

base=atomic.base
compact=partial.compact


def block_input(plan,block):
    """Each short unit is an exact parent slice, with one unambiguous role."""
    units=[];mapping={};roles=dict(core=[],context_only=[])
    spans=[dict(id='title',text=plan['document']['title'] or '',start=0,role='context_only',section='title')]
    spans += [dict(block['core'],section='body')]+[dict(s,section='body') for s in block['context']]
    numbers={'T':0,'B':0}
    for span in spans:
        document=dict(title=None,text=span['text'],source=None,publication_time=None)
        rep,positions=compact.source_representation(document)
        for (_,text),pos in zip(rep['u'],positions):
            prefix='T' if span['section']=='title' else 'B';numbers[prefix]+=1;ref=prefix+str(numbers[prefix])
            units.append([ref,text]);roles[span['role']].append(ref)
            mapping[ref]=dict(section=span['section'],quote=text,start=span['start']+pos['start'],end=span['start']+pos['end'],
                              document_id=plan['parent_document_id'],document_sha256=base.digest(plan['document']),
                              quote_sha256=partial.sha(text.encode()),role=span['role'],span=span['id'])
    return dict(meta=dict(p=plan['document']['publication_time'],s=plan['document']['source']),u=units,unit_roles=roles),mapping


def project_event(event,index,document,representation,mapping):
    """Compact wire translation only; field validity uses existing inspect_a."""
    _,issues=compact.parse_wire(json.dumps(dict(e=[event]),ensure_ascii=False),'A')
    if issues:return dict(original_index=index,original_event=event,status='quarantined',issues=issues)
    groups=[event['q'],event['st'][5]]+[x[-1] for g in ('p','t','d') for x in event.get(g,[])]+[[x[4]] for x in event.get('im',[])]
    selected={ref for refs in groups for ref in refs}
    if selected-set(mapping):return dict(original_index=index,original_event=event,status='quarantined',issues=['unknown_block_unit'])
    if not any(mapping[r]['role']=='core' for r in event['q']):
        return dict(original_index=index,original_event=event,status='quarantined',issues=['context_only_no_core_summary_evidence'])
    ordered=[r for r,_ in representation['u'] if r in selected];lookup={r:i for i,r in enumerate(ordered)}
    refs=lambda values:[lookup[r] for r in values]
    quote=lambda ref:dict(section=mapping[ref]['section'],quote=mapping[ref]['quote'])
    st=event['st']
    facts=dict(summary=event['s'],participants=[dict(name=x[0],role=x[1],evidence_refs=refs(x[2])) for x in event.get('p',[])],
               status=dict(phase=st[0],polarity=compact.POL[st[1]],mode=compact.MODE[st[2]],attributed_to=st[3],context=compact.CONTEXT[st[4]],evidence_refs=refs(st[5])),
               times=[dict(role=compact.TIME_ROLE[x[0]],text=x[1],normalized=x[2],precision=compact.PRECISION[x[3]],basis=compact.BASIS[x[4]],evidence_refs=refs(x[5])) for x in event.get('t',[])],
               details=[dict(name=x[0],value_text=x[1],evidence_refs=refs(x[2])) for x in event.get('d',[])],matches=[],
               impact_mentions=[dict(target_name=x[0],direction=compact.DIRECTION[x[1]],mode=compact.MODE[x[2]],attributed_to=x[3],quote=quote(x[4])) for x in event.get('im',[])],
               evidence_quotes=[quote(r) for r in ordered],uncertainties=event.get('u',[]))
    check=compact.checks.inspect_a(json.dumps(dict(events=[facts],document_notes=[]),ensure_ascii=False),document)
    if check['schema_status']!='passed':
        return dict(original_index=index,original_event=event,status='quarantined',issues=check)
    proofs=[mapping[r] for r in ordered]
    for proof in proofs:
        text=document['title' if proof['section']=='title' else 'text'] or ''
        if text[proof['start']:proof['end']]!=proof['quote']:raise ValueError('parent_quote_mapping_changed')
    return dict(original_index=index,original_event=event,status='projected',projection=facts,
                field_warnings=check.get('evidence_issues',[]),parent_proofs=proofs,source_refs=ordered,checks=check)

