"""ENG009 local-only compact request preparation; no transport or credential API."""
import copy
import math
import re
from collections import Counter

from event_workbench import mention_matching_pipeline as checks

v2,common=checks.v2,checks.common
ROOT=checks.ROOT
CONTRACT=ROOT/'engineering/contracts/eng009'
VERSION='eng009-compact-v1'
POL={'+':'affirmed','-':'negated','?':'unclear'}
MODE=dict(zip('apfcqu',['asserted','planned','forecast','conditional','question','unclear']))
CONTEXT={'f':'focus','b':'background','u':'unclear'}
TIME_ROLE=dict(zip('oedvu',['occurrence','effective','deadline','observation','unspecified']))
PRECISION=dict(zip(['y','m','d','dt','i','r','u'],['year','month','day','datetime','interval','relative','unknown']))
BASIS=dict(zip('edpu',['explicit','document_context','publication_time','unresolved']))
DIRECTION={'+':'positive','-':'negative','0':'neutral','m':'mixed','?':'unclear'}


def source_representation(document):
    units=[];positions=[]
    for key,prefix in [('title','T'),('text','B')]:
        text=document[key] or '';start=0;i=0;n=0
        while i<len(text):
            if text[i] in '。！？\r\n':
                i+=1
                while i<len(text) and (text[i].isspace() or text[i] in '。！？”’」』'):i+=1
                n+=1;ref=f'{prefix}{n}';units.append([ref,text[start:i]])
                positions.append(dict(ref=ref,section='title' if prefix=='T' else 'body',start=start,end=i));start=i
            else:i+=1
        if start<len(text):
            n+=1;ref=f'{prefix}{n}';units.append([ref,text[start:]])
            positions.append(dict(ref=ref,section='title' if prefix=='T' else 'body',start=start,end=len(text)))
    rep=dict(meta=dict(p=document['publication_time'],s=document['source'],null=[prefix for key,prefix in [('title','T'),('text','B')] if document[key] is None]),u=units)
    if reconstruct(rep)!=document:raise ValueError('source_roundtrip_failed')
    return rep,positions


def reconstruct(rep):
    return dict(title=None if 'T' in rep['meta']['null'] else ''.join(t for r,t in rep['u'] if r.startswith('T')),
        text=None if 'B' in rep['meta']['null'] else ''.join(t for r,t in rep['u'] if r.startswith('B')),
        publication_time=rep['meta']['p'],source=rep['meta']['s'])


def structural_errors(value,schema,path='$'):
    """Existing checker plus tuple keywords used by this compact contract only."""
    errors=v2.schema_errors(value,schema,path)
    def extra(v,s,p):
        if isinstance(v,list):
            if len(v)>s.get('maxItems',len(v)):errors.append(common.issue(p,'max_items','紧凑元组长度超限。'))
            for i,child in enumerate(s.get('prefixItems',[])):
                if i<len(v):errors.extend(structural_errors(v[i],child,f'{p}[{i}]'))
            if isinstance(s.get('items'),dict):
                for i,item in enumerate(v):extra(item,s['items'],f'{p}[{i}]')
        if isinstance(v,dict):
            for key,child in s.get('properties',{}).items():
                if key in v:extra(v[key],child,p+'.'+key)
    extra(value,schema,path)
    return errors


def parse_wire(raw,stage):
    def finite(v):
        if isinstance(v,float) and not math.isfinite(v):raise ValueError('nonfinite')
        if isinstance(v,dict):
            for child in v.values():finite(child)
        if isinstance(v,list):
            for child in v:finite(child)
    try:
        value=common.strict_json(raw);finite(value)
        return value,structural_errors(value,v2.read_json(CONTRACT/f'{stage.lower()}_wire.schema.json'))
    except (ValueError,TypeError,RecursionError):return None,[common.issue('$','invalid_json','保留原答，不修补。')]


