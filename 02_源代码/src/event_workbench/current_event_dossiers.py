"""Pure current-state dossiers; no identity decisions or model-written intermediates."""
import copy
import json
import re
import sqlite3
from contextlib import contextmanager
from datetime import date,datetime
from pathlib import Path
from event_workbench import unified_pipeline as base
from event_workbench import event_dossiers as old
from event_workbench import impact_watch

VERSION='eng025-current-candidates-v1'
FIELDS=('participants','status','times','details','impact_mentions','matches')


def verify_relation_proof(proof,documents):
    """Relation proofs omit the derived quote hash; verify their stored positions unchanged."""
    if not isinstance(proof,dict) or not isinstance(proof.get('quote'),str):raise ValueError('invalid_relation_quote')
    bound=dict(proof)
    if 'quote_sha256' not in bound:bound['quote_sha256']=old.sha(proof['quote'].encode('utf-8'))
    impact_watch.verify_proof(bound,documents)


def clean(value):
    if isinstance(value,dict):return {k:clean(v) for k,v in value.items() if k not in ('evidence_refs','quote')}
    if isinstance(value,list):return [clean(v) for v in value]
    return value


def date_key(value):
    """Only literal complete dates/months/years, never infer a year from publication."""
    text=value.get('text');normalized=value.get('normalized');precision=value.get('precision')
    if not isinstance(text,str) or not isinstance(normalized,str) or value.get('basis')!='explicit':return None,'unknown_or_inferred_normalization'
    t=text.strip();n=normalized.strip()
    matches=[]
    for y,m,d in re.findall(r'(?<!\d)(\d{4})年(\d{1,2})月(\d{1,2})日',t):
        try:matches.append(date(int(y),int(m),int(d)).isoformat())
        except ValueError:pass
    months={m.lower():i for i,m in enumerate(['January','February','March','April','May','June','July','August','September','October','November','December'],1)}
    for month,day,year in re.findall(r'\b([A-Za-z]+)\s+(\d{1,2})(?:st|nd|rd|th)?[,]?\s+(\d{4})\b',t):
        try:matches.append(date(int(year),months[month.lower()],int(day)).isoformat())
        except (ValueError,KeyError):pass
    for item in re.findall(r'(?<!\d)\d{4}-\d{2}-\d{2}(?!\d)',t):
        try:matches.append(date.fromisoformat(item).isoformat())
        except ValueError:pass
    if precision=='day' and n in matches:return n,'literal_day_normalization_unverified'
    if precision=='month':
        candidates=[f'{int(y):04d}-{int(m):02d}' for y,m in re.findall(r'(?<!\d)(\d{4})年(\d{1,2})月',t) if 1<=int(m)<=12]
        candidates+=re.findall(r'(?<!\d)\d{4}-(?:0[1-9]|1[0-2])(?![-\d])',t)
        if n in candidates:return n,'literal_month_no_day_added'
    if precision=='year' and re.fullmatch(r'\d{4}',n) and re.search(r'(?<!\d)'+n+r'(?:年|\b)',t):return n,'literal_year_no_month_added'
    if precision=='interval' and '/' in n:
        ends=n.split('/')
        if len(ends)==2 and all(x in matches for x in ends) and ends[0]<=ends[1]:return ends[0],'literal_day_interval_keep_both_endpoints'
    return None,'unsupported_precision_or_literal_binding'


def selected_proofs(document,jid,mention,path,value):
    refs=value.get('evidence_refs',[]) if isinstance(value,dict) else []
    quotes=mention['body'].get('evidence_quotes',[]);selected=[];issues=[]
    if path=='summary':selected=quotes
    elif isinstance(value,dict) and isinstance(value.get('quote'),dict):selected=[value['quote']]
    elif isinstance(refs,list) and refs and all(type(i) is int and 0<=i<len(quotes) for i in refs):selected=[quotes[i] for i in refs]
    else:issues.append('missing_or_invalid_selected_evidence')
    if base.digest(document)!=jid:issues.append('document_version_no_longer_matches_job')
    proofs=[]
    for q in selected:
        try:
            p=dict(old.locate(document,q['section'],q['quote']),document_id=jid,document_sha256=old.fingerprint(document),quote_sha256=old.sha(q['quote'].encode('utf-8')))
            impact_watch.verify_proof(p,{jid:dict(document=document)})
            text=document['text' if q['section']=='body' else 'title'] or ''
            if text.find(q['quote'],p['start']+1)>=0:issues.append('ambiguous_repeated_quote')
            proofs.append(p)
        except (KeyError,TypeError,ValueError):issues.append('selected_quote_not_locatable')
    prefix=f"$.events[{mention['original_index']}].{path}"
    warnings=[w for w in mention.get('field_warnings',[]) if isinstance(w,dict) and (w.get('field','')==prefix or w.get('field','').startswith(prefix+'.') or w.get('field','').startswith(prefix+'['))]
    if warnings:issues.append('upstream_selected_field_warning')
    return proofs,issues,warnings


