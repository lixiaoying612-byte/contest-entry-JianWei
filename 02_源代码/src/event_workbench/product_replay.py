"""Bounded real-data workbench: ingestion and saved results, no model execution."""
import json
from contextlib import closing, nullcontext
from datetime import datetime, timedelta, timezone
from decimal import Decimal, ROUND_HALF_UP
from pathlib import Path
from event_workbench import training_corpus as corpus
from event_workbench.product_research import SCHEMA as RESEARCH_SCHEMA

ROOT = corpus.ROOT
DEFAULT = ROOT / 'artifacts/eng063/runtime'
TZ = timezone(timedelta(hours=8))
SOURCES = [('train', '官方数据/news_without_tags_train.xlsx', '398a97a849ea4aecec25c9762d9c1eedd7cb9bce64851ac7ca84beb7443f3302'),
           ('test', '官方数据/news_without_tags_test.xlsx', '73f494d0b5ff309bd74da503b1527437fe948969d4a9f685ab8a55cc447664db')]
SCHEMA = """
CREATE TABLE IF NOT EXISTS replay_rows(row_id INTEGER PRIMARY KEY REFERENCES source_rows(id),
 split TEXT, available_at TEXT, precision TEXT, guard TEXT, body_length INTEGER);
CREATE INDEX IF NOT EXISTS replay_visible ON replay_rows(guard,available_at);
CREATE TABLE IF NOT EXISTS replay_runs(id TEXT PRIMARY KEY, title TEXT, cutoff TEXT, revision INTEGER DEFAULT 0);
CREATE TABLE IF NOT EXISTS replay_documents(id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS replay_events(id TEXT PRIMARY KEY, run_id TEXT, document_id TEXT, available_at TEXT, body TEXT);
CREATE TABLE IF NOT EXISTS replay_actions(run_id TEXT, kind TEXT, target TEXT, body TEXT,
 PRIMARY KEY(run_id,kind,target));
CREATE TABLE IF NOT EXISTS replay_operations(id INTEGER PRIMARY KEY, run_id TEXT, kind TEXT, target TEXT, body TEXT, operated_at TEXT);
CREATE TABLE IF NOT EXISTS replay_catalog(id TEXT PRIMARY KEY, body TEXT);
CREATE TABLE IF NOT EXISTS replay_extraction_reviews(id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
 source_row TEXT NOT NULL, document_sha TEXT NOT NULL, body TEXT NOT NULL, saved_at TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS replay_review_source ON replay_extraction_reviews(run_id,source_row);
CREATE TABLE IF NOT EXISTS replay_scope_rows(run_id TEXT,source_row TEXT,body TEXT,
 PRIMARY KEY(run_id,source_row));
"""


def published_notices(event):
    return [d for d in event.get('warning_reasons',{}).get('decisions',[])
            if d.get('id') and type(d.get('version')) is int and d['version']>0 and type(d.get('active')) is bool]


def iso(value):
    dt = datetime.fromisoformat(value)
    if dt.tzinfo is None:
        raise ValueError('时间必须带时区')
    return dt.astimezone(timezone.utc).isoformat(timespec='microseconds')


def publication(cell, system):
    if cell.get('formula') or cell.get('value') in (None, ''):
        return None, 'unknown'
    try:
        if cell['type'] == 'n':
            # Excel serials have floating storage noise; retain raw value, use second proxy.
            seconds = int((Decimal(cell['value']) * 86400).to_integral_value(rounding=ROUND_HALF_UP))
            base = datetime(1904, 1, 1) if system == '1904' else datetime(1899, 12, 30)
            dt = base + timedelta(seconds=seconds)
            precision = 'excel_second_proxy' if Decimal(cell['value']) % 1 else 'day_end_proxy'
        else:
            dt = datetime.fromisoformat(cell['value'])
            precision = 'text_time' if ('T' in cell['value'] or ' ' in cell['value']) else 'day_end_proxy'
        if precision == 'day_end_proxy':
            dt = dt.replace(hour=23, minute=59, second=59, microsecond=999999)
        return iso(dt.replace(tzinfo=dt.tzinfo or TZ).isoformat()), precision
    except (ValueError, ArithmeticError, OverflowError):
        return None, 'invalid'