def decode_a(raw,document,document_key=None):
    wire,errors=parse_wire(raw,'A');rep,_=source_representation(document)
    result=dict(compact_parsed=wire,issues=errors,projection=None,binding_map=[],semantic_review_status='pending',
        adapter_version=VERSION,document_version=v2.digest(document),matching_status='not_run',mention_records=[])
    if errors:return result
    units=dict(rep['u']);events=[]
    for i,e in enumerate(wire['e']):
        groups=[e['q'],e['st'][5]]+[x[-1] for g in ['p','t','d'] for x in e.get(g,[])]+[[x[4]] for x in e.get('im',[])]
        selected={r for refs in groups for r in refs}
        for ref in selected-units.keys():errors.append(common.issue(f'$.e[{i}]','unknown_source_ref','未知来源，不补找、不删坏字段。'))
        if errors:continue
        ordered=[ref for ref in units if ref in selected];idx={r:j for j,r in enumerate(ordered)}
        def refs(values):return [idx[r] for r in values]
        def quote(ref):return dict(section='title' if ref.startswith('T') else 'body',quote=units[ref])
        st=e['st']
        event=dict(summary=e['s'],participants=[dict(name=x[0],role=x[1],evidence_refs=refs(x[2])) for x in e.get('p',[])],
            status=dict(phase=st[0],polarity=POL[st[1]],mode=MODE[st[2]],attributed_to=st[3],context=CONTEXT[st[4]],evidence_refs=refs(st[5])),
            times=[dict(role=TIME_ROLE[x[0]],text=x[1],normalized=x[2],precision=PRECISION[x[3]],basis=BASIS[x[4]],evidence_refs=refs(x[5])) for x in e.get('t',[])],
            details=[dict(name=x[0],value_text=x[1],evidence_refs=refs(x[2])) for x in e.get('d',[])],matches=[],
            impact_mentions=[dict(target_name=x[0],direction=DIRECTION[x[1]],mode=MODE[x[2]],attributed_to=x[3],quote=quote(x[4])) for x in e.get('im',[])],
            evidence_quotes=[quote(r) for r in ordered],uncertainties=e.get('u',[]))
        events.append(event);result['binding_map'].append(dict(event_index=i,source_refs=ordered,summary_evidence_refs=refs(e['q'])))
    if errors:return result  # No partial projection silently drops an invalid item.
    projection=dict(events=events,document_notes=[dict(code=x[0],message=x[1]) for x in wire.get('n',[])])
    check=checks.inspect_a(common.json_text(projection),document)
    result.update(projection=projection,checks=check,ready_for_review=check['schema_status']=='passed' and check['evidence_status']=='passed')
    result['mention_records']=[dict(mention_id=v2.digest([document_key,result['document_version'],VERSION,v2.digest(wire),i]),
        event_index=i,payload=copy.deepcopy(e),matching_status='not_run',semantic_review_status='pending') for i,e in enumerate(events)]
    return result


def compact_existing_a(a_result,document):
    """Diagnostic serialization of frozen A, never repairs its bad refs or original words."""
    rep,positions=source_representation(document);out=[];lineage=[]
    def encode(table,value):return {v:k for k,v in table.items()}[value]
    for i,e in enumerate(a_result['parsed_response']['events']):
        faults=[];qmap={}
        def original_quote(q,path):
            section=q['section'];text=document['title' if section=='title' else 'text'] or ''
            candidates=common.locate_quote(text,q['quote'])['candidates']
            if not candidates:faults.append([path,'missing_quote']);return []
            refs=[p['ref'] for p in positions if p['section']==section and any(p['start']<c['end'] and p['end']>c['start'] for c in candidates)]
            if len(candidates)>1:faults.append([path,'ambiguous_quote'])
            lineage.append(dict(mention=i,field=path,original_quote=q,positions=candidates,source_refs=refs))
            return refs
        for j,q in enumerate(e['evidence_quotes']):qmap[j]=original_quote(q,f'q.{j}')
        def remap(refs,path):
            selected=[]
            for ref in refs:
                if ref not in qmap:faults.append([path,'invalid_ref:'+str(ref)])
                else:selected.extend(qmap[ref])
            return list(dict.fromkeys(selected))
        st=e['status']
        c=dict(i=i,s=e['summary'],st=[st['phase'],encode(POL,st['polarity']),encode(MODE,st['mode']),st['attributed_to'],encode(CONTEXT,st['context']),remap(st['evidence_refs'],'st')],
            q=list(dict.fromkeys(r for refs in qmap.values() for r in refs)),x=faults)
        for group in ['participants','times','details']:
            values=[]
            for j,x in enumerate(e[group]):
                refs=remap(x['evidence_refs'],f'{group}.{j}')
                if group=='participants':values.append([x['name'],x['role'],refs])
                elif group=='times':values.append([encode(TIME_ROLE,x['role']),x['text'],x['normalized'],encode(PRECISION,x['precision']),encode(BASIS,x['basis']),refs])
                else:values.append([x['name'],x['value_text'],refs])
            if values:c[{'participants':'p','times':'t','details':'d'}[group]]=values
        if e['impact_mentions']:
            c['im']=[[x['target_name'],encode(DIRECTION,x['direction']),encode(MODE,x['mode']),x['attributed_to'],original_quote(x['quote'],f'im.{j}')] for j,x in enumerate(e['impact_mentions'])]
        if e['uncertainties']:c['u']=e['uncertainties']
        # Keep original selected-field check defects, without repeating long display messages.
        for problem in a_result['evidence_issues']:
            if problem['field'].startswith(f'$.events[{i}].'):faults.append([problem['field'].split('].',1)[1],problem['code']])
        out.append(c)
    return out,lineage


