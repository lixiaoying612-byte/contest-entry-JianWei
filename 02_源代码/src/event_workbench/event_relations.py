"""ENG012 bounded extraction, model relation proposals and tentative dossier adapter."""
import copy
import html
import itertools
import json
from pathlib import Path
from event_workbench import compact_live_recovery as recovery
from event_workbench import event_dossiers as dossiers

base=recovery.base;common=base.common;compact=base.compact;v2=base.v2
ROOT=compact.ROOT;VERSION='eng012-v1'




def validate(raw,content):
    docs={d['id']:d for d in content['docs']};pairs={x['p']:x for x in content['pairs']};proposals=[];global_issues=[]
    if not isinstance(raw,dict) or set(raw)!={'pairs'} or not isinstance(raw['pairs'],list):return dict(proposals=[],issues=['invalid_root'],missing_pairs=list(pairs))
    counts={i:0 for i in pairs}
    for row_idx,row in enumerate(raw['pairs']):
        if not isinstance(row,dict) or type(row.get('p')) is not int or row['p'] not in pairs:
            global_issues.append(dict(row=row_idx,code='unknown_pair'));continue
        pid=row['p'];counts[pid]+=1;pair=pairs[pid]
        if set(row)!={'p','r','u'} or not isinstance(row.get('r'),list) or not row['r'] or not isinstance(row.get('u'),str) or not row['u'].strip():
            global_issues.append(dict(pair=pid,code='pair_structure'));continue
        for index,item in enumerate(row['r']):
            p=dict(id=f'p{pid}-r{row_idx}-{index}',pair=pid,raw=item,issues=[],unprocessed=row['u']);proposals.append(p)
            if not isinstance(item,dict) or not {'k','a','b','qa','qb','why'}<=set(item) or set(item)-{'k','a','b','qa','qb','why','f'}:
                p['issues'].append('relation_structure');continue
            if item['k'] not in ('same_event','same_project_new_progress','different_event','insufficient'):p['issues'].append('relation_type')
            if not isinstance(item['why'],str) or not item['why'].strip():p['issues'].append('missing_reason')
            p['members']=[];p['proofs']=[];p['fields']=[]
            for side in ['a','b']:
                doc=docs[pair[side]];i=item[side];units=dict(doc['units']['u']);qs=item['q'+side]
                if type(i) is not int or not 0<=i<len(doc['events']):p['issues'].append('unknown_mention');continue
                p['members'].append((doc['id'],i))
                if not isinstance(qs,list) or not qs or any(not isinstance(q,str) or q not in units for q in qs):p['issues'].append('unknown_unit');continue
                if len(set(qs))!=len(qs):p['issues'].append('duplicate_unit')
                pool={u for us in doc['events'][i]['evidence_units'] for u in us}
                if not pool.intersection(qs):p['issues'].append('mention_evidence_not_selected')
                p['proofs'] += [dict(doc=doc['id'],unit=q,quote=units[q],section='title' if q.startswith('T') else 'body') for q in qs]
            if len(p['members'])==2 and p['members'][0]==p['members'][1]:p['issues'].append('self_loop')
            fs=item.get('f',[])
            if not isinstance(fs,list):p['issues'].append('fields_structure');continue
            if fs and item['k'] not in ('same_event','same_project_new_progress'):p['issues'].append('fields_not_permitted')
            for f in fs:
                if not isinstance(f,list) or len(f)!=3 or f[0] not in ('a','b') or type(f[1]) is not int or type(f[2]) is not int:p['issues'].append('field_reference');continue
                side,i,idx=f;doc=docs[pair[side]]
                if not 0<=i<len(doc['events']) or not 0<=idx<len(doc['events'][i]['details']):p['issues'].append('field_reference');continue
                event=doc['events'][i];qs=item.get('q'+side,[]);refs=event['details'][idx]['evidence_refs']
                required={u for ri in refs for u in event['evidence_units'][ri]}
                if not required or not isinstance(qs,list) or not required<=set(qs):p['issues'].append('field_evidence_not_selected')
                if item['k']=='same_project_new_progress' and i!=item[side]:p['issues'].append('progress_field_wrong_endpoint')
                p['fields'].append(dict(doc=doc['id'],index=i,detail=idx))
    missing=[i for i,n in counts.items() if n==0]
    for p in proposals:
        if counts[p['pair']]>1:p['issues'].append('duplicate_pair')
    # Conservative closure includes intended same-event edges, even invalid ones.
    parent={}
    def find(x):
        parent.setdefault(x,x)
        if parent[x]!=x:parent[x]=find(parent[x])
        return parent[x]
    for p in proposals:
        ms=p.get('members',[])
        if len(ms)==2 and p['raw']['k']=='same_event':parent[find(ms[1])]=find(ms[0])
    blocked=set()
    seen={}
    for p in proposals:
        ms=p.get('members',[])
        if len(ms)!=2:continue
        key=(tuple(sorted(ms)),p['raw']['k']);seen.setdefault(key,[]).append(p)
        if p['raw']['k']=='same_event' and p['issues']:blocked.add(find(ms[0]))
        if p['raw']['k'] in ('different_event','same_project_new_progress') and find(ms[0])==find(ms[1]):
            blocked.add(find(ms[0]));p['issues'].append('contradicts_same_event_group')
    for values in seen.values():
        if len(values)>1:
            for p in values:
                p['issues'].append('duplicate_relation')
                if p['raw']['k']=='same_event':blocked.add(find(p['members'][0]))
    for p in proposals:
        if p.get('members') and p['raw']['k'] in ('same_event','same_project_new_progress') and any(find(m) in blocked for m in p['members']):p['issues'].append('merge_dependency_invalid')
        p['status']='pending_invalid' if p['issues'] else 'recorded_insufficient' if p['raw']['k']=='insufficient' else 'validated_proposal'
    if missing:global_issues.append('missing_pairs')
    return dict(proposals=proposals,issues=global_issues,missing_pairs=missing)