class Workbench:
    def __init__(self, root=DEFAULT):
        self.root = Path(root).resolve()
        if not self.root.is_relative_to((ROOT / 'artifacts/eng063').resolve()):
            raise ValueError('运行目录必须在本单隔离范围内')
        self.root.mkdir(parents=True, exist_ok=True)
        self.path = self.root / 'workbench.sqlite3'
        with closing(self.connect()) as c, c:
            c.executescript(corpus.SCHEMA + SCHEMA + RESEARCH_SCHEMA)
            c.execute('INSERT OR IGNORE INTO replay_runs(id,title,cutoff) VALUES(?,?,?)',
                      ('may-replay', '5月新闻回放', iso('2026-05-30T00:00:00+08:00')))
            c.execute('INSERT OR IGNORE INTO replay_runs(id,title,cutoff) VALUES(?,?,?)',
                      ('eng061', '已有真实结果 · 工程诊断', iso('2026-09-12T00:00:00+08:00')))

    def connect(self):
        c = corpus.connect(self.path)
        from event_workbench.news_deadline import bind
        return bind(c,10000)

    def save_news(self,payload):
        from event_workbench.news_intake import save
        return save(self,payload)

    def register_scope(self, run_id, manifest):
        """Bind a finite processing run to news identities, without importing answers."""
        if run_id in ('may-replay','eng061') or not run_id:raise ValueError('new_processing_run_required')
        rows=manifest.get('documents',[])
        if not rows or len({r['source_row'] for r in rows})!=len(rows):raise ValueError('invalid_material_scope')
        split=manifest.get('split')
        if split not in ('development','acceptance'):raise ValueError('explicit_split_required')
        with closing(self.connect()) as c,c:
            c.execute('BEGIN IMMEDIATE')
            cutoff=iso('2026-06-01T00:00:00+08:00')
            existing=c.execute('SELECT source_row,body FROM replay_scope_rows WHERE run_id=?',(run_id,)).fetchall()
            # References and expected answers never become processing inputs.
            allowed=('source_row','available_at','precision','body_sha256','title_sha256','content_sha256','original_split')
            records={r['source_row']:corpus.js({k:r[k] for k in allowed if k in r}) for r in rows}
            if existing and {r['source_row']:r['body'] for r in existing}!=records:raise ValueError('frozen_processing_scope_changed')
            for row in rows:
                actual=c.execute("SELECT p.guard,p.available_at FROM replay_rows p JOIN source_rows s ON s.id=p.row_id WHERE s.row_key=?",(row['source_row'],)).fetchone()
                if not actual or actual['guard']!='allowed' or not actual['available_at'] or actual['available_at']>=cutoff:raise ValueError('material_not_allowed')
                if row.get('available_at') and iso(row['available_at'])!=iso(actual['available_at']):raise ValueError('material_time_changed')
            c.execute('INSERT OR IGNORE INTO replay_runs(id,title,cutoff) VALUES(?,?,?)',(run_id,'官方材料 · '+('开发运行' if split=='development' else '独立验收'),cutoff))
            c.executemany('INSERT OR IGNORE INTO replay_scope_rows VALUES(?,?,?)',[(run_id,k,v) for k,v in records.items()])
        return dict(run_id=run_id,materials=len(rows),split=split)

    def setup_catalog(self):
        audit = json.loads((ROOT / 'research/data_audit/official_dataset_audit.json').read_text(encoding='utf-8'))
        expected = {f['file']: f['sha256'] for f in audit['file_inventory']}
        with closing(self.connect()) as c, c:
            for kind, suffix in [('stock', '标的'), ('industry', '行业')]:
                path = ROOT / ('官方数据/事件类型_' + suffix + '.json')
                if corpus.filehash(path) != expected[path.name]: raise ValueError('定义文件指纹变化')
                for record in json.loads(path.read_text(encoding='utf-8')):
                    obj = record['company' if kind == 'stock' else 'industry']
                    code = obj['trading_code' if kind == 'stock' else 'inducode']
                    item = dict(id=kind + ':' + code, type=kind, code=code,
                                name=obj['secu_abbr' if kind == 'stock' else 'induname'],
                                definitions=record['event_schema'], source=path.name,
                                source_sha256=expected[path.name], industry_system='官方事件体系；未提供分类版本' if kind=='industry' else None)
                    c.execute('INSERT OR REPLACE INTO replay_catalog VALUES(?,?)', (item['id'], corpus.js(item)))




    def run(self, c, run_id):
        if not c.in_transaction: c.execute('BEGIN')
        row = c.execute('SELECT * FROM replay_runs WHERE id=?', (run_id,)).fetchone()
        if not row: raise ValueError('未登记的运行')
        return dict(row)

    def status(self, run_id, *, events_only=False):
        with closing(self.connect()) as c:
            run = self.run(c, run_id)
            visible_events=[json.loads(r['body']) for r in self.visible_event_rows(c,run,current_only=True,events_only=events_only)]
            events = len(visible_events)
            linked={}
            for event in visible_events:
                if event.get('record_kind')=='news':continue
                evidence=event.get('evidence_sources',{})
                sources=[m['source_row'] for m in evidence.get('members',[]) if m.get('source_row')] or evidence.get('source_rows',[])
                for source in set(sources):linked.setdefault(source,[]).append(event['member_id'])
            material_outcomes={}
            counts = [dict(r) for r in c.execute('SELECT p.split,substr(r.published_iso,1,10) AS day,p.guard,count(*) AS rows FROM replay_rows p JOIN source_rows r ON r.id=p.row_id GROUP BY p.split,day,p.guard')]
            visible = c.execute('SELECT count(*) FROM replay_rows WHERE available_at<?', (run['cutoff'],)).fetchone()[0] if run_id=='may-replay' else c.execute('SELECT count(DISTINCT document_id) FROM replay_events WHERE run_id=? AND available_at<?', (run_id,run['cutoff'])).fetchone()[0]
            allowed = c.execute("SELECT count(*) FROM replay_rows WHERE available_at<? AND guard='allowed'", (run['cutoff'],)).fetchone()[0] if run_id=='may-replay' else visible
            total = c.execute('SELECT count(*) FROM replay_rows').fetchone()[0]
            processing=[];processed_documents=[]
            if run_id not in ('may-replay','eng061'):
                scope_args=(run_id,run['cutoff'])
                visible=c.execute('SELECT count(*) FROM replay_scope_rows s JOIN source_rows r ON r.row_key=s.source_row JOIN replay_rows p ON p.row_id=r.id WHERE s.run_id=? AND p.available_at<?',scope_args).fetchone()[0]
                allowed=c.execute("SELECT count(*) FROM replay_scope_rows s JOIN source_rows r ON r.row_key=s.source_row JOIN replay_rows p ON p.row_id=r.id WHERE s.run_id=? AND p.available_at<? AND p.guard='allowed'",scope_args).fetchone()[0]
                total=c.execute('SELECT count(*) FROM replay_scope_rows WHERE run_id=?',(run_id,)).fetchone()[0]
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='product_dependencies'").fetchone():
                    processed=c.execute("""WITH bindings AS (
                        SELECT job,json_extract(body,'$.source_row') AS source_row FROM product_dependencies
                        UNION
                        SELECT json_extract(body,'$.job'),json_extract(body,'$.source_row') FROM imports WHERE id LIKE 'news-source:%'
                        ) SELECT s.source_row,t.title,j.job FROM replay_scope_rows s
                        JOIN source_rows r ON r.row_key=s.source_row JOIN replay_rows p ON p.row_id=r.id
                        JOIN documents d ON d.id=r.document_id JOIN contents t ON t.id=d.content_id
                        JOIN bindings j ON j.source_row=s.source_row
                        WHERE s.run_id=? AND p.guard='allowed' AND p.available_at<?
                        ORDER BY p.available_at,r.row_key""",(run_id,run['cutoff'])).fetchall()
                    processed_documents=[dict(source_row=r['source_row'],title=r['title']) for r in processed]
                    processing=[dict(source_row=row['source_row'],stage=s['stage'],status=s['status'],reason=s['reason'])
                                for row in processed for s in c.execute('SELECT stage,status,reason FROM stages WHERE job=?',(row['job'],))]
                    from event_workbench import news_outcomes
                    material_outcomes={row['source_row']:news_outcomes.saved(c,row['job'],linked.get(row['source_row'],[]),row['source_row']) for row in processed}
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='news_input_records'").fetchone():
                    from event_workbench import news_outcomes
                    for row in c.execute("SELECT i.source_row,i.body FROM news_input_records i JOIN source_rows r ON r.row_key=i.source_row JOIN replay_rows p ON p.row_id=r.id JOIN replay_scope_rows s ON s.source_row=i.source_row AND s.run_id=i.run_id WHERE i.run_id=? AND p.guard='allowed' AND p.available_at<?",(run_id,run['cutoff'])):
                        record=json.loads(row['body']);source=row['source_row']
                        if source not in material_outcomes:
                            material_outcomes[source]=news_outcomes.saved(c,corpus.digest(record['document']),linked.get(source,[]),source)
                        material_outcomes[source]['matter_outcomes']=news_outcomes.matter_items(c,corpus.digest(record['document']),run_id,linked.get(source,[]))
            reviews=[dict(r) for r in c.execute("""SELECT v.id,v.source_row,json_extract(v.body,'$.title') AS title,json_extract(v.body,'$.status') AS status
                FROM replay_extraction_reviews v JOIN source_rows r ON r.row_key=v.source_row
                JOIN replay_rows p ON p.row_id=r.id WHERE v.run_id=? AND p.guard='allowed' AND p.available_at<?
                ORDER BY v.saved_at DESC,v.id LIMIT 10""",(run_id,run['cutoff']))]
            result=dict(engine='eng063', run=run, runs=[dict(r) for r in c.execute('SELECT * FROM replay_runs')],
                        metadata=corpus.metadata(c), daily=counts, input_rows=total if run_id!='eng061' else visible,
                        released_rows=visible, readable_rows=allowed, events=events, semantic=('saved_candidate_results' if events else 'saved_failed_reviews' if reviews else 'not_executed') if run_id=='may-replay' else 'saved_partial_results',
                        notifications=sum(bool(d.get('active')) for e in visible_events for d in e.get('warning_reasons',{}).get('decisions',[])), model_calls=0, model_calls_scope='new_calls_by_this_read_service_not_imported_history', extraction_reviews=reviews, processing=processing, processed_documents=processed_documents,material_outcomes=material_outcomes,
                        catalog=[json.loads(r[0]) for r in c.execute('SELECT body FROM replay_catalog ORDER BY id')])
            if result['metadata'].get('showcase_version'):
                from event_workbench.case_replay import describe
                result.update(describe(self,run_id))
            return result

    def materials(self, run_id, query='', page=1):
        if not isinstance(query, str) or len(query)>200: raise ValueError('搜索词过长')
        page = int(page)
        if not 1<=page<=20000: raise ValueError('页码无效')
        with closing(self.connect()) as c:
            run = self.run(c, run_id)
            if run_id=='eng061':
                rows = c.execute('SELECT DISTINCT d.id,d.body FROM replay_documents d JOIN replay_events e ON e.document_id=d.id WHERE e.run_id=? AND e.available_at<?', (run_id,run['cutoff'])).fetchall()
                data=[dict(id=r['id'], **json.loads(r['body'])) for r in rows]
                data=[{k:v for k,v in r.items() if k!='text'} for r in data if query.lower() in (r.get('title','')+r.get('text','')).lower()]
                return dict(total=len(data),rows=data[(page-1)*20:page*20],page=page,revision=run['revision'])
            join=" FROM replay_rows p JOIN source_rows r ON r.id=p.row_id JOIN documents d ON d.id=r.document_id JOIN contents t ON t.id=d.content_id WHERE p.guard='allowed' AND p.available_at<?"
            args=[run['cutoff']]
            if run_id not in ('may-replay','eng061'):
                join+=' AND EXISTS (SELECT 1 FROM replay_scope_rows s WHERE s.run_id=? AND s.source_row=r.row_key)';args.append(run_id)
            if corpus.metadata(c).get('showcase_version'):
                join+=' AND r.id=(SELECT min(r2.id) FROM source_rows r2 JOIN replay_scope_rows s2 ON s2.source_row=r2.row_key WHERE r2.document_id=r.document_id AND s2.run_id=?)';args.append(run_id)
            if query:
                join+=" AND (instr(lower(coalesce(t.title,'')),lower(?))>0 OR instr(lower(coalesce(t.body,'')),lower(?))>0 OR instr(coalesce(r.article_id_raw,''),?)>0)"; args += [query,query,query]
            total=c.execute('SELECT count(*)'+join,args).fetchone()[0]
            rows=[dict(r) for r in c.execute('SELECT r.row_key AS id,t.title,r.source_raw AS source,r.published_iso AS publication_time,p.available_at,p.split,p.precision,r.text_status'+join+' ORDER BY p.available_at DESC,r.id DESC LIMIT 20 OFFSET ?',args+[(page-1)*20])]
            return dict(total=total,rows=rows,page=page,revision=run['revision'])

    def document(self, run_id, identity, *, include_analysis=True, _connection=None):
        with (closing(self.connect()) if _connection is None else nullcontext(_connection)) as c:
            run=self.run(c,run_id)
            if run_id=='eng061':
                row=c.execute('SELECT d.body FROM replay_documents d JOIN replay_events e ON e.document_id=d.id WHERE d.id=? AND e.run_id=? AND e.available_at<?',(identity,run_id,run['cutoff'])).fetchone()
                if not row: raise ValueError('原文不属于本运行或在此时点不可见')
                return dict(id=identity,run_revision=run['revision'],**json.loads(row[0]))
            row=c.execute("SELECT t.title,t.body AS text,r.source_raw AS source,r.published_iso AS publication_time,r.published_raw,r.row_key AS id,r.row_number,f.path,p.available_at,p.precision,p.split FROM replay_rows p JOIN source_rows r ON r.id=p.row_id JOIN files f ON f.sha=r.file_sha JOIN documents d ON d.id=r.document_id JOIN contents t ON t.id=d.content_id WHERE r.row_key=? AND p.guard='allowed' AND p.available_at<?",(identity,run['cutoff'])).fetchone()
            if not row: raise ValueError('原文受保护、尚未到达或不属于本运行')
            if run_id not in ('may-replay','eng061') and not c.execute('SELECT 1 FROM replay_scope_rows WHERE run_id=? AND source_row=?',(run_id,identity)).fetchone():raise ValueError('原文不属于固定材料范围')
            result=dict(row,run_revision=run['revision'])
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='showcase_sources'").fetchone():
                provenance=c.execute('SELECT body FROM showcase_sources WHERE source_row=?',(identity,)).fetchone()
                if provenance:result.update(json.loads(provenance[0]))
            duplicate=c.execute('SELECT x.target_raw FROM duplicate_edges x JOIN source_rows r ON r.id=x.source_row_id WHERE r.row_key=?',(identity,)).fetchone()
            if duplicate and duplicate[0]:
                target=c.execute("SELECT r.row_key FROM source_rows r JOIN replay_scope_rows s ON s.source_row=r.row_key JOIN replay_rows p ON p.row_id=r.id WHERE r.article_id_raw=? AND s.run_id=? AND p.guard='allowed' AND p.available_at<? LIMIT 1",(duplicate[0],run_id,run['cutoff'])).fetchone()
                result['duplicate_relation']=dict(target=target[0] if target else None,status='已关联' if target else '关联原文未收录')
            from event_workbench.news_intake import metadata
            result.update(metadata(c,run_id,identity))
            # Internal evidence reads need the guarded original, not the dossier-wide UI projection.
            if not include_analysis:return result
            from event_workbench.document_pipeline import digest
            doc_sha=digest({k:result[k] for k in ('title','text','source','publication_time')})
            result['extraction_reviews']=[dict(json.loads(v['body']),id=v['id'],saved_at=v['saved_at']) for v in c.execute(
                'SELECT * FROM replay_extraction_reviews WHERE run_id=? AND source_row=? AND document_sha=? ORDER BY saved_at DESC,id',
                (run_id,identity,doc_sha))]
            result['processing']=[]
            result['model_records']=[]
            if run_id not in ('may-replay','eng061') and c.execute("SELECT 1 FROM sqlite_master WHERE name='product_dependencies'").fetchone():
                # Identical guarded source rows share one job; retain each row's own original above.
                result['processing']=[dict(stage=r['stage'],status=r['status'],reason=r['reason']) for r in c.execute("SELECT t.stage,t.status,t.reason FROM product_dependencies d JOIN stages t ON t.job=d.job WHERE d.job=?",(doc_sha,))]
                for r in c.execute("SELECT t.stage,t.status,t.reason,t.result FROM product_dependencies d JOIN stages t ON t.job=d.job WHERE d.job=? AND t.result IS NOT NULL AND t.stage IN ('extract','mentions')",(doc_sha,)):
                    record=json.loads(r['result']);provider=record.get('provider') or {}
                    raw_answer=(provider.get('choices') or [{}])[0].get('message',{}).get('content')
                    coverage=[]
                    if record.get('version') in ('news-mvp-v1','news-competition-v1','news-competition-baseline-v1','news-business-v1'):
                        from event_workbench.document_processing_plan import plan_document
                        from event_workbench.document_pipeline import DOCUMENT_MAX_BYTES
                        plan=plan_document({k:result[k] for k in ('title','text','source','publication_time')},document_max_bytes=DOCUMENT_MAX_BYTES)
                        parts=record.get('admission',{}).get('parts',[])
                        blocks=plan.get('blocks',[]) if plan['route']!='full_document' else []
                        raw_answer='\n\n'.join('分块 '+str(i+1)+'\n'+p.get('raw_answer',p.get('error','未返回')) for i,p in enumerate(parts))
                        coverage=[dict(block=i+1,core=blocks[i]['core'] if i<len(blocks) else dict(start=0,end=len(result['text'])),status=p['status'],accepted=len(p.get('core_admission',{}).get('accepted',p.get('accepted',[]))),rejected=len(p.get('core_admission',{}).get('rejected',p.get('rejected',[]))),error=p.get('error')) for i,p in enumerate(parts)]
                    if record.get('enrichment_parts') is not None:
                        raw_answer='\n\n'.join(p.get('enrichment_raw_answer',p.get('error','未执行补充')) for p in record['enrichment_parts'])
                    result['model_records'].append(dict(stage=r['stage'],status=r['status'],reason=r['reason'],
                        raw_answer=raw_answer,
                        projection_derivations=[d for p in record.get('enrichment_parts',record.get('admission',{}).get('parts',[])) for d in p.get('projection_derivations',[])],
                        admission=record.get('admission'),coverage=coverage,core_candidates=record.get('core_candidates',[]),unmatched_reason=record.get('unmatched_reason'),
                        checks=record.get('strict') or record.get('scope_review') or record.get('error')))
            latest=next((r for r in result['model_records'] if r['stage']=='mentions' and r.get('core_candidates')),None)
            if latest:
                for r in result['model_records']:
                    if r['stage']=='extract':r['core_candidates']=[]
            from event_workbench import news_outcomes
            linked=[e['member_id'] for r in self.visible_event_rows(c,run,current_only=True) for e in [json.loads(r['body'])] if e.get('record_kind')!='news' and identity in e.get('evidence_sources',{}).get('source_rows',[])]
            result['outcome']=news_outcomes.saved(c,doc_sha,linked,identity)
            result['matter_outcomes']=news_outcomes.matter_items(c,doc_sha,run_id,linked)
            result['processing_reviews']=[]
            if c.execute("SELECT 1 FROM sqlite_master WHERE name='review_receipts'").fetchone():
                result['processing_reviews']=[dict(r) for r in c.execute("SELECT origin,status,reason,body FROM review_receipts WHERE json_extract(body,'$.source_row')=? ORDER BY rowid DESC LIMIT 3",(identity,))]
            for table in ('dossier_reviews','impact_reviews'):
                if not linked or not c.execute("SELECT 1 FROM sqlite_master WHERE name=?",(table,)).fetchone():continue
                marks=','.join('?' for _ in linked)
                for r in c.execute(f"SELECT status,body,projection,error FROM {table} WHERE run_id=? AND event_id IN ({marks}) AND json_extract(body,'$.primary_source_row')=? AND json_extract(body,'$.document_sha')=? ORDER BY rowid DESC LIMIT 8",(run_id,*linked,identity,doc_sha)):
                    review=json.loads(r['body'])
                    result['processing_reviews'].append(dict(origin=review.get('origin','codex_session'),status=r['status'],scope='article_review',
                        reason=r['error'] or review.get('reason'),body=corpus.js(dict(review,checked=json.loads(r['projection']) if r['projection'] else None))))
            return result



    def visible_event_rows(self, c, run, event_id=None, current_only=False, events_only=False):
        params=[run['id'],run['cutoff'],run['cutoff']]
        selected=''
        if event_id is not None:selected=' AND e.id=?';params.append(event_id)
        if events_only:selected+=" AND coalesce(json_extract(e.body,'$.record_kind'),'event')!='news'"
        rows=c.execute("""SELECT e.* FROM replay_events e WHERE e.run_id=? AND e.available_at<? AND
            (coalesce(json_extract(e.body,'$.candidate_only'),0)=0 OR EXISTS
             (SELECT 1 FROM source_rows r JOIN replay_rows p ON p.row_id=r.id
              WHERE r.row_key=e.document_id AND p.guard='allowed' AND p.available_at<?))"""+selected+' ORDER BY e.available_at,e.id',params).fetchall()
        if current_only:
            from event_workbench.product_research import publication_gaps
            rows=[r for r in rows for body in [json.loads(r['body'])] if body.get('current_status')!='background' and not set(publication_gaps(body)) & {'事件摘要','主体或明确缺口','材料来源'}]
        if run['id'] in ('may-replay','eng061'):return rows
        safe=[]
        allowed={r[0] for r in c.execute("SELECT s.source_row FROM replay_scope_rows s JOIN source_rows r ON r.row_key=s.source_row JOIN replay_rows p ON p.row_id=r.id WHERE s.run_id=? AND p.guard='allowed' AND p.available_at<?",(run['id'],run['cutoff']))}
        for row in rows:
            event=json.loads(row['body']);sources=event.get('evidence_sources',{}).get('source_rows',[])
            if not sources:continue
            if set(sources)<=allowed:safe.append(row)
        return safe

    def prepare_document(self, run_id, identity, build_request=False):
        """Local input preview using the existing Product planner; never submit a job."""
        if run_id == 'eng061':
            raise ValueError('历史诊断材料不进入新原文处理准备')
        item = self.document(run_id, identity, include_analysis=False)
        from event_workbench.document_pipeline import validate_product_document, DOCUMENT_MAX_BYTES
        from event_workbench.document_processing_plan import plan_document
        document = {k: item[k] for k in ('title', 'text', 'source', 'publication_time')}
        plan = plan_document(validate_product_document(document), document_max_bytes=DOCUMENT_MAX_BYTES)
        from event_workbench.development_lexical_candidates import plan_candidates
        definitions = plan_candidates(plan)
        result = dict(status='prepared_not_extracted', run_id=run_id,
                    run_revision=item['run_revision'], source_row=item['id'],
                    available_at=item['available_at'], time_precision=item['precision'],
                    plan=plan, definition_candidates=definitions,
                    model_executed=False, processing_authorized=False,
                    note='完成原文校验、分块及文字定义候选；没有抽取事件、发送请求或创建业务任务')
        if build_request:
            from event_workbench.development_request import plan_requests
            result['extraction_input']=plan_requests(plan,definitions)
        return result

    def events(self, run_id, with_revision=False, summary=False, events_only=False):
        with closing(self.connect()) as c:
            run=self.run(c,run_id)
            data = [json.loads(r['body']) for r in self.visible_event_rows(c,run,current_only=True,events_only=events_only)]
            if summary:
                from event_workbench.training_showcase import summary as project_summary
                data=[project_summary(e) for e in data]
            if with_revision:
                for e in data:
                    if not e.get('event_version'):
                        e['evidence_sources']={k:v for k,v in e.get('evidence_sources',{}).items() if k in ('original','original_quotes')}
                        e['warning_reasons']={'decisions':e.get('warning_reasons',{}).get('decisions',[])}
                return dict(revision=run['revision'],items=data)
            return data

    def state(self, run_id, with_revision=False):
        with closing(self.connect()) as c:
            run=self.run(c,run_id)
            data = [dict(kind=r['kind'],target=r['target'],**json.loads(r['body'])) for r in c.execute('''SELECT kind,target,body FROM replay_actions a WHERE run_id=? AND
                (kind IN ('subscription','alert_closed') OR EXISTS (SELECT 1 FROM replay_events e WHERE e.id=a.target AND e.run_id=a.run_id AND e.available_at<?))''',(run_id,run['cutoff']))]
            rows=self.visible_event_rows(c,run)
            bodies={r['id']:json.loads(r['body']) for r in rows}
            visible=set(bodies)
            notices={d['id']:d for event in bodies.values() for d in published_notices(event)}
            data=[r for r in data if r['kind']=='subscription' or (r['target'] in notices if r['kind']=='alert_closed' else r['target'] in visible)]
            versions={identity:(event.get('event_version'),event.get('assessment_version',1)) for identity,event in bodies.items()}
            for r in data:
                if r['kind']=='read' and versions.get(r['target']) is not None:
                    r['value']=bool(r['value'] and r.get('event_version')==versions[r['target']][0] and r.get('assessment_version',1)==versions[r['target']][1])
                if r['kind']=='alert_closed':r['value']=bool(r['value'] and r.get('notice_version')==notices[r['target']]['version'])
            return dict(revision=run['revision'],items=data) if with_revision else data

    def change(self, payload):
        if not isinstance(payload,dict):raise ValueError('操作必须是对象')
        run_id,kind=payload.get('run'),payload.get('kind')
        if not isinstance(run_id,str) or not isinstance(kind,str) or type(payload.get('revision')) is not int:
            raise ValueError('运行、操作类型或版本格式不正确')
        if kind!='cutoff' and (not isinstance(payload.get('target'),str) or len(payload['target'])>2000):
            raise ValueError('操作对象格式不正确')
        with closing(self.connect()) as c, c:
            c.execute('BEGIN IMMEDIATE')
            run=self.run(c,run_id)
            if payload.get('revision') != run['revision']: raise ValueError('运行已变化，请刷新后操作')
            if kind=='cutoff':
                if run_id!='may-replay': raise ValueError('历史结果诊断是固定快照，不能用作时点回放')
                cutoff=iso(payload['value'])
                if run_id=='may-replay' and not iso('2026-05-26T00:00:00+08:00')<=cutoff<=iso('2026-06-01T00:00:00+08:00'): raise ValueError('回放时点超出六天范围')
                c.execute('UPDATE replay_runs SET cutoff=?,revision=revision+1 WHERE id=?',(cutoff,run_id))
                return dict(saved=True)
            target=payload.get('target');value=payload.get('value')
            if kind=='subscription':
                if not c.execute('SELECT 1 FROM replay_catalog WHERE id=?',(target,)).fetchone(): raise ValueError('监测对象不存在')
            elif kind=='alert_closed':
                notices={d['id']:d for e in self.visible_event_rows(c,run) for d in published_notices(json.loads(e['body']))}
                if target not in notices:raise ValueError('提醒不在当前可见范围')
            elif kind in ('follow','read','task','mute'):
                if target not in {r['id'] for r in self.visible_event_rows(c,run)}: raise ValueError('事件不在当前可见范围')
            else: raise ValueError('未开放的操作')
            if kind in ('follow','subscription','read','alert_closed','mute') and type(value) is not bool: raise ValueError('操作值无效')
            if kind=='task' and (not isinstance(value,dict) or not isinstance(value.get('text'),str) or not 1<=len(value['text'].strip())<=1000 or type(value.get('done')) is not bool): raise ValueError('待办需有1—1000字内容和完成状态')
            action=dict(value=value,operated_at=corpus.now())
            if kind=='read':
                event=json.loads(c.execute('SELECT body FROM replay_events WHERE id=? AND run_id=?',(target,run_id)).fetchone()[0])
                action['event_version']=event.get('event_version')
                action['assessment_version']=event.get('assessment_version',1)
            if kind=='alert_closed':action['notice_version']=notices[target]['version']
            body=corpus.js(action)
            old=c.execute('SELECT body FROM replay_actions WHERE run_id=? AND kind=? AND target=?',(run_id,kind,target)).fetchone()
            if old and json.loads(old[0])['value']==value and all(json.loads(old[0]).get(k)==action.get(k) for k in ('event_version','assessment_version','notice_version')):return dict(saved=True,unchanged=True)
            c.execute('INSERT OR REPLACE INTO replay_actions VALUES(?,?,?,?)',(run_id,kind,target,body))
            c.execute('INSERT INTO replay_operations(run_id,kind,target,body,operated_at) VALUES(?,?,?,?,?)',(run_id,kind,target,body,corpus.now()))
            return dict(saved=True)
