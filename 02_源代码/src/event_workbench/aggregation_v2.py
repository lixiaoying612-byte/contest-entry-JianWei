"""Source-bound aggregation proposals. No semantic inference or model transport."""
import copy
import re
import unicodedata
from datetime import date

VERSION='event-aggregation-v2'
NORMALIZER='nfkc-case-space-code-field-alias-v4-difference-only'
DIFFERENCE_ONLY_NAMES={'产品','技术产品'}
IDENTITY_NAMES={'项目','药物','交易标的','政策文件','试验登记号','申请号','项目编号','案件编号','债券代码','债券项目','融资方式','具体法人','登记动作','产品型号','会议','讲话','报告期','适应症','国家地区','轮次','观察口径'}
IDENTITY_NAMES.add('影视作品')
SPECIFIC_NAMES=IDENTITY_NAMES-{'融资方式','登记动作','报告期','适应症','国家地区','轮次','观察口径'}
FIELD_ALIASES={'影片':'影视作品','影片名称':'影视作品','片名':'影视作品','电影名称':'影视作品','电视剧名称':'影视作品',
               '药品':'药物','药物名称':'药物','药品名称':'药物','项目名称':'项目','车型':'产品型号','产品名称':'产品型号'}

def identity_name(detail):
    name=detail.get('name')
    return FIELD_ALIASES.get(name,name)

def identity_field_policy():
    """Expose the validator's actual vocabulary instead of asking callers to guess."""
    return dict(paths=['details[i]'],same_names=sorted(IDENTITY_NAMES),
                difference_only_names=sorted(DIFFERENCE_ONLY_NAMES),aliases=dict(FIELD_ALIASES),
                difference_may_use_empty_basis=True,
                note='字段名先按aliases规范化；status和times仅用于claim_delta，不能用于identity_basis。引文仍需覆盖引用字段。')
OPERATIONS={'MATERIAL_ONLY','ENRICH','NEW_NODE','BACKFILL','CORRECT','CONFLICT','NEW_MATTER','PENDING'}
LABELS=dict(MATERIAL_ONLY='转载或等价报道',ENRICH='事实补充',NEW_NODE='新进展',BACKFILL='历史补报',CORRECT='主张更正',CONFLICT='存在分歧',NEW_MATTER='不同事项',PENDING='待核关联')

def normalized(value):
    return re.sub(r'\s+',' ',unicodedata.normalize('NFKC',value).casefold()).strip()

def endpoint_fingerprint(body):
    from event_workbench.unified_pipeline import digest
    return digest({k:body.get(k) for k in ('summary','participants','times','status','details')})

def recall_text(mention):
    b=mention['body']
    text=' '.join([b.get('summary') or '']+[p.get('name','') for p in b.get('participants',[])]+[d.get('value_text','') for d in b.get('details',[])])
    text=normalized(text)
    # Retrieval variants only: never modify source characters or evidence positions.
    codes=re.findall(r'\b[a-z]{2,6}[- ]?\d{3,5}[a-z]?\b',text)
    return text+' '+' '.join(re.sub(r'[- ]','',c) for c in codes)

def identity_anchors(mention):
    return {normalized(d['value_text']) for d in mention['body'].get('details',[]) if identity_name(d) in ('项目','药物','影视作品','产品型号','交易标的','政策文件','试验登记号','案件编号','债券代码') and d.get('value_text')}

def checked_detail(event,path,units,selected):
    if path=='status':value=event.get('status',{});literal=value.get('phase')
    else:
        match=re.fullmatch(r'(details|times)\[(\d+)\]',path)
        if not match:raise ValueError('aggregation_field_path')
        value=event[match[1]][int(match[2])];literal=value.get('value_text') if match[1]=='details' else value.get('text')
    refs=value.get('evidence_refs',[])
    bound={u for i in refs for u in event['evidence_units'][i]}
    if not bound or not bound<=set(selected):raise ValueError('aggregation_field_quote_not_selected')
    if path=='status' and literal is None:return value
    if not isinstance(literal,str) or not literal.strip() or not any(literal in units[u] for u in bound):raise ValueError('aggregation_literal_not_in_quote')
    return value

