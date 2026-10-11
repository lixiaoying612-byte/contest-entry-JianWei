"""ENG040 lexical-only document candidates; no event carriers or encoders."""
import hashlib,json,math,re
from collections import Counter
from pathlib import Path
from event_workbench.compact_model_io import terms
ROOT=Path(__file__).resolve().parents[2]
METHOD='eng040-lexical-only-top5'
OFFICIAL_SHA='db0c4d922c07c44aee2c450c1551359f397c5942d78d8e538e5924b9ee3040d3'


def load_definitions():
    raw=(ROOT/'engineering/contracts/eng007_v2/official_definitions.json').read_bytes()
    if hashlib.sha256(raw).hexdigest()!=OFFICIAL_SHA:raise ValueError('catalog_drift')
    definitions=json.loads(raw)
    if len(definitions)!=120 or len({d['definition_id'] for d in definitions})!=120:raise ValueError('catalog_count')
    return definitions
def rank_window(query,definitions):
    # Frozen ENG009 formula and raw-score ordering, rounding only after sorting.
    texts=[' '.join(str(d[key]) for key in ['target_name','target_code','event_name','description']) for d in definitions]
    bags=[terms(t) for t in texts];df=Counter(t for bag in bags for t in bag)
    idf={t:math.log((1+len(bags))/(1+n))+1 for t,n in df.items()}
    q=terms(query);rank=[]
    for d,bag in zip(definitions,bags):
        shared=set(q)&set(bag)
        score=sum(idf[t]**2 for t in shared)/math.sqrt(max(1,len(q))*max(1,sum(idf[t]**2 for t in bag)))
        target=any(value and value.casefold() in query.casefold() for value in [d['target_name'],d['target_code']])
        if score>0 or target:rank.append((score+2*target,d['definition_id'],target))
    rank.sort(key=lambda x:(-x[0],x[1]))
    return [dict(definition_id=x[1],score=round(x[0],6),target_text_hit=x[2]) for x in rank]

TOPIC_GENERIC={'市场','发布','相关','变化','进展','调整','支持','重大','公司','行业','业务','企业','信息','动态','发展'}


def scoped_ranking(query,definitions):
    """Conservative lexical hints, not entity resolution or an event classifier."""
    sentences=[(m.start(),m.end(),m.group(),set(terms(m.group()))) for m in re.finditer(r'[^。！？!?；;\n]+',query)]
    by={d['definition_id']:d for d in definitions};qualified=[];weak=[]
    for row in rank_window(query,definitions):
        d=by[row['definition_id']];topic=set(terms(d['event_name']))-TOPIC_GENERIC
        hints=[]
        for start,end,text,tokens in sentences:
            hits=sorted(topic & tokens)
            if not hits:continue
            local_target=any(v and (v.casefold() in tokens if k=='target_code' else v.casefold() in text.casefold()) for k in ('target_name','target_code') for v in [d[k]])
            coverage=len(hits)/max(1,len(topic))
            hints.append(dict(start=start,end=end,topic_terms=hits,topic_coverage=coverage,local_target_and_topic=local_target,
                              usable=local_target or (len(hits)>=2 and coverage>=0.5)))
        best=max(hints,key=lambda h:(h['usable'],h['local_target_and_topic'],h['topic_coverage']),default=None)
        item=dict(row,lexical_score=row['score'],scope_hint=best)
        if best and best['usable']:
            item['score']=round(row['score']-2*row['target_text_hit']+best['topic_coverage']+2*best['local_target_and_topic'],6)
            qualified.append(item)
        else:
            item['reason']='topic_hint_needs_context' if best else ('object_without_topic' if row['target_text_hit'] else 'generic_or_description_overlap_only')
            weak.append(item)
    qualified.sort(key=lambda x:(-x['score'],x['definition_id']))
    return qualified,weak


def plan_candidates(plan):
    """Reuse lexical scoring on complete planned spans; no tokenizer or encoder load."""
    from event_workbench.document_processing_plan import audit_plan
    audit_plan(plan)
    definitions=load_definitions();by={d['definition_id']:d for d in definitions}
    doc=plan['document'];title=doc['title'] or ''
    spans=[dict(field='title',start=0,end=len(title),text=title)]
    spans.extend(dict(field='text',start=b['core']['start'],end=b['core']['end'],text=b['core']['text']) for b in plan['blocks'])
    windows=[];matches={}
    # ponytail: scan the 120 definitions per block; measure before adding an index.
    for span in spans:
        ranked,weak=scoped_ranking(span['text'],definitions)
        selected=ranked[:5];wi=len(windows)
        for row in selected:
            d=by[row['definition_id']]
            shared=sorted(set(terms(span['text'])) & set(terms(' '.join(str(d[k]) for k in ('target_name','target_code','event_name','description')))))
            hint=dict(row['scope_hint']);hint.update(start=span['start']+hint['start'],end=span['start']+hint['end'])
            matches.setdefault(row['definition_id'],[]).append(dict(window_index=wi,score=row['score'],lexical_score=row['lexical_score'],target_text_hit=row['target_text_hit'],shared_terms=shared,scope_hint=hint))
        windows.append(dict(field=span['field'],start=span['start'],end=span['end'],positive_count=len(ranked)+len(weak),qualified_count=len(ranked),selected=selected,weak_matches=weak,truncated=len(ranked)>len(selected)))
    ids=sorted(matches,key=lambda did:(-max(m['score'] for m in matches[did]),did))
    target_hit=any(m['target_text_hit'] for ms in matches.values() for m in ms)
    unresolved='weak_lexical_matches_unresolved' if any(w['weak_matches'] for w in windows) else 'no_positive_lexical_candidates_unresolved'
    return dict(method='product-plan-lexical-v2',catalog_sha256=OFFICIAL_SHA,
                plan_id=plan['id'],windows=windows,
                candidates=[dict(definition=by[did],matches=matches[did]) for did in ids],
                status=('candidate_review_required' if target_hit else 'lexical_candidates_target_unresolved') if ids else unresolved,
                target_link_verified=False,
                candidate_use='diagnostic_only_not_validated_for_routing; retain weak matches for scope review',
                selection='union of at most top5 qualified hints per title/core span; no final union truncation; weak matches remain diagnostic',
                hint_policy=dict(generic_topic_terms=sorted(TOPIC_GENERIC),object_route='literal object and a non-generic event-name term in the same sentence',unlinked_route='at least 2 event-name terms and coverage >= 0.5 in one sentence',scope='co-occurrence hint only; no subject-role resolution; no hard exclusion of documents'),
                model_calls=0,embedding_calls=0,semantic_match_verified=False,
                route='needs_extraction_or_scope_review',
                limitations=['未解析别名或间接对象关系；低分和空结果不证明范围外',
                             '句内共现不证明主体角色；仅定义正文中的语义线索可能降为弱匹配，必须复核',
                             '逐块文字匹配，跨块语义未判断；未运行BM25或BGE对照'])


