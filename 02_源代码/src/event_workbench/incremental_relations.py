"""Incremental candidate adapters; original mentions and model opinions stay separate."""
import copy
import json
from event_workbench import evidence_retrieval as retrieval
from event_workbench import event_relations as relations
from event_workbench import auto_verification as journal

digest=journal.digest


def candidates(incoming,history,vectors):
    import numpy as np
    import bm25s
    eligible=[h for h in history if retrieval.query(h['body'])['text'].strip()]
    index=bm25s.BM25(method='lucene',k1=1.5,b=.75)
    if eligible:index.index([list(retrieval.old.baseline.terms(retrieval.query(h['body'])['text']).elements()) for h in eligible],show_progress=False)
    result=[]
    for m in incoming:
        text=retrieval.query(m['body'])['text'];union={}
        if text.strip() and eligible:
            bm=index.get_scores(list(retrieval.old.baseline.terms(text).elements()))
            dense=[float(np.max(vectors[m['id']]@vectors[h['id']].T)) for h in eligible]
            for channel,scores in [('bm25',bm),('bge_dense',dense)]:
                rank=0
                for i in sorted(range(len(eligible)),key=lambda i:(-float(scores[i]),i)):
                    h=eligible[i]
                    if h['id']==m['id'] or channel=='bm25' and scores[i]<=0:continue
                    rank+=1;union.setdefault(h['id'],{})[channel]=dict(rank=rank,score=float(scores[i]))
                    if rank==2:break
        result.append(dict(mention=m['id'],query=retrieval.query(m['body']),candidates=[dict(mention=k,channels=v) for k,v in union.items()],status='candidates_pending' if union else 'no_candidates' if text.strip() else 'no_query'))
    return result


def batch(incoming,history,documents,ranked):
    all_mentions={m['id']:m for m in history+incoming};needed={r['mention'] for r in ranked}
    needed.update(c['mention'] for r in ranked for c in r['candidates'])
    docs=[];mapping={};lookup={};proofs={}
    for did,document in documents.items():
        members=[m for m in all_mentions.values() if m['id'] in needed and m['document_id']==did]
        if not members:continue
        short='D'+str(len(docs));rep,_=relations.compact.source_representation(document['document']);units=dict(rep['u']);events=[];used=set()
        for m in members:
            body=m['body'];refs=[[u for u,t in rep['u'] if t==q['quote']] for q in body['evidence_quotes']]
            if any(not x for x in refs):raise ValueError('original_quote_has_no_source_unit')
            for rs in refs:used.update(rs)
            e={k:copy.deepcopy(body[k]) for k in ('summary','participants','times','status','details','uncertainties') if k in body}
            e['evidence_units']=refs;e['field_warnings']=m['field_warnings'];idx=len(events);events.append(e)
            lookup[m['id']]=(short,idx);mapping[short+':'+str(idx)]=m['id']
        for u in used:
            section='title' if u.startswith('T') else 'body'
            proofs[short+':'+u]=dict(relations.dossiers.locate(document['document'],section,units[u]),document_id=did,document_sha256=digest(document['document']))
        docs.append(dict(id=short,publication_time=document['document'].get('publication_time'),source_family=document.get('source_family'),units={'u':[[u,t] for u,t in rep['u'] if u in used]},events=events))
    pairs=[]
    for row in ranked:
        a,ai=lookup[row['mention']]
        for candidate in row['candidates']:
            b,bi=lookup[candidate['mention']];pairs.append(dict(p=len(pairs),a=a,b=b,ai=ai,bi=bi))
    return dict(content=dict(docs=docs,pairs=pairs),mapping=mapping,proofs=proofs,coverage=ranked,incoming=[m['id'] for m in incoming])


