"""Local HTTP server shared by the existing four-page entry."""
import json
import sys
import sqlite3
import logging
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs, unquote

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from event_workbench.product_replay import Workbench, DEFAULT
from event_workbench import product_research


def serve(app, port, service=None):
    # Enable concurrent page reads while analysis publishes its transaction.
    for workspace in {x.path:x for x in (app,service.app if service else app)}.values():
        with closing(workspace.connect()) as c:
            if c.execute('PRAGMA journal_mode=WAL').fetchone()[0].lower()!='wal':
                raise RuntimeError('无法开启数据库并发读取')
    static=ROOT/'engineering/demos/zhiyan-demo'
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args): pass
        def reply(self, data, code=200):
            body=json.dumps(data,ensure_ascii=False).encode()
            self.send_response(code);self.send_header('Content-Type','application/json; charset=utf-8')
            self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store')
            self.end_headers();self.wfile.write(body)
        def local(self):
            hosts={f'127.0.0.1:{port}',f'localhost:{port}'}
            return self.headers.get('Host') in hosts and self.headers.get('Origin') in (None,*['http://'+h for h in hosts])
        def do_GET(self):
            if not self.local():return self.reply({'error':'拒绝跨站访问'},403)
            u=urlparse(self.path);q={k:v[0] for k,v in parse_qs(u.query).items()}
            try:
                run=q.get('run',service.run_id if service else 'may-replay')
                selected=service.app if service and run==service.run_id else app
                if u.path=='/api/replay/health':
                    import hashlib
                    budget=getattr(service,'budget',None)
                    return self.reply(dict(engine='eng063',target='default' if app.root==DEFAULT.resolve() else 'self-check',workspace=hashlib.sha256(str(app.root.resolve()).encode()).hexdigest(),model_send_enabled=bool(service and service.send_model),model_budget_ledger=str(Path(budget.path).resolve()) if getattr(budget,'path',None) else None,model_budget_total_micros=getattr(budget,'total_micros',None)))
                if u.path=='/api/replay/status':
                    if q.get('progress')=='1' and service and run==service.run_id:
                        with closing(selected.connect()) as c:current=selected.run(c,run)
                        return self.reply(dict(run=current,**service.status()))
                    return self.reply(dict(selected.status(run,events_only=q.get('events_only')=='1'),**(service.status() if service and run==service.run_id else {})))
                if u.path=='/api/replay/materials':return self.reply(selected.materials(run,q.get('q',''),q.get('page',1)))
                if u.path=='/api/replay/document':return self.reply(selected.document(run,q['id']))
                if u.path=='/api/replay/reference':
                    from event_workbench.official_reference import lookup
                    return self.reply(lookup(selected.document(run,q['id'])))
                if u.path=='/api/replay/events':return self.reply(selected.events(run,True,summary=q.get('view')=='summary',events_only=q.get('events_only')=='1'))
                if u.path=='/api/replay/state':return self.reply(selected.state(run,True))
                if u.path=='/api/replay/dossier':return self.reply(product_research.dossier(selected,run,q['id'],q.get('material_version')))
                if u.path=='/api/replay/research':return self.reply(product_research.records(selected,run))
                if u.path=='/api/replay/notifications':
                    from event_workbench.news_notifications import listing
                    return self.reply(listing(selected,run))
                name=unquote(u.path).lstrip('/') or 'index.html'
                path=(static/name).resolve()
                types={'.html':'text/html; charset=utf-8','.js':'text/javascript; charset=utf-8','.css':'text/css; charset=utf-8','.svg':'image/svg+xml','.png':'image/png'}
                if not path.is_relative_to(static.resolve()) or path.suffix not in types or not path.is_file():return self.reply({'error':'没有这个入口'},404)
                body=path.read_bytes();self.send_response(200);self.send_header('Content-Type',types[path.suffix]);self.send_header('Content-Length',str(len(body)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.end_headers();self.wfile.write(body)
            except (ValueError,KeyError,TypeError) as exc:self.reply({'error':str(exc)},400)
            except sqlite3.OperationalError as exc:
                logging.exception('Replay read failed: %s',u.path)
                locked=(getattr(exc,'sqlite_errorcode',0)&255) in (sqlite3.SQLITE_BUSY,sqlite3.SQLITE_LOCKED)
                self.reply({'error':'分析结果正在保存，请稍后重试' if locked else '数据读取异常，请重新连接'},503 if locked else 500)
            except Exception as exc:self.reply({'error':'读取失败：'+type(exc).__name__},500)
        def do_POST(self):
            if not self.local():return self.reply({'error':'拒绝跨站操作'},403)
            if self.path not in ('/api/replay/action','/api/replay/news','/api/replay/news-preview','/api/replay/news-batch','/api/replay/process','/api/replay/research','/api/replay/notification-read','/api/replay/case'):return self.reply({'error':'未开放的操作'},404)
            try:
                size=int(self.headers.get('Content-Length','0'))
                limit=1500000 if self.path in ('/api/replay/news-preview','/api/replay/news-batch') else 65536 if self.path=='/api/replay/news' else 10000
                if not 0<size<=limit or self.headers.get('Content-Type')!='application/json':raise ValueError('请求格式或大小不合法')
                payload=json.loads(self.rfile.read(size))
                if not isinstance(payload,dict):raise ValueError('请求内容必须为对象')
                selected=service.app if service and payload.get('run')==service.run_id else app
                if self.path=='/api/replay/case':
                    from event_workbench.case_replay import change
                    return self.reply(change(selected,payload))
                if self.path=='/api/replay/notification-read':
                    from event_workbench.news_notifications import mark_read
                    return self.reply(mark_read(selected,payload))
                if self.path=='/api/replay/research':return self.reply(product_research.change(selected,payload))
                if self.path=='/api/replay/action':return self.reply(selected.change(payload))
                if not service or payload.get('run')!=service.run_id:raise ValueError('本运行未开放新闻处理')
                if self.path in ('/api/replay/news-preview','/api/replay/news-batch'):
                    from event_workbench import news_upload
                    return self.reply(news_upload.preview(payload) if self.path.endswith('preview') else news_upload.save_batch(service.app,payload))
                return self.reply(service.app.save_news(payload) if self.path=='/api/replay/news' else service.submit(payload))
            except (ValueError,KeyError,TypeError) as exc:self.reply({'error':str(exc)},400)
            except Exception as exc:self.reply({'error':'操作失败：'+type(exc).__name__},500)
    server=ThreadingHTTPServer(('127.0.0.1',port),Handler)
    print(f'见微真实运行 http://127.0.0.1:{port}/?mode=real',flush=True)
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:
        server.server_close()
        if service:service.close()


if __name__=='__main__':
    raise SystemExit('请使用 scripts/open_competition.py 启动见微四页。历史离线命令保留在旧运行版本中。')
