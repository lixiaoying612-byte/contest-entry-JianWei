"""Two-stage recognition: immutable checked core, independent enrichment/emotion.

Rich fields keep the existing source checks. Only rich-qualified events enter
mentions; all checked core results and object-scoped emotion outcomes survive.
"""
import copy
import json
from event_workbench import news_mvp as mvp

VERSION='news-competition-v1'
def emotions(raw,core,content):
    allowed={m['definition_id'] for m in core['original']['matches']}
    units=dict(content['u']);body=set(content['unit_roles']['core'])
    values=[]
    for identity in sorted(allowed):
        entries=[x for x in raw if isinstance(x,dict) and x.get('definition_id')==identity] if isinstance(raw,list) else []
        try:
            if len(entries)!=1:raise ValueError('emotion_missing_or_duplicate')
            item=entries[0]
            if set(item)!={'definition_id','label','source_refs','reason'}:raise ValueError('emotion_fields')
            if item['label'] not in ('正面','中性','负面','未判断'):raise ValueError('emotion_label')
            if not isinstance(item['reason'],str) or not item['reason'].strip():raise ValueError('emotion_reason')
            refs=item['source_refs']
            if not isinstance(refs,list) or any(not isinstance(r,str) or r not in units for r in refs):raise ValueError('emotion_reference')
            if item['label']!='未判断' and not body.intersection(refs):raise ValueError('emotion_body_required')
            values.append(dict(item,status='ready' if item['label']!='未判断' else 'undetermined',
                               evidence=[dict(ref=r,quote=units[r]) for r in refs],confirmed=False))
        except (ValueError,TypeError,KeyError) as error:
            values.append(dict(definition_id=identity,label='未判断',status='failed',reason=str(error),source_refs=[],confirmed=False))
    return values


def enrich_part(raw,document,source,original_part):
    parsed=mvp.requests.compact.common.strict_json(raw)
    if not isinstance(parsed,dict) or set(parsed)!={'events'} or not isinstance(parsed['events'],list):raise ValueError('enrichment_root')
    result=copy.deepcopy(original_part)
    for core in result['items']:
        if not core.get('core'):continue
        index=core['original_index']
        found=[x for x in parsed['events'] if isinstance(x,dict) and type(x.get('index')) is int and x['index']==index]
        core['emotions']=emotions(found[0].get('emotions') if len(found)==1 else None,core,source['content'])
        try:
            if len(found)!=1:raise ValueError('enrichment_missing_or_duplicate')
            value=dict(core['original'],elements=found[0].get('elements'))
            checked=mvp.admit(json.dumps(dict(events=[value]),ensure_ascii=False),document,source['content'],source.get('mapping'))['items'][0]
            if not checked['accepted']:raise ValueError(checked.get('reason','elements_rejected'))
            core.update(accepted=True,projection=checked['projection'],scope=checked['scope'])
            core.pop('reason',None)
        except (ValueError,KeyError,TypeError,IndexError) as error:
            core.update(accepted=False,reason=str(error))
    ready=all(x.get('accepted') and all(e['status']!='failed' for e in x.get('emotions',[])) for x in result['items'])
    result.update(status='ready' if ready else 'partial',enrichment_raw_answer=raw)
    return result


def mentions(document,parts,jid):
    enriched=mvp.extraction(document,parts)
    values={bi*100000+i['original_index']:i.get('emotions',[]) for bi,p in enumerate(parts) for i in p.get('items',[])}
    for c in enriched['core_candidates']:
        c['emotions']=values[c['original_index']]
        c['emotion_status']='ready' if c['emotions'] and all(e['status']!='failed' for e in c['emotions']) else 'failed'
    result=mvp.mentions(enriched,jid)
    for m in result['all_mentions']:m['emotions']=copy.deepcopy(values[m['original_index']])
    result.update(contract_revision=VERSION,core_candidates=enriched['core_candidates'],
                  enrichment_parts=parts,model_executed=bool(parts),local_conversion=False)
    return result