def validate(answer,batch,complete=True):
    if not complete:return dict(proposals=[],issues=['incomplete_or_failed_response'],coverage=[dict(r,status='not_processed') for r in batch['coverage']])
    try:
        raw=relations.common.strict_json(answer)
        value=relations.validate(raw,batch['content'])
    except (ValueError,TypeError,KeyError,IndexError):return dict(proposals=[],issues=['invalid_output'],coverage=[dict(r,status='not_processed') for r in batch['coverage']])
    allowed={p['p']:p for p in batch['content']['pairs']}
    for p in value['proposals']:
        pair=allowed[p['pair']];r=p['raw']
        if not isinstance(r,dict) or r.get('a')!=pair['ai'] or r.get('b')!=pair['bi']:p['issues'].append('outside_candidate_whitelist')
        if isinstance(r,dict) and r.get('f'):p['issues'].append('field_updates_out_of_scope')
        p['member_ids']=[batch['mapping'][str(d)+':'+str(i)] for d,i in p.get('members',[]) if str(d)+':'+str(i) in batch['mapping']]
        p['original_proofs']=[batch['proofs'][q['doc']+':'+q['unit']] for q in p.get('proofs',[]) if q['doc']+':'+q['unit'] in batch['proofs']]
        p['status']='pending_invalid' if p['issues'] else 'recorded_insufficient' if r['k']=='insufficient' else 'model_tentative'
        p['confirmed']=False
    # Each candidate gets exactly one opinion; duplicate candidate opinions stay pending.
    for pair in allowed.values():
        ps=[p for p in value['proposals'] if p['pair']==pair['p']]
        if len(ps)>1:
            for p in ps:p['issues'].append('multiple_candidate_opinions');p['status']='pending_invalid'
    value['coverage']=[]
    for row in batch['coverage']:
        checks=[]
        for candidate in row['candidates']:
            pair=next(x for x in allowed.values() if batch['mapping'][x['a']+':'+str(x['ai'])]==row['mention'] and batch['mapping'][x['b']+':'+str(x['bi'])]==candidate['mention'])
            ps=[p for p in value['proposals'] if p['pair']==pair['p']]
            checks.append(dict(candidate, pair=pair['p'],status='not_processed' if not ps else ps[0]['status'] if len(ps)==1 else 'pending_invalid'))
        value['coverage'].append(dict(row,candidates=checks))
    return value


def current_graph(mentions,proposals):
    """Recompute from current eligible edges, never persist union-find parents."""
    rows=copy.deepcopy(proposals);parent={m:m for m in mentions}
    def find(m):
        if parent[m]!=m:parent[m]=find(parent[m])
        return parent[m]
    eligible=[p for p in rows if p['status']=='model_tentative' and len(p['member_ids'])==2 and all(m in parent for m in p['member_ids'])]
    for p in eligible:
        if p['raw']['k']=='same_event':a,b=p['member_ids'];parent[find(b)]=find(a)
    blocked=set()
    for p in eligible:
        a,b=p['member_ids']
        if p['raw']['k'] in ('different_event','same_project_new_progress') and find(a)==find(b):blocked.add(find(a))
    for p in eligible:
        if any(find(m) in blocked for m in p['member_ids']):p['status']='pending_closure_conflict';p['issues'].append('current_cross_batch_closure_conflict')
    parent={m:m for m in mentions}
    for p in eligible:
        if p['status']=='model_tentative' and p['raw']['k']=='same_event':a,b=p['member_ids'];parent[find(b)]=find(a)
    groups={}
    for m in mentions:groups.setdefault(find(m),[]).append(m)
    member_group={m:'event-'+digest(sorted(ms))[:20] for ms in groups.values() for m in ms}
    progress=[dict(id=p['id'],new_node=member_group[p['member_ids'][0]],history_node=member_group[p['member_ids'][1]],direction='ingestion_order_not_causal_or_occurrence_order',confirmed=False) for p in eligible if p['status']=='model_tentative' and p['raw']['k']=='same_project_new_progress']
    return dict(proposals=rows,groups=[dict(id=member_group[ms[0]],members=ms,status='model_tentative' if len(ms)>1 else 'unlinked_mention',confirmed=False) for ms in groups.values()],progress=progress,confirmed=False)