def terms(text):
    text=text.casefold();words=re.findall(r'[a-z0-9]+',text)
    for run in re.findall(r'[\u3400-\u9fff]+',text):words.extend(run[j:j+2] for j in range(len(run)-1))
    return Counter(words)




def request(stage,content):
    prompt=(ROOT/f'examples/eng009/prompt_{stage.lower()}.txt').read_text(encoding='utf-8')
    return dict(model='deepseek-v4-pro',thinking={'type':'disabled'},stream=False,response_format={'type':'json_object'},
        max_tokens=2048 if stage=='A' else 512,messages=[dict(role='system',content=prompt),dict(role='user',content=common.json_text(content,separators=(',',':')))])




def decode_b(raw,content):
    wire,errors=parse_wire(raw,'B');result=dict(compact_parsed=wire,issues=errors,matches=[],unmatched=[],semantic_review_status='pending')
    if errors:return result
    mentions={e['i']:e for e in content['a']};defs={d['definition_id']:d for d in content['c']};units=dict(content['u'])
    found=set();empty=set();pairs=set()
    for row in wire['m']:
        i,did,decision,basis,refs,dq,reason=row
        if i not in mentions or did not in defs or any(r not in units for r in refs):errors.append(common.issue('m','unknown_reference','提及、候选或来源未知。'));continue
        if (i,did) in pairs:errors.append(common.issue('m','duplicate_match','重复匹配。'))
        pairs.add((i,did));found.add(i);d=defs[did]
        if basis=='d':
            if dq is not None or not any(t and t in units[r] for t in [d['target_name'],d['target_code']] for r in refs):errors.append(common.issue('m','target_evidence_invalid','直接目标证据不符。'))
        elif dq is None or not common.locate_quote(d['description'],dq)['candidates']:errors.append(common.issue('m','definition_quote_invalid','间接定义引文不符。'))
        result['matches'].append(dict(mention_ref=i,definition=copy.deepcopy(d),decision={'m':'matched','p':'possible'}[decision],
            basis={'d':'direct_text','i':'definition_mediated'}[basis],source_refs=refs,definition_quote=dq,reason=reason,
            a_check_issues=mentions[i]['x'],confirmed=False,semantic_review_status='pending'))
    for i,code in wire['n']:
        if i not in mentions or i in empty or i in found:errors.append(common.issue('n','coverage_conflict','空匹配范围冲突。'))
        messages={'no_candidate':'本地未召回候选，仍待核对','no_target':'尚未建立监测目标关系','insufficient':'现有依据不足','not_applicable':'候选事项适用性未建立'}
        empty.add(i);result['unmatched'].append(dict(mention_ref=i,code=code,message=messages[code],status='unconfirmed'))
    if found|empty!=set(mentions):errors.append(common.issue('$','mention_coverage','必须交代每条提及。'))
    result.update(adapter_version=VERSION,catalog_namespace='official',candidate_version=v2.digest(content['c']))
    return result
