"""ENG037 raw train corpus. Local SQLite only; labels are never FTS/model text."""
import hashlib
import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from event_workbench import corpus_xlsx as xlsx

ROOT=Path(__file__).resolve().parents[2]
DB=ROOT/'artifacts/eng037/corpus/v1/corpus.sqlite'
VERSION='eng037-v1'
SOURCES=[('官方数据/news_without_tags_train.xlsx','398a97a849ea4aecec25c9762d9c1eedd7cb9bce64851ac7ca84beb7443f3302'),
         ('官方数据/news_with_tags_train.xlsx','ea66f4b5fac1b1ce226fbe213b0a98438bc61bd4a8a9740b6275caeac1a4d063')]
CORE={'article_file_id','article_title','article_source','content','article_publish_time'}
LABELS={'trading_code','secu_abbr','industry_code','industry_name','event_name','event_emotion','event_impact_analysis'}
def js(v):return json.dumps(v,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def digest(v):return hashlib.sha256(js(v).encode('utf-8')).hexdigest()
def filehash(p):
    with Path(p).open('rb') as f:return hashlib.file_digest(f,'sha256').hexdigest()
def now():return datetime.now(timezone.utc).isoformat()
def connect(path,readonly=False):
    c=sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro',uri=True) if readonly else sqlite3.connect(path)
    c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON');c.execute('PRAGMA cache_size=-32768')
    return c

SCHEMA="""
CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
CREATE TABLE IF NOT EXISTS files(sha TEXT PRIMARY KEY,path TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS sheets(file_sha TEXT,sheet TEXT,member TEXT,header_json TEXT,date_system TEXT,last_row INTEGER DEFAULT 1,rows_count INTEGER DEFAULT 0,state TEXT DEFAULT 'building',PRIMARY KEY(file_sha,sheet));
CREATE TABLE IF NOT EXISTS contents(id INTEGER PRIMARY KEY,sha TEXT UNIQUE NOT NULL,title TEXT,body TEXT);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY,original_id TEXT,content_id INTEGER REFERENCES contents(id));
CREATE TABLE IF NOT EXISTS source_rows(id INTEGER PRIMARY KEY,row_key TEXT UNIQUE NOT NULL,file_sha TEXT REFERENCES files(sha),sheet TEXT,row_number INTEGER,document_id TEXT REFERENCES documents(id),article_id_raw TEXT,source_raw TEXT,published_raw TEXT,published_iso TEXT,date_status TEXT,text_status TEXT,label_available INTEGER,cell_meta_json TEXT,extras_json TEXT,issues_json TEXT,raw_sha TEXT,UNIQUE(file_sha,sheet,row_number));
CREATE TABLE IF NOT EXISTS annotations(source_row_id INTEGER PRIMARY KEY REFERENCES source_rows(id),payload_json TEXT);
CREATE TABLE IF NOT EXISTS duplicate_edges(source_row_id INTEGER PRIMARY KEY REFERENCES source_rows(id),target_raw TEXT,target_id TEXT,resolution TEXT);
CREATE INDEX IF NOT EXISTS row_document ON source_rows(document_id);
CREATE INDEX IF NOT EXISTS doc_original ON documents(original_id);
CREATE INDEX IF NOT EXISTS row_filter ON source_rows(sheet,published_iso,source_raw);
CREATE VIRTUAL TABLE IF NOT EXISTS text_fts USING fts5(title,body,content='contents',content_rowid='id',tokenize='trigram',detail='none');
"""
def putmeta(c,k,v):c.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',(k,js(v)))
def metadata(c):return {r['key']:json.loads(r['value']) for r in c.execute('SELECT * FROM meta')}
def parsed_date(cell,system):
    if cell['value'] is None:return None,'missing'
    if cell['value']=='':return None,'empty'
    if cell['formula']:return None,'formula_not_evaluated'
    if cell['type']=='n':
        value,issue=xlsx.old._publication(cell,system)
        if issue:return None,'invalid'
        fraction=xlsx.old.Decimal(cell['value'])%1
        if fraction:
            dt=datetime.fromisoformat(value)+timedelta(seconds=float(fraction*86400))
            return dt.isoformat(),'parsed_excel_datetime'
        return value,'parsed_excel_date'
    try:
        return datetime.fromisoformat(cell['value']).isoformat() if 'T' in cell['value'] or ' ' in cell['value'] else datetime.fromisoformat(cell['value']).date().isoformat(),'parsed_text'
    except (ValueError,TypeError):return None,'invalid'

def insert_row(c,file_sha,sheet,row_number,header,cells,system):
    missing=dict(type='missing',value=None,formula=False,formula_text=None)
    raw={name:cells.get(col,missing) for col,name in header.items()}
    extras={str(col):cell for col,cell in cells.items() if col not in header}
    values={name:cell['value'] for name,cell in raw.items()}
    title=values.get('article_title');body=values.get('content');article=values.get('article_file_id')
    row_key=file_sha+':'+sheet+':'+str(row_number)
    content_sha=digest([title,body]);document_id=digest([article if article not in (None,'') else row_key,content_sha])
    c.execute('INSERT OR IGNORE INTO contents(sha,title,body) VALUES (?,?,?)',(content_sha,title,body))
    content_id=c.execute('SELECT id FROM contents WHERE sha=?',(content_sha,)).fetchone()[0]
    c.execute('INSERT OR IGNORE INTO documents VALUES (?,?,?)',(document_id,article,content_id))
    text_status='missing' if body is None else 'empty' if body=='' else 'whitespace' if not body.strip() else 'available'
    pub,date_status=parsed_date(raw['article_publish_time'],system)
    issues=[]
    if text_status!='available':issues.append('body_'+text_status)
    if article is None or not article.strip():issues.append('missing_article_id')
    if date_status in ('missing','empty','invalid','formula_not_evaluated'):issues.append('publication_'+date_status)
    if any(cell['formula'] for cell in raw.values()):issues.append('formula_cached_values_preserved_not_evaluated')
    if extras:issues.append('undeclared_columns_preserved')
    labels={k:v for k,v in raw.items() if k in LABELS}
    available=int(any(v['value'] not in (None,'') for v in labels.values()))
    core_meta={k:{kk:vv for kk,vv in v.items() if kk!='value'} for k,v in raw.items() if k not in LABELS}
    cur=c.execute('INSERT OR IGNORE INTO source_rows(row_key,file_sha,sheet,row_number,document_id,article_id_raw,source_raw,published_raw,published_iso,date_status,text_status,label_available,cell_meta_json,extras_json,issues_json,raw_sha) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
      (row_key,file_sha,sheet,row_number,document_id,article,values.get('article_source'),values.get('article_publish_time'),pub,date_status,text_status,available,js(core_meta),js(extras),js(issues),digest(cells)))
    if cur.rowcount==0:
        old=c.execute('SELECT raw_sha FROM source_rows WHERE row_key=?',(row_key,)).fetchone()[0]
        if old!=digest(cells):raise ValueError('same_source_row_changed')
        return False
    if labels:c.execute('INSERT INTO annotations VALUES (?,?)',(cur.lastrowid,js(labels)))
    if 'duplication_id' in raw:
        target=values['duplication_id']
        c.execute('INSERT INTO duplicate_edges VALUES (?,?,?,?)',(cur.lastrowid,target,target if target not in (None,'') else None,'empty' if target in (None,'') else 'unresolved'))
    return True
