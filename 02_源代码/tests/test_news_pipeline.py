"""Replay a saved development response through the real queue and publication path."""
import json
import sys
import tempfile
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from event_workbench.product_replay import Workbench
from event_workbench.news_service import NewsService
from event_workbench.news_budget import ModelBudget, COMPETITION_PLAN


class NewsPipelineCheck(unittest.TestCase):
    def test_saved_answer_publishes_new_event_with_two_sends(self):
        fixture = json.loads((ROOT / 'tests/fixtures/saved-event.json').read_text('utf8'))
        parent = ROOT / 'artifacts/eng063'; parent.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(prefix='pipeline-', dir=parent) as directory:
            root = Path(directory); app = Workbench(root); run = 'deployment-news'
            budget = ModelBudget(root / 'synthetic-budget.sqlite3', total_rmb=3, pricing=json.loads((ROOT / 'data/pricing.json').read_text('utf8')),
                                 send_model=True, plan_id=COMPETITION_PLAN, phase='core', run_id=run)
            answers = list(fixture['answers'])
            def answer(request, key, timeout, captured):
                if not answers: raise AssertionError('Unexpected third send')
                return dict(model=request['model'], choices=[dict(finish_reason='stop', message=dict(content=answers.pop(0)))],
                            usage=dict(prompt_tokens=10, completion_tokens=10))
            service = None
            try:
                with patch('event_workbench.news_budget.read_credential', return_value='SYNTHETIC_KEY'), patch('event_workbench.news_budget.trial_transport.send', side_effect=answer) as sends:
                    service = NewsService(app, run, send_model=True, budget=budget)
                    with closing(app.connect()) as c: revision = app.run(c, run)['revision']
                    saved = app.save_news(dict(fixture['document'], run=run, revision=revision))
                    reply = service.submit(dict(run=run, revision=saved['revision'], material_id=saved['material_id']))
                    deadline = time.monotonic() + 25
                    while time.monotonic() < deadline:
                        with closing(app.connect()) as c:
                            state = c.execute('SELECT state FROM news_tasks WHERE id=?', (reply['task']['id'],)).fetchone()[0]
                        if state not in ('queued', 'running'): break
                        time.sleep(.05)
                    self.assertEqual(state, 'completed')
                    self.assertEqual(sends.call_count, 2)
                    events = app.events(run, events_only=True)
                    self.assertEqual(len(events), 1)
                    self.assertTrue(events[0]['event_summary']['text'])
                    self.assertTrue(events[0]['evidence_sources']['members'])
                    with closing(app.connect()) as c:
                        self.assertEqual(c.execute('SELECT count(*) FROM stages').fetchone()[0], 7)
            finally:
                if service:
                    service.close(); service.worker.join(timeout=5)


if __name__ == '__main__': unittest.main()
