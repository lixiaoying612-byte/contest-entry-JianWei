"""Immutable user/public material intake into the existing replay corpus."""
import hashlib
import json
import re
from contextlib import closing
from datetime import datetime, timezone
from functools import lru_cache
from urllib.parse import urlsplit
from event_workbench import training_corpus as corpus
from event_workbench.replay_guard import fingerprints
from event_workbench.document_pipeline import validate_product_document

SCHEMA='''
CREATE TABLE IF NOT EXISTS news_workspaces(run_id TEXT PRIMARY KEY,kind TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS news_input_records(identity TEXT PRIMARY KEY,run_id TEXT,source_row TEXT UNIQUE,body TEXT);
'''


def register(app, run_id, kind='user'):
    if kind not in ('user','business','regression') or not run_id or run_id in ('may-replay','eng061'):
        raise ValueError('invalid_news_workspace')
    with closing(app.connect()) as c,c:
        c.executescript(SCHEMA)
        old=c.execute('SELECT kind FROM news_workspaces WHERE run_id=?',(run_id,)).fetchone()
        if old and old[0]!=kind:raise ValueError('workspace_kind_changed')
        if not old and c.execute('SELECT 1 FROM replay_runs WHERE id=?',(run_id,)).fetchone():raise ValueError('existing_run_is_not_user_workspace')
        c.execute('INSERT OR IGNORE INTO news_workspaces VALUES(?,?)',(run_id,kind))
        c.execute('INSERT OR IGNORE INTO replay_runs(id,title,cutoff) VALUES(?,?,?)',(run_id,{'user':'我的新闻工作区','business':'真实业务闭环','regression':'固定识别回归'}[kind],'9999-12-31T00:00:00+00:00'))


def is_workspace(c, run_id):
    return bool(c.execute("SELECT 1 FROM sqlite_master WHERE name='news_workspaces'").fetchone() and c.execute('SELECT 1 FROM news_workspaces WHERE run_id=?',(run_id,)).fetchone())


@lru_cache(maxsize=2)
def _protected(stamp):
    base=corpus.ROOT/'artifacts/eng063/runtime'
    path=base/'evaluation_hashes.json'
    with closing(corpus.connect(base/'workbench.sqlite3',True)) as c:
        expected=corpus.metadata(c).get('guard',{}).get('sha256')
    if not expected or corpus.filehash(path)!=expected:raise ValueError('protection_fingerprint_changed')
    data=json.loads(path.read_text('utf-8'));hashes={k:set(v) for k,v in data['hashes'].items()};whitespace=set()
    groups=corpus.ROOT/'artifacts/eng038/partition/v1/groups.sqlite'
    if corpus.filehash(groups)!='3b8deb06483b309b807a3ad273598ea595f05454b587ffc209f7c93e081a6011':raise ValueError('protection_partition_changed')
    with closing(corpus.connect(groups,True)) as c:
        # retrieval_blocked also marks ordinary development rows not selected for
        # retrieval; it is not a test/holdout designation.
        for row in c.execute("SELECT n.features_json FROM nodes n JOIN groups g USING(group_id) WHERE g.pool='internal_holdout_candidate' OR g.purpose IN ('internal_holdout_candidate','excluded')"):
            fp=json.loads(row[0]).get('fingerprints',{})
            for key in ('content','body'):
                if fp.get(key):hashes[key].add(fp[key])
            if fp.get('whitespace'):whitespace.add(fp['whitespace'])
    return hashes,whitespace


def guard(document):
    from event_workbench import competition_data_guard as extra
    packaged=extra.packaged_fingerprints()
    if packaged:hashes,whitespace=packaged
    else:
        paths=[corpus.ROOT/'artifacts/eng063/runtime/evaluation_hashes.json',corpus.ROOT/'artifacts/eng038/partition/v1/groups.sqlite']
        stamp=tuple((str(p),p.stat().st_size,p.stat().st_mtime_ns) for p in paths)
        hashes,whitespace=_protected(stamp)
    fp=fingerprints(document['title'],document['text'])
    if any(v in hashes[k] for k,v in fp.items()) or hashlib.sha256(''.join(document['text'].split()).encode()).hexdigest() in whitespace:
        raise ValueError('当前入口无法导入这份材料，请换一篇新闻。')
    whitespace_sha=hashlib.sha256(''.join(document['text'].split()).encode()).hexdigest()
    if any(any(row.get(k)==v for k,v in fp.items()) or row.get('whitespace')==whitespace_sha for row in extra.evaluation_fingerprints()):
        raise ValueError('当前入口无法导入这份材料，请换一篇新闻。')


