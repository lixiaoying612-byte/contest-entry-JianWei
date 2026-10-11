"""ENG053 lossless interval union over the existing verification material batch."""
import copy
import datetime as dt
from collections import defaultdict

VERSION='eng053-evidence-spans-v2-current-cutoff'


def collection_time(materials,previous=None):
    """Collection boundary from retained records, never wall-clock time."""
    rows=[];seen=set();times=[];unknown=False
    for m in materials:
        did=m['document']['local_id'];raw=m['document']['document'].get('publication_time')
        key=(did,raw)
        if key in seen:continue
        seen.add(key);row=dict(document_id=did,raw=raw)
        try:
            parsed=dt.datetime.fromisoformat(raw.replace('Z','+00:00'))
            naive=parsed.tzinfo is None
            if naive:parsed=parsed.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
            instant=parsed.astimezone(dt.timezone.utc);times.append(instant)
            row.update(utc=instant.isoformat(),timezone_basis='assumed_Asia/Shanghai_for_unzoned_local_records' if naive else 'explicit_offset')
        except (AttributeError,TypeError,ValueError):row['status']='unknown_publication_time';unknown=True
        rows.append(row)
    if previous:
        try:
            p=dt.datetime.fromisoformat(previous.replace('Z','+00:00'))
            if p.tzinfo is None:p=p.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
            times.append(p.astimezone(dt.timezone.utc))
        except (AttributeError,TypeError,ValueError):pass
    bound=max(times).isoformat() if times else None
    return dict(current_judgment_cutoff=None if unknown else bound,latest_known_material_time=bound,
        cutoff_basis='latest_retained_publication_or_previous_collection_boundary; not_wall_clock_or_event_time',
        timezone_policy='unzoned_local_records_assume_Asia/Shanghai; explicit_offsets_compared_in_UTC',
        time_status='unknown_material_time_no_definite_cutoff' if unknown else 'bounded_current_collection',records=rows)


def normalize(batch,lookup):
    out=copy.deepcopy(batch);groups=defaultdict(list);audit=[];sources={}
    for ref,m in out['mapping']['materials'].items():
        did=m['document']['local_id'];field=m['field'];start=m['start'];end=m['end']
        row=dict(original_ref=ref,document_id=did,field=field,start=start,end=end,text=m['text'],status='pending')
        try:
            if did not in sources:sources[did]=lookup(did)
            source=sources[did]
            if source!=m['document']['document']:raise ValueError('source_metadata_or_content_changed')
            if field not in ('title','text') or type(start) is not int or type(end) is not int:raise ValueError('invalid_position')
            text=source[field] or ''
            if not 0<=start<end<=len(text) or text[start:end]!=m['text']:raise ValueError('quote_position_mismatch')
            groups[(did,field,m['relation'])].append((start,end,ref,m))
            row['status']='validated'
        except (ValueError,KeyError,TypeError) as e:row.update(status='invalid',reason=str(e))
        audit.append(row)
    byref={r['original_ref']:r for r in audit};remap={};mapped={};wire=[]
    for (did,field,relation),entries in groups.items():
        unions=[]
        for start,end,ref,m in sorted(entries,key=lambda x:(x[0],x[1],x[2])):
            if unions and start<=unions[-1]['end']:
                unions[-1]['end']=max(end,unions[-1]['end']);unions[-1]['refs'].append(ref)
            else:unions.append(dict(start=start,end=end,refs=[ref],material=m))
        for u in unions:
            ref='E'+str(len(wire));text=sources[did][field][u['start']:u['end']];m=u['material']
            mapped[ref]=dict(m,start=u['start'],end=u['end'],text=text)
            doc=sources[did]
            wire.append(dict(id=ref,text=text,relation=relation,title=doc['title'],channel=doc['source'],publication_time=doc['publication_time']))
            for old in u['refs']:
                remap[old]=ref;row=byref[old]
                row.update(status='included',sent_ref=ref,relative_start=row['start']-u['start'],relative_end=row['end']-u['start'])
    def translate(refs):return list(dict.fromkeys(remap[r] for r in refs if r in remap))
    missing_refs=[]
    for row in out['content']['candidates']:
        missing_refs.extend(dict(mention=row['mention'],ref=r) for r in row['materials'] if r not in byref)
        row['materials']=translate(row['materials'])
    for mention in out['content']['mentions']:
        for row in mention['quote_index_to_materials']:
            missing_refs.extend(dict(mention=mention['id'],quote=row['index'],ref=r) for r in row['materials'] if r not in byref)
            row['materials']=translate(row['materials'])
    out['mapping']['materials']=mapped;out['content']['materials']=wire
    invalid=[r for r in audit if r['status']=='invalid']
    out['material_contract']=VERSION
    out['transmission_audit']=dict(contract=VERSION,status='blocked_invalid_spans' if invalid or missing_refs else 'complete',spans=audit,missing_candidate_refs=missing_refs,
        original_records=len(audit),sent_intervals=len(wire),source_identities=len({m['document']['local_id'] for m in mapped.values()}),
        original_characters=sum(len(r['text']) for r in audit),sent_characters=sum(len(m['text']) for m in wire),
        original_utf8_bytes=sum(len(r['text'].encode('utf-8')) for r in audit),sent_utf8_bytes=sum(len(m['text'].encode('utf-8')) for m in wire),
        invalid_count=len(invalid),independent_sources=None,ref_map=remap)
    # Any invalid record blocks the whole package; valid fragments are not
    # permission to silently send an incomplete package.
    out['retrieval_version']=dict(previous=batch['retrieval_version'],material_contract=VERSION)
    return out
