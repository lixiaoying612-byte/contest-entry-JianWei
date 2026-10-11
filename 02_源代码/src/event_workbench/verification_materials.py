"""Thin Top3/exclude-own adapter over the existing evidence retriever."""
import json
from event_workbench import evidence_retrieval as er
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
read=lambda p:json.loads(p.read_text(encoding='utf-8'))
VERSION='eng020-materials-v1'

def original_corpus():
    manifest=read(ROOT/'engineering/contracts/eng013/input_manifest.json');docs=[]
    family=read(ROOT/'engineering/contracts/eng012/public_sources_v1.json')['source_family']
    for entry in manifest['documents']:
        raw=(ROOT/entry['path']).read_bytes()
        if er.old.sha(raw)!=entry['file_sha256']:raise ValueError('corpus_file_changed')
        doc=er.old.selected_json_field(raw,entry['field'])
        if er.old.fingerprint(doc)!=entry['document_sha256']:raise ValueError('corpus_original_changed')
        known=entry['local_id'] in {x['local_id'] for x in manifest['documents'][-4:]}
        docs.append(dict(local_id=entry['local_id'],document=doc,source_family=family if known else None,source_family_basis='frozen_BYD_family' if known else 'unknown',url=doc['source'] if (doc['source'] or '').startswith('http') else None))
    old=read(ROOT/'artifacts/eng013/runs/r1/windows.json');corpus=[]
    for i,w in enumerate(old['query_windows']):
        d=docs[w['document_index']]
        if d['document'][w['field']][w['start']:w['end']]!=w['source_text']:raise ValueError('corpus_span_changed')
        corpus.append(dict(w,document_id=d['local_id'],parent_vector_row=i))
    return docs,corpus


def batch_input(job,retrieved):
    """Provider gets short IDs/text only; all document hashes/offsets stay local."""
    prepared=job['prepared'];document=job['document'];origin=next((d for d in retrieved['documents'] if d['document']==document),dict(local_id=job['id'],document=document,source_family=None,url=None))
    materials={};wire=[]
    positions={p['ref']:p for p in prepared['source_positions']}
    for ref,text in prepared['representation']['u']:
        pos=positions[ref];field='title' if pos['section']=='title' else 'text'
        materials[ref]=dict(document=origin,field=field,start=pos['start'],end=pos['end'],text=text,relation='own_document')
        wire.append(dict(id=ref,text=text,relation='own_document',channel=document['source'],publication_time=document['publication_time']))
    found={};mentions=[];bindings={};candidate_rows=[]
    for item in (x for x in retrieved['packages'] if x['job']==job['id']):
        event=item['event'];i=len(mentions);bindings[str(i)]=dict(original_index=event['original_index'],event=event)
        facts=event['facts'];projection={k:v for k,v in facts.items() if k!='evidence_quotes'}
        quote_map=[dict(index=qi,materials=[ref for ref,m in materials.items() if m['relation']=='own_document' and q['quote'] in m['text']]) for qi,q in enumerate(facts.get('evidence_quotes',[]))]
        mentions.append(dict(id=i,original=event.get('original_event'),projection=projection,quote_index_to_materials=quote_map,field_warnings=event['field_warnings']))
        ids=[]
        for c in item['material_package']['candidates']:
            w=c['window'];key=(w['document_id'],w['field'],w['start'],w['end'])
            if key not in found:
                ref='M'+str(len(found)+1);found[key]=ref;source=next(d for d in retrieved['documents'] if d['local_id']==w['document_id'])
                materials[ref]=dict(document=source,field=w['field'],start=w['start'],end=w['end'],text=w['source_text'],relation=c['source_relation'])
                wire.append(dict(id=ref,text=w['source_text'],title=source['document']['title'],channel=source['document']['source'],publication_time=source['document']['publication_time'],relation=c['source_relation']))
            ids.append(found[key])
        candidate_rows.append(dict(mention=i,materials=ids))
    return dict(content=dict(mentions=mentions,materials=wire,candidates=candidate_rows,mode='retrospective_current_collected_materials'),mapping=dict(document=origin,mentions=bindings,materials=materials),retrieval_version=retrieved['version'])
