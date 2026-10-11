"""Sourced names and securities; metadata never counts as event corroboration."""
import copy,json,re,unicodedata
from functools import lru_cache
from event_workbench import training_corpus as corpus
from event_workbench.news_materials import moment

PATH=corpus.ROOT/'data/security-master.json'
VERSION='security-identity-v1'

@lru_cache(maxsize=32768)
def normalized(value):return unicodedata.normalize('NFKC',value).casefold().strip()

@lru_cache(maxsize=2)
def _load(path,mtime):
    data=json.loads(path.read_text('utf-8'));seen=set()
    for e in data['entities']:
        if not e.get('id') or e['id'] in seen or not e.get('name') or not isinstance(e.get('code'),str) or not e['code'] or e['type'] not in ('company','industry'):raise ValueError('security_directory_identity')
        if not e.get('source_ids') or any(s not in data['sources'] for s in e['source_ids']):raise ValueError('security_directory_source')
        seen.add(e['id'])
    for r in data['relations']:
        if r['entity_id'] not in seen or r['kind'] not in ('product_of','brand_of','subsidiary_of') or not r.get('anchor') or not r.get('source_ids') or any(s not in data['sources'] for s in r['source_ids']):raise ValueError('security_directory_relation')
    grouped=set()
    for g in data.get('issuers',[]):
        ids=set(g['entity_ids'])
        if not g.get('id') or len(ids)<2 or not ids<=seen or grouped&ids or not g.get('source_ids') or any(s not in data['sources'] for s in g['source_ids']):raise ValueError('security_directory_issuer')
        grouped.update(ids)
    return data

def load():return _load(PATH,PATH.stat().st_mtime_ns) if PATH.exists() else None

def _catalogue_path(as_of):
    data=load()
    if data and moment(data['available_at'])<=moment(as_of):return PATH
    # An updated directory must not change validation of an earlier saved answer.
    history=PATH.parent/'security-master-history'
    versions=[(p,_load(p,p.stat().st_mtime_ns)) for p in history.glob('*.json')]
    eligible=[v for v in versions if moment(v[1]['available_at'])<=moment(as_of)]
    return max(eligible,key=lambda v:moment(v[1]['available_at']))[0] if eligible else None

def at_time(as_of):
    path=_catalogue_path(as_of)
    return _load(path,path.stat().st_mtime_ns) if path else None

def metadata(entity,data,anchor,refs,relation,source_ids=None):
    row=dict(entity_id=entity['id'],name=entity['name'],type=entity['type'],code=entity['code'],legal_name=entity.get('legal_name'),
        anchor=anchor,source_refs=refs,relation=relation,source_ids=source_ids or entity['source_ids'])
    group=next((g for g in data.get('issuers',[]) if entity['id'] in g['entity_ids']),None)
    if group:
        members=[e for e in data['entities'] if e['id'] in group['entity_ids']]
        row.update(issuer_id=group['id'],listings=[dict(code=e['code'],market=e['market']) for e in members])
        row['source_ids']=list(dict.fromkeys(row['source_ids']+group['source_ids']))
    row.update(directory_version=data['version'],available_at=data['available_at'],sources=[dict(id=s,**data['sources'][s]) for s in row.pop('source_ids')])
    return row

def contains(alias,text):
    alias=normalized(alias);text=normalized(text)
    if alias.isascii():return bool(re.search(r'(?<![a-z0-9])'+re.escape(alias)+r'(?![a-z0-9])',text))
    return alias in text

def candidates(content,as_of):
    path=_catalogue_path(as_of)
    if not path:return []
    context=set(content.get('identity_context_refs',[]))
    if not context<=set(content['unit_roles']['context_only']):raise ValueError('security_identity_context_outside_title')
    allowed=set(content['unit_roles']['core'])|context
    units=tuple((ref,text) for ref,text in content['u'] if ref in allowed)
    return json.loads(_candidate_json(path,path.stat().st_mtime_ns,units))

@lru_cache(maxsize=64)
def _candidate_json(path,mtime,units):
    # Cached text is immutable; every caller receives a fresh metadata object.
    data=_load(path,mtime)
    result=[];by={e['id']:e for e in data['entities']}
    for entity in data['entities']:
        hits=[(alias,ref) for alias in entity['aliases'] for ref,text in units if contains(alias,text)]
        if hits:
            aliases=dict.fromkeys(a for a,r in hits) if data.get('alias_candidate_policy')=='all_grounded_aliases' else [max((a for a,r in hits),key=len)]
            for alias in aliases:
                result.append(metadata(entity,data,alias,sorted({r for a,r in hits if a==alias}),'same_entity'))
    for relation in data['relations']:
        refs=[ref for ref,text in units if contains(relation['anchor'],text)]
        if refs:
            e=by[relation['entity_id']]
            result.append(metadata(e,data,relation['anchor'],refs,relation['kind'],list(dict.fromkeys(e['source_ids']+relation['source_ids']))))
    return json.dumps(result,ensure_ascii=False)

