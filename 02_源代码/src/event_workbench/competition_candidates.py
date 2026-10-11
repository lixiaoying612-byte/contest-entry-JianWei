"""Bounded object routing when candidates are absent or only weak word overlap."""
import copy,json,re
from event_workbench.development_lexical_candidates import load_definitions

VERSION='candidate-scope-20261006-v4-union'
# This mapping adds candidates only. It never confirms an event or an affiliation.
ALIASES=[dict(code='002460',name='赣锋锂业',alias='Ganfeng Lithium',
    source='https://www1.hkexnews.hk/listedco/listconews/sehk/2025/0409/2025040901075_c.pdf',
    source_date='2025-04-09',checked_on='2026-09-27',role='candidate_only_not_event_evidence')]


def alias_hits(text):
    return [dict(a) for a in ALIASES if re.search(r'(?<![A-Za-z0-9])'+re.escape(a['alias'])+r'(?![A-Za-z0-9])',text,re.I)]


def target_aliases(definition,text):
    return [a for a in alias_hits(text) if a['code']==definition.get('target_code') and a['name']==definition.get('target_name')]


def target_evidence(definition,text):
    return any(definition.get(k) and definition[k] in text for k in ('target_name','target_code')) or bool(target_aliases(definition,text))


def literal_fallback(chunks):
    result=copy.deepcopy(chunks);catalog=load_definitions()
    for chunk in result:
        text='\n'.join(t for _,t in chunk['content']['u'])
        aliases=alias_hits(text);codes={a['code'] for a in aliases}
        chosen=[d for d in catalog if d['target_code'] in codes or d['target_name'] in text or re.search(r'(?<![A-Za-z0-9])'+re.escape(d['target_code'])+r'(?![A-Za-z0-9])',text)]
        known={d['definition_id'] for d in chunk['content']['c']}
        chunk['content']['c'].extend(d for d in chosen if d['definition_id'] not in known)
        if aliases:chunk['content']['target_aliases']=aliases
        if chunk.get('request'):chunk['request']['messages'][1]['content']=json.dumps(chunk['content'],ensure_ascii=False)
        chunk['candidate_origin']='official_literal_or_sourced_alias_candidates' if chosen else 'unresolved'
        chunk['alias_candidates']=aliases
    return result