def parse_publication_time(value):
    """Parse the supported input spellings without replacing the saved original."""
    try:return datetime.fromisoformat(value.replace('Z','+00:00'))
    except ValueError:
        for fmt in ('%Y/%m/%d %H:%M:%S','%Y/%m/%d %H:%M','%Y/%m/%d','%Y-%m-%d %H:%M:%S','%Y-%m-%d %H:%M','%Y-%m-%d'):
            try:return datetime.strptime(value.strip(),fmt)
            except ValueError:pass
    raise ValueError('发布时间格式或日期无效，例如 2026/5/28 21:15:00、2026-05-28 或带时区的 ISO 时间；未知可留空')


def validate(payload,check_guard=guard):
    allowed={'run','revision','title','text','source','source_url','publication_time','related_event_id'}
    if not isinstance(payload,dict) or set(payload)-allowed:raise ValueError('新闻字段不合法')
    run=payload.get('run');doc=validate_product_document({k:None if payload.get(k)=='' else payload.get(k) for k in ('title','text','source','publication_time')})
    if not doc['text'] or not doc['text'].strip():raise ValueError('正文不能为空')
    for key in ('source_url','related_event_id'):
        if payload.get(key) is not None and not isinstance(payload[key],str):raise ValueError('来源链接及关联身份必须为文本')
    url=payload.get('source_url') or None
    if url:
        parsed=urlsplit(url)
        if len(url)>2048 or parsed.scheme not in ('http','https') or not parsed.hostname or parsed.username or parsed.password or any(ord(x)<33 for x in url):raise ValueError('仅允许有效的 HTTP/HTTPS 原文链接')
    published=doc['publication_time'];precision='unknown'
    if published:
        parsed=parse_publication_time(published)
        precision='day' if re.fullmatch(r'\d{4}[-/]\d{1,2}[-/]\d{1,2}',published.strip()) else ('datetime' if parsed.tzinfo else 'datetime_timezone_unknown')
    check_guard(doc)
    return dict(document=doc,source_url=url,related_event_id=payload.get('related_event_id') or None),precision


def legacy_material(app,c,run,document):
    """Reuse scoped legacy evidence only when body, publisher and time are exact."""
    from event_workbench.product_research import publication_gaps
    rows=c.execute('''SELECT r.row_key,t.title FROM replay_scope_rows s
        JOIN source_rows r ON r.row_key=s.source_row JOIN replay_rows p ON p.row_id=r.id
        JOIN documents d ON d.id=r.document_id JOIN contents t ON t.id=d.content_id
        WHERE s.run_id=? AND p.guard='allowed' AND p.available_at<?
        AND t.body=? AND coalesce(r.source_raw,'')=? AND coalesce(r.published_raw,'')=?
        ORDER BY r.id''',(run['id'],run['cutoff'],document['text'],document['source'] or '',document['publication_time'] or '')).fetchall()
    visible=[json.loads(r['body']) for r in app.visible_event_rows(c,run,current_only=True,events_only=True)]
    for row in rows:
        events=[e for e in visible if e.get('record_kind')=='event'
            and any(m.get('source_row')==row['row_key'] for m in e.get('evidence_sources',{}).get('members',[]))]
        from event_workbench.news_service import complete_result
        native=any(not e.get('case_id') for e in events)
        original=app.document(run['id'],row['row_key']) if native else None
        complete=not native or complete_result(c,run['id'],corpus.digest({k:original[k] for k in ('title','text','source','publication_time')}),visible)
        if events and complete and all(not e.get('identity_update_pending') and not e.get('publication_update_pending') and not publication_gaps(e)
            and all(not d.get('stale_assessment') and d.get('impact_event_version',e['event_version'])==e['event_version']
                for d in e.get('warning_reasons',{}).get('decisions',[])) for e in events):
            return dict(material_id=row['row_key'],exact_title=(row['title'] or None)==document['title'],events=events)
    return None


