"""Deployment checks use temporary workspaces and a local fake provider, never paid calls."""
import hashlib
import importlib.util
import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
from contextlib import closing
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from event_workbench import deepseek_credentials as credentials, trial_transport
from event_workbench import compact_live_recovery

spec = importlib.util.spec_from_file_location('deployment_entry', ROOT / 'scripts/open_competition.py')
entry = importlib.util.module_from_spec(spec); spec.loader.exec_module(entry)


class DeploymentChecks(unittest.TestCase):
    def test_seed_installs_once_and_retains_user_data(self):
        parent = ROOT / 'artifacts/eng063'; parent.mkdir(parents=True, exist_ok=True)
        seed = ROOT / 'data/示例事件库.sqlite3.gz'
        before = seed.read_bytes()
        with tempfile.TemporaryDirectory(prefix='deployment-', dir=parent) as directory:
            root = Path(directory); entry.prepare_workspace(root)
            with closing(sqlite3.connect(root / 'workbench.sqlite3')) as c, c:
                self.assertEqual(c.execute('PRAGMA quick_check').fetchone()[0], 'ok')
                self.assertEqual(c.execute('PRAGMA foreign_key_check').fetchall(), [])
                self.assertEqual(c.execute('SELECT count(*) FROM replay_events').fetchone()[0], 160)
                self.assertEqual(c.execute('SELECT count(*) FROM source_rows').fetchone()[0], 238)
                self.assertEqual(c.execute('SELECT count(*) FROM replay_research').fetchone()[0], 0)
                c.execute("INSERT INTO meta VALUES('deployment_test','true')")
            entry.prepare_workspace(root)
            with closing(sqlite3.connect(root / 'workbench.sqlite3')) as c:
                self.assertEqual(c.execute("SELECT value FROM meta WHERE key='deployment_test'").fetchone()[0], 'true')
        self.assertEqual(seed.read_bytes(), before)

    def test_workspace_does_not_write_outside_program(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaises(ValueError): entry.prepare_workspace(Path(directory))
            self.assertEqual(list(Path(directory).iterdir()), [])

    def test_config_is_portable_environment_wins_and_replacement_preserves_settings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'deepseek.json'
            path.write_text(json.dumps(dict(api_key='SYNTHETIC_FILE_KEY', port=8781)), encoding='utf8')
            with patch.object(credentials, 'CREDENTIAL_PATH', path), patch.dict(os.environ, {'DEEPSEEK_API_KEY': ''}):
                self.assertEqual(credentials.read_credential(), 'SYNTHETIC_FILE_KEY')
                with self.assertRaises(credentials.CredentialError): credentials.save_credential('SYNTHETIC_NEW_KEY')
                credentials.save_credential('SYNTHETIC_NEW_KEY', overwrite=True)
                self.assertEqual(credentials.read_credential(), 'SYNTHETIC_NEW_KEY')
                self.assertEqual(json.loads(path.read_text('utf8'))['port'], 8781)
                with patch.dict(os.environ, {'DEEPSEEK_API_KEY': 'SYNTHETIC_ENV_KEY'}):
                    self.assertEqual(credentials.read_credential(), 'SYNTHETIC_ENV_KEY')
                path.write_text('{"api_key": "contains a space"}', encoding='utf8')
                self.assertFalse(credentials.credential_exists())

    def test_portable_bm25_has_no_gpu_or_scipy_requirement(self):
        import bm25s
        index = bm25s.BM25(method='lucene', k1=1.5, b=.75)
        index.index([['company', 'contract'], ['weather']], show_progress=False)
        scores = index.get_scores(['contract'])
        self.assertGreater(float(scores[0]), float(scores[1]))

    def test_real_spawn_transport_with_local_provider_and_fake_key(self):
        provider = dict(model='deepseek-flash', choices=[dict(finish_reason='stop', message=dict(content='{"ok":true}'))],
                        usage=dict(prompt_tokens=5, completion_tokens=5))
        received = []
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                received.append((self.headers.get('Authorization'), payload['model']))
                body = json.dumps(provider).encode()
                self.send_response(200); self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body))); self.end_headers(); self.wfile.write(body)
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
        request = dict(model='deepseek-flash', max_tokens=100, messages=[dict(role='user', content='Return JSON {"ok":true}')])
        try:
            with patch.object(compact_live_recovery.common, 'ENDPOINT', f'http://127.0.0.1:{server.server_port}/chat/completions'):
                captured = []; answer = trial_transport.send(request, 'SYNTHETIC_KEY', 15, captured)
            self.assertEqual(answer['choices'][0]['message']['content'], '{"ok":true}')
            self.assertEqual(received, [('Bearer SYNTHETIC_KEY', 'deepseek-flash')])
            self.assertTrue(captured)
        finally:
            server.shutdown(); server.server_close(); worker.join(timeout=2)


if __name__ == '__main__': unittest.main()
