"""Persistent single-document J flow. SQLite claims precede every possible send."""
import json
import sqlite3
from contextlib import contextmanager
from datetime import datetime,timezone
from event_workbench import joint_extraction_trial as trial

VERSION='eng019-v1'
ROOT=trial.ROOT
read=trial.v2.read_json
digest=trial.v2.digest
save=trial.save
now=lambda:datetime.now(timezone.utc).isoformat()
DOCUMENT_MAX_BYTES=48000


def validate_product_document(value):
    return validate(value,max_bytes=DOCUMENT_MAX_BYTES)

def validate(value, *, max_bytes=16000):
    fields=('title','text','publication_time','source')
    if not isinstance(value,dict) or set(value)-set(fields):raise ValueError('仅允许 title/text/publication_time/source 四个字段')
    doc={k:value.get(k) for k in fields}
    if any(v is not None and not isinstance(v,str) for v in doc.values()):raise ValueError('字段必须为字符串或 null')
    if not any((doc[k] or '').strip() for k in ('title','text')):raise ValueError('标题与正文不能同时为空')
    try:size=len(json.dumps(doc,ensure_ascii=False).encode('utf-8'))
    except UnicodeError:raise ValueError('文本包含非法 Unicode')
    if size>max_bytes:raise ValueError(f'原始四字段 JSON 超过{max_bytes} UTF-8字节；未截断、未发送')
    if any(len(doc[k] or '')>1000 for k in ('publication_time','source')):raise ValueError('来源或时间超过1000字符')
    return doc


class Pipeline:
    """Historical database helpers only; new analysis uses NewsProcessor."""
    @contextmanager
    def connect(self):
        c=sqlite3.connect(self.db,timeout=30);c.row_factory=sqlite3.Row
        try:
            with c:yield c
        finally:c.close()

    def counts(self):
        with self.connect() as c:return dict(attempts=c.execute('SELECT count(*) FROM attempts').fetchone()[0],limit=c.execute('SELECT max_attempts FROM policy').fetchone()[0])
