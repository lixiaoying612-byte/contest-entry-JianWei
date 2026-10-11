"""One site notification per reminder version; reading never changes event facts."""
import json
from contextlib import closing
from event_workbench import training_corpus as corpus

SCHEMA='''
CREATE TABLE IF NOT EXISTS news_notifications(run_id TEXT,notice_id TEXT,version INTEGER,event_id TEXT,
 body TEXT,created TEXT,PRIMARY KEY(run_id,notice_id,version));
CREATE TABLE IF NOT EXISTS news_notification_reads(run_id TEXT,notice_id TEXT,version INTEGER,read_at TEXT,
 PRIMARY KEY(run_id,notice_id,version));
'''


def record(c,run_id,event):
    for item in event.get('warning_reasons',{}).get('decisions',[]):
        if not item.get('id') or type(item.get('version')) is not int:continue
        if item.get('rule_version')=='alert-rules-20261004-v3':
            if not item.get('delivery',{}).get('notify') or item.get('stale_assessment'):continue
        elif item.get('rule_version')!='competition-rules-20260923-v1':continue
        c.execute('INSERT OR IGNORE INTO news_notifications VALUES(?,?,?,?,?,?)',
            (run_id,item['id'],item['version'],event['member_id'],corpus.js(dict(item,event_version=event['event_version'])),corpus.now()))


def listing(app,run_id):
    with closing(app.connect()) as c:
        run=app.run(c,run_id)
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='news_notifications'").fetchone():return dict(revision=run['revision'],items=[],unread=0)
        rows=c.execute('''SELECT n.*,r.read_at FROM news_notifications n LEFT JOIN news_notification_reads r
            USING(run_id,notice_id,version) WHERE n.run_id=? ORDER BY n.created DESC,n.notice_id,n.version DESC''',(run_id,)).fetchall()
        visible={eid for eid in {r['event_id'] for r in rows} if app.visible_event_rows(c,run,eid)}
        items=[dict(id=r['notice_id'],version=r['version'],event_id=r['event_id'],created=r['created'],read_at=r['read_at'],notice=json.loads(r['body'])) for r in rows if r['event_id'] in visible]
        return dict(revision=run['revision'],items=items,unread=sum(not r['read_at'] for r in items))


def mark_read(app,payload):
    if not isinstance(payload,dict) or set(payload)!={'run','revision','id','version'} or type(payload['version']) is not int:raise ValueError('通知已读字段无效')
    with closing(app.connect()) as c,c:
        c.execute('BEGIN IMMEDIATE');run=app.run(c,payload['run'])
        if payload['revision']!=run['revision']:raise ValueError('运行版本已变化')
        if not c.execute("SELECT 1 FROM sqlite_master WHERE name='news_notifications'").fetchone():raise ValueError('通知不存在')
        row=c.execute('SELECT event_id FROM news_notifications WHERE run_id=? AND notice_id=? AND version=?',(payload['run'],payload['id'],payload['version'])).fetchone()
        if not row or not app.visible_event_rows(c,run,row[0]):raise ValueError('通知不在可见范围')
        c.execute('INSERT OR IGNORE INTO news_notification_reads VALUES(?,?,?,?)',(payload['run'],payload['id'],payload['version'],corpus.now()))
    return dict(saved=True)
