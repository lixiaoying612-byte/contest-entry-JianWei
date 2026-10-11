"""Explicit, backed-up adoption of a reviewed workspace. Never sends a model call."""
import argparse
import json
import sqlite3
import sys
from contextlib import closing
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from event_workbench import training_corpus as corpus, product_downstream, news_intake
from event_workbench.product_replay import Workbench
from event_workbench.document_pipeline import digest


def connect(app, source_path, run_id='training-showcase'):
    source_path=Path(source_path).resolve()
    if source_path==app.path.resolve():raise ValueError('source_must_be_separate')
    fingerprint=corpus.filehash(source_path)
    with closing(app.connect()) as c:
        old=corpus.metadata(c).get('input_connection')
        if old:
            if old['pipeline_sha256']!=fingerprint or old['run']!=run_id:raise ValueError('connection_source_changed')
            return dict(old,reused=True)
        if not corpus.metadata(c).get('showcase_version'):raise ValueError('reviewed_showcase_required')
        app.run(c,run_id)
        if c.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone() and c.execute('SELECT 1 FROM jobs LIMIT 1').fetchone():raise ValueError('destination_pipeline_not_empty')
    with closing(sqlite3.connect(source_path.as_uri()+'?mode=ro',uri=True)) as source:
        source.row_factory=sqlite3.Row
        jobs=list(source.execute('SELECT * FROM jobs'));bindings={}
        for row in source.execute('SELECT body FROM imports'):
            bundle=json.loads(row[0]);answer=bundle.get('provenance',{}).get('raw_answer',{})
            if answer.get('source_row'):bindings[digest(bundle['document'])]=answer['source_row']
        if not jobs or len(bindings)!=len(jobs):raise ValueError('review_source_bindings_incomplete')
        bound={}
        for row in jobs:
            current=app.document(run_id,bindings[row['id']])
            document={k:current[k] for k in ('title','text','source','publication_time')}
            if digest(document)!=row['id'] or document!=json.loads(row['document']):raise ValueError('reviewed_source_changed')
            bound[row['id']]=dict(processor='news-processing-v1',source_row=bindings[row['id']],available_at=current['available_at'],contract_revision='news-mvp-v1',review_origin='codex_session')
        if source.execute("SELECT 1 FROM stages WHERE status IN ('running','unknown_no_retry')").fetchone():raise ValueError('unfinished_source_pipeline')
        # Existing constructors own the schema; raw responses and history copy unchanged.
        product_downstream.Pipeline(app.path)
        with closing(app.connect()) as c:
            c.executescript(news_intake.SCHEMA)
            with c:
                c.execute('BEGIN IMMEDIATE')
                for table in ('jobs','stages','signals','attempts','imports','changes','withdrawals'):
                    rows=list(source.execute('SELECT * FROM '+table))
                    if rows:c.executemany('INSERT INTO '+table+' VALUES('+','.join('?' for _ in rows[0])+')',[tuple(r) for r in rows])
                for jid,binding in bound.items():
                    c.execute('INSERT INTO product_dependencies VALUES(?,?)',(jid,corpus.js(binding)))
                    record=dict(job=jid,source_row=binding['source_row'],available_at=binding['available_at'],document_sha=jid)
                    c.execute('INSERT INTO imports VALUES(?,?,?)',('news-source:'+digest([jid,binding['source_row']]),digest(record),corpus.js(record)))
                c.execute('INSERT INTO news_workspaces VALUES(?,?)',(run_id,'user'))
                result=dict(version='daily-input-v1',run=run_id,reviewed_jobs=len(jobs),pipeline_sha256=fingerprint,model_calls=0)
                corpus.putmeta(c,'input_connection',result)
                c.execute('UPDATE replay_runs SET revision=revision+1 WHERE id=?',(run_id,))
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--workspace',type=Path,required=True);p.add_argument('--pipeline',type=Path,required=True);p.add_argument('--backup',type=Path,required=True);a=p.parse_args()
    if a.backup.exists():raise SystemExit('备份文件已存在，请指定新的路径。')
    app=Workbench(a.workspace);a.backup.parent.mkdir(parents=True,exist_ok=True)
    with closing(app.connect()) as source,sqlite3.connect(a.backup) as target:source.backup(target)
    print(json.dumps(connect(app,a.pipeline),ensure_ascii=False))