def save(app,payload,*,check_guard=guard,origin=None):
    normalized,precision=validate(payload,check_guard)
    run=payload.get('run');doc=normalized['document'];published=doc['publication_time']
    identity='input:'+corpus.digest([run,normalized])
    with closing(app.connect()) as c,c:
        c.execute('BEGIN IMMEDIATE')
        current=app.run(c,run)
        if not is_workspace(c,run):raise ValueError('本运行不接受手工新闻')
        old=c.execute('SELECT source_row FROM news_input_records WHERE identity=? AND run_id=?',(identity,run)).fetchone()
        if old:return dict(material_id=old[0],duplicate=True,revision=current['revision'])
        legacy=legacy_material(app,c,current,doc) if not normalized['source_url'] and not normalized['related_event_id'] else None
        if legacy and legacy['exact_title']:return dict(material_id=legacy['material_id'],duplicate=True,revision=current['revision'],historical_result_reused=True)
        if payload.get('revision')!=current['revision']:raise ValueError('工作区版本已变化，请刷新后重试；正文未丢弃')
        related=normalized['related_event_id']
        if related and not c.execute('SELECT 1 FROM replay_events WHERE id=? AND run_id=?',(related,run)).fetchone():raise ValueError('关联事件不属于本工作区')
        received=datetime.now(timezone.utc).isoformat(timespec='microseconds')
        file_sha=corpus.digest(['news_input',run]);sheet='inputs'
        c.execute('INSERT OR IGNORE INTO files VALUES(?,?)',(file_sha,'手工/公开材料：'+run))
        number=c.execute('SELECT coalesce(max(row_number),0)+1 FROM source_rows WHERE file_sha=?',(file_sha,)).fetchone()[0]
        values=dict(article_file_id=identity,article_title=doc['title'],content=doc['text'],article_source=doc['source'],article_publish_time=published)
        header=dict(enumerate(sorted(corpus.CORE),1))
        corpus.insert_row(c,file_sha,sheet,number,header,{i:dict(type='inlineStr',value=values[k],formula=False) for i,k in header.items()},'1900')
        row=c.execute('SELECT id,row_key FROM source_rows WHERE file_sha=? AND sheet=? AND row_number=?',(file_sha,sheet,number)).fetchone()
        # Preserve supplied time spelling and precision; never replace it with receipt time.
        c.execute('UPDATE source_rows SET published_iso=? WHERE id=?',(published,row['id']))
        kind=(origin or {}).get('source_kind','user_input')
        record=dict(normalized,source_kind=kind,received_at=received,precision=precision,body_sha256=hashlib.sha256(doc['text'].encode()).hexdigest(),source_check=origin or dict(status='not_checked',nature='user_provided',independence='not_established'))
        c.execute('INSERT INTO replay_rows VALUES(?,?,?,?,?,?)',(row['id'],kind,received,precision,'allowed',len(doc['text'])))
        c.execute('INSERT INTO news_input_records VALUES(?,?,?,?)',(identity,run,row['row_key'],corpus.js(record)))
        c.execute('INSERT INTO replay_scope_rows VALUES(?,?,?)',(run,row['row_key'],corpus.js(dict(source_row=row['row_key'],available_at=received,precision=precision,body_sha256=record['body_sha256']))))
        c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run,))
        return dict(material_id=row['row_key'],duplicate=False,revision=current['revision']+1,received_at=received)


def metadata(c,run,source_row):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE name='news_input_records'").fetchone():return {}
    row=c.execute('SELECT body FROM news_input_records WHERE run_id=? AND source_row=?',(run,source_row)).fetchone()
    if not row:return {}
    value=json.loads(row[0]);value.pop('document',None)
    return value