def industry_catalogue(as_of):
    from event_workbench.development_lexical_candidates import load_definitions
    data=at_time(as_of)
    if not data:return []
    definitions=load_definitions()
    return [dict(name=e['name'],code=e['code'],event_types=list(dict.fromkeys(d['event_name'] for d in definitions if d['target_type']=='industry' and d['target_code']==e['code'])))
            for e in data['entities'] if e['type']=='industry']


def prepare(chunks,as_of):
    result=copy.deepcopy(chunks)
    for chunk in result:
        content=chunk['content']
        content['identity_context_refs']=[r for r in content['unit_roles']['context_only'] if r.startswith('T')]
        content['identity_candidates']=candidates(content,as_of);content['identity_as_of']=as_of
        content['industry_catalogue']=industry_catalogue(as_of)
        if chunk.get('request'):chunk['request']['messages'][1]['content']=json.dumps(content,ensure_ascii=False)
    return result

def resolve(name,kind,refs,content,proposal=None):
    if 'identity_candidates' not in content and 'identity_as_of' not in content:return None
    if 'identity_candidates' not in content or 'identity_as_of' not in content:raise ValueError('security_identity_input_incomplete')
    supplied=content['identity_candidates']
    # Both conversation import and model transport must reproduce the saved catalogue.
    expected=candidates(content,content['identity_as_of'])
    if supplied!=expected:raise ValueError('security_identity_candidates_changed')
    values=[r for r in supplied if r['type']==kind and normalized(name) in {normalized(r['anchor']),normalized(r['name']),normalized(r.get('legal_name') or '')} and set(refs)&set(r['source_refs'])]
    if not values and kind=='industry' and 'industry_catalogue' in content:
        if content['industry_catalogue']!=industry_catalogue(content['identity_as_of']):raise ValueError('security_industry_catalogue_changed')
        data=at_time(content['identity_as_of'])
        entities=[e for e in data['entities'] if e['type']=='industry' and normalized(e['name'])==normalized(name)] if data else []
        if len(entities)==1 and set(refs)&set(content['unit_roles']['core']):
            values=[dict(metadata(entities[0],data,name,list(refs),'model_industry'),inference_status='model_inferred_not_independently_verified')]
    if not values and proposal and kind=='company':
        if proposal['identity_relation']!='same_entity':return None
        data=at_time(content['identity_as_of'])
        if data:
            key=normalized(proposal['canonical_name'])
            # A model-suggested parent is not an alias. Only a literal shortening
            # of a sourced name is admissible here; nicknames require sourced aliases.
            compact=lambda s:re.sub(r'[\s,.，。()（）-]','',normalized(s))
            alias=compact(name)
            if len(alias)<2 or re.search(r'(公司|有限责任公司|limited|\bltd\.?|\binc\.?|\bllc)$',normalized(name)):return None
            eligible=[e for e in data['entities'] if e['type']=='company' and key in {normalized(a) for a in e['aliases']} and any(alias in compact(a) for a in e['aliases'])]
            units=dict(content['u'])
            for e in eligible:
                conflict=any(re.search(r'子公司|母公司|旗下|控股股东|合资|subsidiar|owned by|brand of',units.get(r,''),re.I) and
                    any(normalized(a)!=normalized(name) and contains(a,units.get(r,'')) for a in e['aliases']) for r in refs)
                if not conflict:values.append(metadata(e,data,name,list(refs),'model_alias'))
            for row in values:row.update(alias_proposal=proposal['canonical_name'],alias_reason=proposal['identity_reason'],alias_status='model_inferred_not_independently_verified')
    identities={r.get('issuer_id',r['entity_id']) for r in values}
    if len(identities)>1:
        return dict(status='ambiguous',reason='原文名称对应多个证券身份，尚不能唯一确定市场或代码',candidates=copy.deepcopy(values))
    if not identities:return None
    # Prefer the explicit brand/product relationship to a shorter company substring.
    return copy.deepcopy(max(values,key=lambda r:(r['relation'] not in ('same_entity','model_alias'),len(r['anchor']),r['entity_id'].startswith('security:HK:'))))