def validate(g,pair,batch,row):
    """Validate proposed semantics against program-bound mentions and original units.

    This checks structure, references and explicit contradictions, not semantic truth.
    Identity sufficiency and equivalence still require a session/model judgment.
    """
    required={'identity','node_relation','time_relation','operation','identity_basis','claim_delta','rule_ids','decision_status'}
    if not isinstance(g,dict) or set(g)!=required:raise ValueError('aggregation_fields')
    if g['decision_status'] not in ('proposed','pending'):raise ValueError('model_cannot_mark_validated')
    if g['identity'] not in ('same','different','unknown') or g['node_relation'] not in ('same','other','unknown') or g['time_relation'] not in ('same_node','after','before','overlap','unknown'):raise ValueError('aggregation_enum')
    op=g['operation']
    if op not in OPERATIONS:raise ValueError('aggregation_operation')
    if g['decision_status']=='pending' and op!='PENDING':raise ValueError('pending_answer_cannot_merge')
    expected=('different_event' if g['identity']=='different' else 'insufficient' if g['identity']=='unknown' or op=='PENDING' else 'same_event' if g['node_relation']=='same' else 'same_project_new_progress')
    if row['k']!=expected:raise ValueError('aggregation_legacy_relation_mismatch')
    if op=='NEW_MATTER' and g['identity']!='different':raise ValueError('new_matter_requires_difference')
    if op not in ('NEW_MATTER','PENDING') and g['identity']!='same':raise ValueError('operation_requires_same_identity')
    if op in ('MATERIAL_ONLY','ENRICH','CORRECT','CONFLICT') and (g['node_relation']!='same' or g['time_relation']!='same_node'):raise ValueError('operation_requires_same_node')
    if op in ('NEW_NODE','BACKFILL') and g['node_relation']!='other':raise ValueError('operation_requires_other_node')
    if op=='BACKFILL' and g['time_relation']!='before':raise ValueError('backfill_requires_before')
    if op=='NEW_NODE' and g['time_relation'] not in ('after','overlap','unknown'):raise ValueError('new_node_time')
    docs={d['id']:d for d in batch['content']['docs']}
    endpoints={s:docs[pair[s]]['events'][pair[s+'i']] for s in ('a','b')}
    for s in ('a','b'):
        pool={u for refs in endpoints[s]['evidence_units'] for u in refs}
        if not isinstance(row['q'+s],list) or not row['q'+s] or not set(row['q'+s])<=pool:raise ValueError('aggregation_quote_outside_mention')
    def field(s,path):return checked_detail(endpoints[s],path,dict(docs[pair[s]]['units']['u']),row['q'+s])
    basis=g['identity_basis']
    if not isinstance(basis,list) or (g['identity']=='same' and not basis):raise ValueError('identity_basis_required')
    checked=[]
    for item in basis:
        if not isinstance(item,dict) or set(item)!={'slot','a','b','comparison','reason'}:raise ValueError('identity_basis_fields')
        if item['comparison'] not in ('equal','compatible','conflict','unknown') or not item['slot'] or not item['reason']:raise ValueError('identity_comparison')
        values=[field(s,item[s]) for s in ('a','b')]
        if any(not item[s].startswith('details[') for s in ('a','b')):raise ValueError('identity_requires_specific_detail')
        difference_only=(g['identity']=='different' and item['comparison']=='conflict'
                         and all(identity_name(v) in DIFFERENCE_ONLY_NAMES|{'产品型号'} for v in values))
        if difference_only:
            if normalized(values[0]['value_text'])==normalized(values[1]['value_text']):raise ValueError('product_difference_requires_distinct_values')
        elif g['identity']=='same' and any(identity_name(v) not in IDENTITY_NAMES for v in values):raise ValueError('identity_cannot_use_company_or_quantity_only')
        if g['identity']=='same' and identity_name(values[0])!=identity_name(values[1]):raise ValueError('identity_field_types_differ')
        if item['comparison']=='equal' and normalized(values[0]['value_text'])!=normalized(values[1]['value_text']):raise ValueError('identity_equal_literal_mismatch')
        if g['identity']=='same' and all(v.get('name')=='融资方式' for v in values):
            modes=['private' if '非公开' in v['value_text'] else 'public' if '公开' in v['value_text'] else None for v in values]
            if None not in modes and modes[0]!=modes[1]:raise ValueError('different_bond_offering_program')
        if g['identity']=='same' and item['comparison']=='conflict':raise ValueError('identity_conflict_forbids_merge')
        checked.append(dict(item,values=values))
    if g['identity']=='same' and not any(x['comparison'] in ('equal','compatible') for x in basis):raise ValueError('identity_unresolved')
    if g['identity']=='same' and not any(x['comparison'] in ('equal','compatible') and all(identity_name(v) in SPECIFIC_NAMES for v in x['values']) for x in checked):raise ValueError('specific_matter_identity_required')
    generic={'项目','相关项目','上述项目','该项目','公司药品','新药','该药物','公司债券','有关政策','该公司'}
    generic.update({'影片','该影片','本片','相关作品','该作品','产品','该车型'})
    if g['identity']=='same' and not any(x['comparison'] in ('equal','compatible') and all(identity_name(v) in SPECIFIC_NAMES and normalized(v['value_text']) not in generic for v in x['values']) for x in checked):raise ValueError('unresolved_generic_matter')
    delta=g['claim_delta']
    if not isinstance(delta,dict) or set(delta)!={'added','equivalent','disputed','superseded'} or any(not isinstance(v,list) for v in delta.values()):raise ValueError('claim_delta_fields')
    for key,refs in delta.items():
        if len(refs)!=len(set(refs)):raise ValueError('duplicate_claim_delta')
        for ref in refs:
            if not isinstance(ref,str) or not re.fullmatch(r'[ab]\.(?:details|times)\[\d+\]|[ab]\.status',ref):raise ValueError('claim_delta_ref')
            field(ref[0],ref[2:])
    accounted=set(ref for refs in delta.values() for ref in refs)
    if op not in ('PENDING','NEW_MATTER'):
        required_refs={'a.status'}|{f'a.{key}[{i}]' for key in ('details','times') for i in range(len(endpoints['a'].get(key,[])))}
        if not required_refs<=accounted:raise ValueError('incoming_facts_not_classified')
    if op=='MATERIAL_ONLY' and any(delta[k] for k in ('added','disputed','superseded')):raise ValueError('material_only_has_fact_delta')
    if op=='ENRICH' and (not delta['added'] or delta['disputed'] or delta['superseded']):raise ValueError('enrich_requires_nonconflicting_addition')
    if op=='CORRECT' and (not delta['superseded'] or not delta['added']):raise ValueError('correction_requires_old_and_new_claim')
    if op=='CORRECT':
        text=' '.join(dict(docs[pair['a']]['units']['u'])[u] for u in row['qa'])
        if not re.search(r'更正|澄清|修正|撤回|勘误|correct|clarif|retract',text,re.I):raise ValueError('correction_statement_missing')
        if not all(r.startswith('b.') for r in delta['superseded']) or not all(r.startswith('a.') for r in delta['added']):raise ValueError('correction_target_side')
    if op=='CONFLICT' and not delta['disputed']:raise ValueError('conflict_requires_bound_claims')
    if op in ('NEW_NODE','BACKFILL'):
        modes=[e.get('status',{}).get('mode') for e in endpoints.values()]
        phases=[e.get('status',{}).get('phase') for e in endpoints.values()]
        if any(phase is None for phase in phases):raise ValueError('new_node_requires_explicit_phase')
        if phases[0]==phases[1] and modes[0]==modes[1]:raise ValueError('new_node_without_bound_change')
        for s in ('a','b'):field(s,'status')
    if g['time_relation'] in ('after','before'):
        from event_workbench.current_event_dossiers import date_key
        intervals=[]
        for s in ('a','b'):
            times=endpoints[s].get('times',[])
            values=[]
            for i,t in enumerate(times):
                if t.get('basis')!='explicit' or t.get('precision')!='day':continue
                value=field(s,f'times[{i}]')
                literal,_=date_key(value)
                if not literal or value.get('role') not in (None,'occurrence'):continue
                day=date.fromisoformat(literal)
                values.append(day)
            if len(set(values))!=1 or endpoints[s].get('status',{}).get('mode') in ('planned','forecast','conditional'):raise ValueError('temporal_direction_requires_one_explicit_occurrence_day')
            intervals.append(values[0])
        if not (intervals[0]>intervals[1] if g['time_relation']=='after' else intervals[0]<intervals[1]):raise ValueError('temporal_direction_conflict')
    if op not in ('NEW_MATTER','PENDING') and (not isinstance(g['rule_ids'],list) or not g['rule_ids'] or any(r not in {f'R{i:02}' for i in range(1,20)} for r in g['rule_ids'])):raise ValueError('aggregation_rule_ids')
    return dict(copy.deepcopy(g),decision_status='validated',contract_version=VERSION,label=LABELS[op],checked_identity=checked,
                endpoint_fingerprints=[endpoint_fingerprint(endpoints[s]) for s in ('a','b')],semantic_review='session_or_model_not_independent_truth')

