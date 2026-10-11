"""Lossless semantic projection/material tables; original model objects stay local."""
import copy
import re
from event_workbench import auto_verification as base

VERSION='eng024-r1-verification-tables'
FIELDS=('participants','times','details','impact_mentions')


def prepare(batch):
    c=batch['content'];columns={}
    for field in FIELDS:
        objects=[v for m in c['mentions'] for v in m['projection'].get(field,[]) if isinstance(v,dict)]
        columns[field]=list(objects[0]) if objects else []
    statuses=[m['projection'].get('status') for m in c['mentions'] if isinstance(m['projection'].get('status'),dict)]
    columns['status']=list(statuses[0]) if statuses else []
    mentions=[]
    for m in c['mentions']:
        projection=copy.deepcopy(m['projection'])
        for field in FIELDS:
            if field in projection:
                projection[field]=[[v[k] for k in columns[field]] if isinstance(v,dict) and set(v)==set(columns[field]) else v for v in projection[field]]
        v=projection.get('status')
        if isinstance(v,dict) and set(v)==set(columns['status']):projection['status']=[v[k] for k in columns['status']]
        mentions.append([m['id'],batch['mapping']['mentions'][str(m['id'])]['original_index'],projection,[[q['index'],q['materials']] for q in m['quote_index_to_materials']],copy.deepcopy(m['field_warnings'])])
    sources=[];lookup={};materials=[]
    for m in c['materials']:
        metadata={k:v for k,v in m.items() if k not in ('id','text','relation')}
        # Equal text/channel never identifies a document. Metadata dedup is scoped to its original document.
        identity=batch['mapping']['materials'][m['id']]['document']['local_id'];key=base.digest([identity,metadata])
        if key not in lookup:lookup[key]='S'+str(len(sources));sources.append([lookup[key],metadata])
        materials.append([m['id'],lookup[key],m['relation'],m['text']])
    wire=dict(contract=VERSION,columns=dict(mentions=['id','original_index','projection','quote_index_to_materials','field_warnings'],sources=['id','metadata'],materials=['id','source','relation','text'],candidates=['mention','materials'],quotes=['index','materials'],projection=columns),mentions=mentions,sources=sources,materials=materials,candidates=[[r['mention'],r['materials']] for r in c['candidates']],mode=c['mode'])
    expected=dict(c,mentions=[{k:copy.deepcopy(v) for k,v in m.items() if k!='original'} for m in c['mentions']])
    if 'evidence_context' in c:wire['evidence_context']=copy.deepcopy(c['evidence_context'])
    if expand(wire)!=expected:raise ValueError('semantic_projection_roundtrip_failed')
    return dict(content=wire,mapping=copy.deepcopy(batch['mapping']),retrieval_version=batch['retrieval_version'],original_batch_sha256=base.digest(batch),roundtrip_sha256=base.digest(expected),original_input=copy.deepcopy(c))


def expand(wire):
    sources=dict(wire['sources']);columns=wire['columns']['projection'];mentions=[]
    for mid,original_index,projection,quotes,warnings in wire['mentions']:
        p=copy.deepcopy(projection)
        for field in FIELDS:
            if field in p:p[field]=[dict(zip(columns[field],v,strict=True)) if isinstance(v,list) else v for v in p[field]]
        if isinstance(p.get('status'),list):p['status']=dict(zip(columns['status'],p['status'],strict=True))
        mentions.append(dict(id=mid,projection=p,quote_index_to_materials=[dict(index=i,materials=ms) for i,ms in quotes],field_warnings=copy.deepcopy(warnings)))
    out=dict(mentions=mentions,materials=[dict(id=i,text=t,relation=relation,**sources[s]) for i,s,relation,t in wire['materials']],candidates=[dict(mention=i,materials=ms) for i,ms in wire['candidates']],mode=wire['mode'])
    if 'evidence_context' in wire:out['evidence_context']=copy.deepcopy(wire['evidence_context'])
    return out


def coverage(checked,batch):
    rows=[]
    for mid,b in batch['mapping']['mentions'].items():
        claims=[c for c in checked['valid'] if c['original_mention_index']==b['original_index']]
        returned={a['proposal']['material'] for c in claims for a in c['valid_assessments'] if a['proposal']['material'] is not None}
        candidates=next(r['materials'] for r in batch['content']['candidates'] if str(r['mention'])==mid)
        notes=[n for n in checked.get('notes',[]) if isinstance(n,str) and re.match(r'^mention\s*=\s*'+re.escape(mid)+r'\s*[:：]\s*\S',n)]
        rows.append(dict(mention=int(mid),original_index=b['original_index'],checkable_claims=len(claims),explicit_uncovered_notes=notes,coverage_status='checkable_claims_returned' if claims else 'explicitly_uncovered' if notes else 'uncovered_without_specific_explanation',candidate_opinions_returned=[m for m in candidates if m in returned],candidates_without_valid_opinion=[m for m in candidates if m not in returned],not_returned_is_not_checked=True,confirmed=False))
    return dict(mentions=rows,notes=checked.get('notes',[]),notes_are_model_scope_statements_not_candidate_judgments=True)
