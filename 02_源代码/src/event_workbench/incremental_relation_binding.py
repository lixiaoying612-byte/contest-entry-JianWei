"""Zero-call, reversible candidate binding and explicit coordinator holds."""
import copy
from collections import Counter
from event_workbench import incremental_relations as old

VERSION='eng021-r1-candidate-binding'


def derive(result,batch):
    raw=old.relations.common.strict_json(result['raw_answer'])
    rows=raw.get('pairs',[]) if isinstance(raw,dict) else []
    if not isinstance(rows,list):rows=[]
    pairs={p['p']:p for p in batch['content']['pairs']}
    counts=Counter(r['p'] for r in rows if isinstance(r,dict) and type(r.get('p')) is int)
    outcomes=[]
    for index,row in enumerate(rows):
        item=dict(original_row=copy.deepcopy(row),original_row_index=index,provider_sha256=result['provider_sha256'],generation_identity=result['identity'],derived_row=None,binding=None,issues=[],status='pending_invalid',member_ids=[],original_proofs=[],confirmed=False,raw={},pair=row.get('p') if isinstance(row,dict) else None)
        pid=item['pair'];item['old_strict_errors']=[x for x in result['checked'].get('issues',[]) if not isinstance(x,dict) or x.get('pair')==pid]
        item['old_strict_errors'] += [p['issues'] for p in result['checked'].get('proposals',[]) if p.get('pair')==pid]
        try:
            if not result['complete']:raise ValueError('incomplete_provider')
            if type(pid) is not int or pid not in pairs:raise ValueError('unknown_candidate')
            if counts[pid]!=1:raise ValueError('duplicate_candidate')
            if set(row)-{'p','r','u'}:raise ValueError('unknown_row_fields')
            if not isinstance(row.get('r'),list) or len(row['r'])!=1 or not isinstance(row['r'][0],dict):raise ValueError('exactly_one_relation_required')
            original=row['r'][0];item['raw']=copy.deepcopy(original);pair=pairs[pid]
            if set(original)-{'k','a','b','ai','bi','qa','qb','why'}:raise ValueError('unknown_relation_fields')
            for side in ('a','b'):
                v=original.get(side);expected=pair[side+'i']
                if not ((type(v) is int and v==expected) or (isinstance(v,str) and v==pair[side])):raise ValueError('endpoint_conflict_'+side)
                if side+'i' in original and (type(original[side+'i']) is not int or original[side+'i']!=expected):raise ValueError('explicit_index_conflict_'+side)
            converted={k:copy.deepcopy(original[k]) for k in ('k','qa','qb','why') if k in original};converted.update(a=pair['ai'],b=pair['bi'])
            derived=dict(p=pid,r=[converted],u='program_generated_validator_placeholder; no model omission assertion')
            item.update(derived_row=derived,binding=dict(candidate=copy.deepcopy(pair),endpoint_source='program_bound_from_unique_p',u_present='u' in row,u_original=copy.deepcopy(row.get('u')),u_placeholder_origin='program_generated',inverse='original_row retained exactly'))
            checked=old.relations.validate(dict(pairs=[derived]),dict(batch['content'],pairs=[pair]));ps=checked['proposals']
            if len(ps)!=1:raise ValueError('derived_structure_invalid')
            p=ps[0];item['issues']+=p['issues'];item['member_ids']=[batch['mapping'][d+':'+str(i)] for d,i in p.get('members',[])]
            item['original_proofs']=[batch['proofs'][q['doc']+':'+q['unit']] for q in p.get('proofs',[])]
            item['status']='pending_invalid' if item['issues'] else 'recorded_insufficient' if original['k']=='insufficient' else 'model_tentative'
        except (ValueError,TypeError,KeyError,IndexError) as e:item['issues'].append(str(e))
        item['id']=result['identity']+'-'+VERSION+'-row-'+str(index);outcomes.append(item)
    return dict(version=VERSION,original_answer=result['raw_answer'],provider_sha256=result['provider_sha256'],generation_identity=result['identity'],outcomes=outcomes,missing_candidates=[p for p in pairs if not counts[p]],returned_rows=len(rows),original_checked=copy.deepcopy(result['checked']))


