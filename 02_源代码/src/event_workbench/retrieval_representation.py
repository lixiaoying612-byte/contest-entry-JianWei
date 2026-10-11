"""Shared source/query helpers; obsolete experiment execution is archived."""
import bisect
import re
from event_workbench import definition_retrieval as old


def pack(text,tokenizer,max_tokens=256,overlap=32,prefix=''):
    """Contiguous source spans; prefer the last fitting newline, overlap in tokens."""
    count=lambda s:len(tokenizer(s,add_special_tokens=True,truncation=False)['input_ids'])
    budget=max_tokens-count(prefix)
    if not 0<=overlap<budget-1:raise ValueError('prefix_leaves_insufficient_window_budget')
    if not text.strip():return []
    boundaries=[m.end() for m in re.finditer('\n',text)]
    result=[];start=0;previous_end=0
    while start<len(text):
        offsets=tokenizer(text[start:],add_special_tokens=False,return_offsets_mapping=True,truncation=False)['offset_mapping']
        end=len(text) if len(offsets)<=budget else start+offsets[budget-1][1]
        while end>start and count(prefix+text[start:end])>max_tokens:end-=1
        if end<=start:raise ValueError('tokenizer_cannot_advance')
        if end<len(text):
            pos=bisect.bisect_right(boundaries,end)-1
            if pos>=0 and boundaries[pos]>max(start,previous_end):end=boundaries[pos]
        source=text[start:end]
        result.append(dict(start=start,end=end,source_text=source,text=prefix+source,derived_prefix=prefix,tokens=count(prefix+source)))
        if end==len(text):break
        inside=tokenizer(source,add_special_tokens=False,return_offsets_mapping=True,truncation=False)['offset_mapping']
        step=inside[max(1,len(inside)-overlap)][0] if overlap and len(inside)>1 else end-start
        previous_end=end;start+=max(1,min(step,end-start))
    verify_spans(text,result,max_tokens)
    return result

def verify_spans(source,windows,max_tokens):
    end=0
    for w in windows:
        if not (0<=w['start']<w['end']<=len(source)) or w['start']>end:raise ValueError('source_coverage_gap')
        if w['source_text']!=source[w['start']:w['end']] or w['text']!=w['derived_prefix']+w['source_text']:raise ValueError('source_mapping_changed')
        if w['tokens']>max_tokens:raise ValueError('token_budget_exceeded')
        end=max(end,w['end'])
    if source.strip() and end!=len(source):raise ValueError('source_tail_missing')