def candidates(mention,jid,document,relations):
    body=mention['body'];rows=[]
    values=[('summary','summary',body.get('summary'))]
    for field in FIELDS:
        v=body.get(field)
        if isinstance(v,list):values.extend((field,f'{field}[{i}]',x) for i,x in enumerate(v))
        elif v is not None:values.append((field,field,v))
    for field,path,value in values:
        proofs,issues,warnings=selected_proofs(document,jid,mention,path,value)
        eligible=bool(proofs) and not issues and field!='matches'
        qualifier=dict(status=clean(body.get('status')),times=clean(body.get('times',[])))
        origin=dict(mention=mention['id'],document_id=jid,original_document_id=mention['document_id'],document_sha256=old.fingerprint(document),original_index=mention['original_index'],field_path=path,original_value=copy.deepcopy(value),quotes=proofs,issues=issues,field_warnings=warnings,identity_relation_ids=relations,eligible_for_evidence_organization=eligible,semantic_support='unverified',confirmed=False)
        key=old.fingerprint(dict(field=field,value=clean(value),qualifier=qualifier))
        rows.append(dict(id='candidate-'+key[:24],category=field,value=copy.deepcopy(value),qualifiers=qualifier,origins=[origin],confirmed=False))
    return rows


def derive(documents,states,families=None,withdrawn=()):
    families=families or {};members={};owners={};proposals={};unavailable=[]
    for jid,ss in sorted(states.items()):
        m=ss['mentions']
        if m['status'] not in base.GOOD or ss['extract']['status'] not in base.GOOD:
            unavailable.append(dict(document_id=jid,status=m['status'],reason='current_mentions_or_extraction_unavailable'));continue
        for row in (m['result'] or {}).get('mentions',[]):members[row['id']]=row;owners[row['id']]=jid
        if ss['relations']['status'] in base.GOOD:
            for p in (ss['relations']['result'] or {}).get('proposals',[]):
                if p['id'] not in withdrawn:proposals[p['id']]=p
    catalog={j:dict(document=d) for j,d in documents.items()}
    for mid,m in members.items():catalog[m['document_id']]=dict(document=documents[owners[mid]])
    checked_proposals=[]
    for p in proposals.values():
        p=copy.deepcopy(p)
        if p['status']=='model_tentative':
            try:
                if not p.get('original_proofs'):raise ValueError('missing_identity_proofs')
                for proof in p['original_proofs']:
                    verify_relation_proof(proof,catalog)
            except (KeyError,TypeError,ValueError):p['status']='pending_invalid';p['issues']=p.get('issues',[])+['current_identity_evidence_unavailable']
        checked_proposals.append(p)
    graph=base.relations.current_graph(sorted(members),checked_proposals);dossiers=[]
    for group in graph['groups']:
        mids=sorted(group['members']);own=set(mids)
        edges=[p for p in graph['proposals'] if p['status']=='model_tentative' and p['raw'].get('k')=='same_event' and set(p['member_ids'])<=own]
        basis=[p['id'] for p in edges];rows={};time_rows=[];missing=[]
        for mid in mids:
            mention=members[mid];jid=owners[mid]
            for candidate in candidates(mention,jid,documents[jid],basis):
                key=candidate['id']
                if key in rows:rows[key]['origins'].extend(candidate['origins'])
                else:rows[key]=candidate
            if not mention['body'].get('times'):missing.append(dict(mention=mid,field='times',status='unknown_no_occurrence_time'))
            for i,t in enumerate(mention['body'].get('times',[])):
                proofs,issues,warnings=selected_proofs(documents[jid],jid,mention,f'times[{i}]',t);key,reason=date_key(t)
                if not isinstance(t.get('text'),str) or not any(t['text'] in p['quote'] for p in proofs):issues.append('time_literal_not_in_selected_evidence')
                if issues:key=None
                time_rows.append(dict(mention=mid,document_id=jid,original_index=mention['original_index'],field_path=f'times[{i}]',original=copy.deepcopy(t),mode=mention['body'].get('status',{}).get('mode'),attributed_to=mention['body'].get('status',{}).get('attributed_to'),sort_key=key,normalization_check=reason,quotes=proofs,issues=issues,warnings=warnings,derived=True,confirmed=False))
        values=list(rows.values());differences={}
        for c in values:
            origins=c['origins'];supplier_docs={x['document_id'] for x in origins if x['eligible_for_evidence_organization']};supplier_mentions={x['mention'] for x in origins}
            c['supplement_for_members']=[m for m in mids if m not in supplier_mentions and any(d!=owners[m] for d in supplier_docs)]
            c['supplement_label']='另一成员报道补充的候选，非独立确认' if c['supplement_for_members'] else None
            value=c['value'];name=value.get('name',value.get('role',value.get('target_name',c['category']))) if isinstance(value,dict) else c['category']
            differences.setdefault((c['category'],name),[]).append(c['id'])
        source_ids=sorted({owners[m] for m in mids});sources=[dict(document_id=j,title=documents[j]['title'],source=documents[j]['source'],publication_time=documents[j]['publication_time'],document_sha256=old.fingerprint(documents[j]),source_family=families.get(j),independent_confirmation='not_established') for j in source_ids]
        links=[]
        for j in source_ids:
            for stage in ('verification','impact'):
                s=states[j][stage];links.append(dict(document_id=j,stage=stage,status=s['status'],semantic_sha=s.get('semantic_sha'),current=s['status'] in base.GOOD,confirmed=False))
        record=dict(id=group['id'],members=mids,member_summaries=[dict(id=m,document_id=owners[m],original_index=members[m]['original_index'],summary=members[m]['body'].get('summary'),field_warnings=members[m].get('field_warnings',[])) for m in mids],identity_status=group['status'],identity_basis=[dict(id=p['id'],raw=p['raw'],proofs=p.get('original_proofs',[]),confirmed=False) for p in edges],candidates=values,field_differences=[dict(category=k[0],literal_name=k[1],candidates=ids,status='difference_or_qualifier_pending_not_contradiction') for k,ids in differences.items() if len(ids)>1],unaligned_names='异名/跨语言并列，不自动同义归并',sources=sources,source_dependence='known_shared_family' if len(sources)>1 and len({s['source_family'] for s in sources})==1 and sources[0]['source_family'] else 'unknown_not_independent',timeline=sorted(time_rows,key=lambda t:(t['sort_key'] is None,t['sort_key'] or '',t['mention'],t['field_path'])),publication_records=[dict(document_id=j,publication_time=documents[j]['publication_time'],role='publication_not_occurrence') for j in source_ids],missing_fields=missing,stage_links=links,progress_links=[p for p in graph['progress'] if p['new_node']==group['id'] or p['history_node']==group['id']],confirmed=False,verification='unverified',version_contract=VERSION)
        record['semantic_version']=old.fingerprint(base.semantic(record));dossiers.append(record)
    result=dict(dossiers=sorted(dossiers,key=lambda d:d['id']),unavailable=unavailable,mentions=len(members),groups=len(dossiers),progress_count=len(graph['progress']),confirmed=False,version_contract=VERSION)
    result['semantic_version']=old.fingerprint(base.semantic(result));return result