def fact_delta_signature(dto):
    """Values, not citation offsets, provider phrasing or source counts."""
    facts={m['mention_id']:f for m,f in zip(dto['evidence_sources']['members'],dto['event_facts'])}
    result=[]
    for p in dto.get('event_relations',[]):
        g=p.get('aggregation')
        if not g or p.get('status')!='model_tentative' or g['operation'] in ('MATERIAL_ONLY','PENDING','NEW_MATTER'):continue
        if not set(p.get('member_ids',[]))<=facts.keys():continue
        delta={}
        for key,refs in g['claim_delta'].items():
            values=[]
            for ref in refs:
                body=facts.get(p['member_ids'][0 if ref[0]=='a' else 1],{})
                path=ref[2:];match=re.fullmatch(r'(details|times)\[(\d+)\]',path)
                value=body.get('status') if path=='status' else body[match[1]][int(match[2])]
                values.append(value)
            delta[key]=values
        result.append(dict(operation=g['operation'],time_relation=g['time_relation'],delta=delta))
    return result

def apply_claim_changes(claims,proposals):
    """A relation alone never refutes a claim. Use explicit field bindings and a
    separately admitted direct correction; keep the old claim and its old score.
    """
    for p in proposals:
        g=p.get('aggregation',{})
        if p.get('status')!='model_tentative' or g.get('operation')!='CORRECT':continue
        a,b=p['member_ids'];delta=g['claim_delta']
        added={r[2:] for r in delta['added'] if r.startswith('a.')}
        removed={r[2:] for r in delta['superseded'] if r.startswith('b.')}
        new=[q for q in claims if q.get('source_mention_id')==a and added.intersection(q.get('fact_refs',[])) and q.get('score_status') in ('已核原始记录','有直接材料支持')]
        if not new:continue
        for q in claims:
            if q.get('source_mention_id')==b and removed.intersection(q.get('fact_refs',[])):
                q['superseded_by']=sorted({n['claim_id'] for n in new})
                q['correction_relation_id']=p['id']