def transitions(previous,current):
    return [dict(old_group=a['id'],new_groups=[dict(id=b['id'],shared_members=sorted(set(a['members'])&set(b['members']))) for b in current['dossiers'] if set(a['members'])&set(b['members'])],inherits_conclusions=False) for a in previous['dossiers'] if not any(a['id']==b['id'] for b in current['dossiers'])]


class Workbench:
    _states=base.Pipeline._states
    list=base.Pipeline.list
    def __init__(self,path):self.path=Path(path);self.threads=[]
    @contextmanager
    def connect(self):
        c=sqlite3.connect('file:'+str(self.path.resolve())+'?mode=ro',uri=True);c.row_factory=sqlite3.Row
        try:yield c
        finally:c.close()
    def counts(self):return dict(new_model_attempts=0,scope='ENG025 pure derivation',encoding=0)
    def dossier_snapshot(self):
        with self.connect() as c:
            documents={r['id']:json.loads(r['document']) for r in c.execute('SELECT * FROM jobs')};states={j:self._states(c,j) for j in documents};families={}
            for r in c.execute('SELECT body FROM imports'):
                b=json.loads(r[0]);family=b.get('source_doc',{}).get('source_family')
                if family:families[base.digest(b['document'])]=family
            withdrawn={r[0] for r in c.execute('SELECT id FROM withdrawals')}
        return derive(documents,states,families,withdrawn)
    def get(self,jid):
        result=base.Pipeline.get(self,jid);snapshot=self.dossier_snapshot();result['event_dossiers']=[d for d in snapshot['dossiers'] if any(s['document_id']==jid for s in d['sources'])];result['dossier_version']=snapshot['semantic_version'];return result
    def submit(self,value,background=False):
        doc=base.documents.validate(value);jid=base.digest(doc)
        if not any(x['id']==jid for x in self.list()):raise ValueError('本单只读已有七篇事项档案，不接收新材料')
        return dict(id=jid,reused=True,status='read_only_current_dossier')