def guard_assignments(proposals,batch):
    """No partial candidate set or competing saved dossiers may silently merge."""
    for coverage in batch['coverage']:
        mid=coverage['mention'];ps=[p for p in proposals if p.get('member_ids',[None])[0]==mid]
        expected={c['mention'] for c in coverage['candidates']}
        checked={p['member_ids'][1] for p in ps if p.get('status') in ('model_tentative','recorded_insufficient')}
        linked=[p for p in ps if p.get('status')=='model_tentative' and p['raw']['k'] in ('same_event','same_project_new_progress')]
        matter_by={c['mention']:c.get('event_id',c['mention']) for c in coverage['candidates']}
        # Fresh mentions are not competing saved dossiers. Their cluster conflicts
        # are checked separately once all comparisons have been received.
        saved={c['mention'] for c in coverage['candidates'] if c.get('persisted_matter',True)}
        targets={matter_by[p['member_ids'][1]] for p in linked if p['member_ids'][1] in saved}
        unresolved=any(p.get('status')=='recorded_insufficient' and p['member_ids'][1] in {c['mention'] for c in coverage['candidates'] if c.get('channels',{}).get('exact_anchors')} for p in ps)
        reason='candidate_comparison_incomplete' if expected-checked or coverage.get('overflow') else 'competing_matter_assignments' if len(targets)>1 else 'unresolved_exact_identity_candidate' if unresolved else None
        if reason:
            for p in linked:p['status']='pending_assignment';p['issues'].append(reason)
    return proposals

def select_current(mids,linked,progress,default,preferred=None):
    """V2 temporal partial order. Legacy dossiers retain their saved behavior."""
    edges=[p for p in progress if p.get('aggregation',{}).get('contract_version')==VERSION and set(p['member_ids'])<=set(mids)]
    if not edges:return default,None
    group={m:i for i,d in enumerate(linked) for m in d['members']};out={i:set() for i in range(len(linked))}
    for p in edges:
        a,b=[group[m] for m in p['member_ids']];t=p['aggregation']['time_relation']
        if t=='after':out[b].add(a)
        elif t=='before':out[a].add(b)
    terminals=[i for i in out if not out[i]]
    selected=terminals[0] if len(terminals)==1 else group.get(preferred or default)
    representative=next((m for m in reversed(mids) if group[m]==selected),default)
    return representative,dict(status='ordered' if len(terminals)==1 else 'parallel_pending',terminal_nodes=[linked[i]['members'] for i in terminals],basis='explicit_occurrence_days; publication_and_arrival_not_business_order')

def business_nodes(linked,by,entries,documents,previous):
    from event_workbench import unified_pipeline as core
    result=[]
    for d in linked:
        mids=d['members'];mid=min(mids,key=lambda m:(entries[by[m][0]]['available_at'],m));jid,mention=by[mid]
        old=next((n for n in (previous or {}).get('event_timeline',{}).get('business_nodes',[]) if set(n['members'])&set(mids)),None)
        times=[t for t in d.get('timeline',[]) if t.get('sort_key') and not t.get('issues') and t.get('mode') not in ('planned','forecast','conditional')]
        dates={t['sort_key'] for t in times}
        time=next(iter(dates)) if len(dates)==1 else documents[jid]['publication_time']
        result.append(dict(id=old['id'] if old else 'node-'+core.digest(mid)[:20],members=mids,time=time,
            kind=mention['body'].get('status',{}).get('phase') or '事项节点',summary=mention['body'].get('summary'),
            time_basis='explicit_occurrence' if len(dates)==1 else 'disclosure_not_occurrence'))
    return sorted(result,key=lambda n:(n['time'] or '',n['id']))

def guard_graph(proposals):
    """Check whole V2-affected matter, not only the last two endpoints."""
    edges=[p for p in proposals if p.get('status')=='model_tentative' and len(p.get('member_ids',[]))==2]
    parent={m:m for p in edges for m in p['member_ids']}
    def find(m):
        while parent[m]!=m:m=parent[m]
        return m
    merges=[p for p in edges if p['raw']['k'] in ('same_event','same_project_new_progress')]
    for p in merges:
        a,b=p['member_ids'];parent[find(a)]=find(b)
    active={find(p['member_ids'][0]) for p in merges if p.get('aggregation',{}).get('contract_version')==VERSION}
    blocked={find(p['member_ids'][0]):'cluster_contains_different_matter' for p in edges if p['raw']['k']=='different_event' and find(p['member_ids'][0])==find(p['member_ids'][1])}
    constraints={}
    for p in merges:
        root=find(p['member_ids'][0])
        for item in p.get('aggregation',{}).get('checked_identity',[]):
            if item['slot'] not in ('药物','债券代码','试验登记号','项目编号','报告期','融资方式','适应症','国家地区') or item['comparison']!='equal':continue
            constraints.setdefault((root,item['slot']),set()).add(normalized(item['values'][0]['value_text']))
    for (root,slot),values in constraints.items():
        if len(values)>1:blocked[root]='cluster_identity_slot_conflict:'+slot
    node={m:m for m in parent}
    def nf(m):
        while node[m]!=m:m=node[m]
        return m
    for p in merges:
        if p['raw']['k']=='same_event':a,b=p['member_ids'];node[nf(a)]=nf(b)
    order={nf(m):set() for m in node}
    for p in merges:
        t=p.get('aggregation',{}).get('time_relation');a,b=[nf(m) for m in p['member_ids']]
        if a==b and p['raw']['k']=='same_project_new_progress':blocked[find(a)]='same_and_other_node_conflict'
        if t=='after':order[b].add(a)
        if t=='before':order[a].add(b)
    indegree={n:0 for n in order}
    for targets in order.values():
        for n in targets:indegree[n]+=1
    ready=[n for n in order if indegree[n]==0]
    while ready:
        for n in order[ready.pop()]:
            indegree[n]-=1
            if indegree[n]==0:ready.append(n)
    for n,count in indegree.items():
        if count:blocked[find(n)]='temporal_cycle'
    for p in merges:
        root=find(p['member_ids'][0])
        if root in active and root in blocked:p['status']='pending_cluster_conflict';p.setdefault('issues',[]).append(blocked[root])
