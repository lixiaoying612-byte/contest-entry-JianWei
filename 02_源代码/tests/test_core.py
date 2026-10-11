"""Offline checks: real budget accounting, no correction fanout, single review."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

HERE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(HERE / 'src'))
from event_workbench import news_joint
from event_workbench.news_budget import ModelBudget

RATES = dict(model='deepseek-flash', currency='CNY', checked_date='2026-09-16',
    source='https://api-docs.deepseek.com/zh-cn/quick_start/pricing/', input_per_million=2, output_per_million=8)
REQUEST = dict(model='deepseek-flash', max_tokens=100, messages=[dict(role='system', content='Return JSON {"ok":true}'), dict(role='user', content='synthetic')])
PROVIDER = dict(model='deepseek-flash', choices=[dict(finish_reason='stop', message=dict(content='{"ok":true}'))], usage=dict(prompt_tokens=20, completion_tokens=10))


class CoreChecks(unittest.TestCase):
    def test_unbound_score_becomes_unknown_without_inventing_evidence(self):
        from event_workbench import alert_review,alert_rules
        good=dict(document_id='doc',section='body',quote='公司披露计划',start=0,end=6)
        other=dict(document_id='other',section='body',quote='其他事项',start=0,end=4)
        claim=dict(claim_id='c',claim_role='current_core',basis=good)
        features=dict(dimensions=dict(K=dict(lower=10,upper=10,refs=[1],reason='unsupported')))
        with self.assertRaises(ValueError):alert_review.validate_bound_premises(features,[good,other],[claim])
        alert_review.validate_bound_premises(features,[good,other],[claim],retain_unbound=True)
        anchors=alert_rules.catalog()['anchors']['K'];d=features['dimensions']['K']
        self.assertEqual((d['lower'],d['upper']),(min(anchors),max(anchors)))
        self.assertEqual(d['refs'],[0]);self.assertIn('待定',d['gap'])
        alert_review.validate_bound_premises(features,[good,other],[claim])
        with self.assertRaises(ValueError):alert_review.validate_bound_premises(dict(dimensions=dict(K=dict(refs=[0]))),[other],[claim],retain_unbound=True)

    def test_legacy_adapter_cannot_send(self):
        from event_workbench import news_processing
        adapter=object.__new__(news_processing._BudgetAdapters)
        with patch.object(news_processing,'_send') as send:
            with self.assertRaisesRegex(ValueError,'legacy_news_execution_removed'):
                adapter.transmit('verification',{}, {})
            send.assert_not_called()
        from event_workbench.product_workbench import Product
        from event_workbench.live_stage_adapters import Adapters
        self.assertFalse(hasattr(Product,'prepare'))
        self.assertFalse(hasattr(Product,'execute'))
        self.assertFalse(hasattr(Product,'submit'))
        with self.assertRaisesRegex(RuntimeError,'legacy_news_execution_removed'):
            Adapters.transmit(None,'extract',{}, {})
        from importlib.util import find_spec
        self.assertIsNone(find_spec('event_workbench.product_blocks'))
        self.assertIsNone(find_spec('event_workbench.live_local_retrieval'))
        self.assertIsNone(find_spec('event_workbench.pipeline_retrieval'))
        from event_workbench import deepseek_extraction
        self.assertFalse(hasattr(deepseek_extraction,'official_transport'))
        with self.assertRaisesRegex(RuntimeError,'legacy_news_retrieval_removed'):
            Adapters.engine(None)

    def test_legacy_receiver_has_no_model_execution_entry(self):
        from event_workbench import news_receivers_v3
        for name in ('request','verification_input','relation_input','execute_relations','impact_input','execute','receive_batch'):
            self.assertFalse(hasattr(news_receivers_v3,name),name)
        self.assertTrue(callable(news_receivers_v3.receive_provider))
        self.assertTrue(callable(news_receivers_v3.receive_verification))

    def test_explicit_rule_score_normalization_does_not_guess_invalid_scores(self):
        def features(value):return dict(dimensions=dict(K=dict(anchor=value)),object_relation={})
        with patch.object(news_joint.alert_rules,'validate'):
            allowed=news_joint.alert_rules.catalog()['anchors']['K']
            self.assertEqual(news_joint.impact_features(features(1),[],{})['dimensions']['K']['lower'],allowed[1])
            score=next(x for x in allowed if x>=len(allowed))
            self.assertEqual(news_joint.impact_features(features(score),[],{})['dimensions']['K']['lower'],score)
            with self.assertRaisesRegex(ValueError,'joint_anchor_index'):
                news_joint.impact_features(features(999),[],{})

    def test_reset_failed_stage_runs_once_under_new_attempt_signature(self):
        from event_workbench.unified_execution import Pipeline
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            execute=Mock(side_effect=[dict(status='failed',result=dict(error='fixture')),dict(status='ready',result=dict(ok=True))])
            pipeline=Pipeline(Path(tmp)/'execution.db',dict(extract=dict(cost=1,prepare=lambda ctx:dict(same_payload=True),execute=execute)),dict(id='explicit',max_new_calls=2,allowed_stages=['extract']))
            doc=dict(title='Synthetic',text='Synthetic article',source='fixture',publication_time=None)
            job=pipeline.submit(doc,background=False)['id']
            pipeline.run(job)
            self.assertEqual(execute.call_count,1)
            original_signature=pipeline._signature
            pipeline._signature=lambda c,j,s,states:'explicit-new-attempt' if s=='extract' else original_signature(c,j,s,states)
            with pipeline.connect() as c:pipeline._write(c,job,'extract','waiting','explicit retry',None)
            pipeline.run(job);pipeline.run(job)
            self.assertEqual(execute.call_count,2)
            with pipeline.connect() as c:
                self.assertEqual(pipeline._states(c,job)['extract']['status'],'ready')
                self.assertEqual(c.execute('SELECT count(*) FROM attempts').fetchone()[0],2)

    def test_missing_score_basis_becomes_range_without_rewriting_reason(self):
        from event_workbench import alert_rules
        reason='未披露必要截止时间，按正常最低档处理'
        features=dict(dimensions=dict(T=dict(lower=0,upper=0,reason=reason,refs=[0])),priority_rules=[dict(rule='H3')])
        alert_rules.retain_uncertain_scores(features)
        value=features['dimensions']['T']
        self.assertLess(value['lower'],value['upper'])
        self.assertEqual(value['reason'],reason)
        self.assertTrue(value['gap']);self.assertEqual(features['priority_rules'],[])

    def test_database_lock_and_long_query_obey_worker_deadline(self):
        import sqlite3,time
        from event_workbench import news_deadline
        from event_workbench.unified_pipeline import Pipeline
        with tempfile.TemporaryDirectory() as tmp:
            pipeline=Pipeline(Path(tmp)/'locked.db')
            locker=sqlite3.connect(pipeline.path)
            locker.execute('BEGIN IMMEDIATE')
            token=news_deadline.current.set(time.monotonic()+.15)
            started=time.monotonic()
            try:
                with self.assertRaises(sqlite3.OperationalError):
                    with pipeline.connect() as c:c.execute('BEGIN IMMEDIATE')
                self.assertLess(time.monotonic()-started,.7)
            finally:
                news_deadline.current.reset(token);locker.rollback();locker.close()
            token=news_deadline.current.set(time.monotonic()+.08)
            started=time.monotonic()
            try:
                with self.assertRaisesRegex(sqlite3.OperationalError,'interrupted'):
                    with pipeline.connect() as c:c.execute('WITH RECURSIVE n(x) AS (SELECT 1 UNION ALL SELECT x+1 FROM n WHERE x<100000000) SELECT sum(x) FROM n').fetchone()
                self.assertLess(time.monotonic()-started,.7)
            finally:news_deadline.current.reset(token)
            with pipeline.connect() as c:self.assertEqual(c.execute('SELECT 1').fetchone()[0],1)

    def test_budget_database_lock_obeys_worker_deadline(self):
        import sqlite3,time
        from contextlib import closing
        from event_workbench import news_deadline,news_budget
        with tempfile.TemporaryDirectory() as tmp:
            budget=object.__new__(news_budget.ModelBudget)
            budget.path=Path(tmp)/'budget.db'
            with closing(sqlite3.connect(budget.path)) as locker:
                locker.execute('BEGIN IMMEDIATE')
                token=news_deadline.current.set(time.monotonic()+.15)
                started=time.monotonic()
                try:
                    with self.assertRaises(sqlite3.OperationalError):
                        with closing(budget.connect()) as c:c.execute('BEGIN IMMEDIATE')
                    self.assertLess(time.monotonic()-started,.7)
                finally:
                    news_deadline.current.reset(token);locker.rollback()
    def test_retry_double_click_is_one_attempt_and_sixty_second_preview_is_incomplete(self):
        import os, sqlite3, psutil
        from contextlib import closing
        from datetime import datetime,timedelta
        from unittest.mock import Mock
        from event_workbench.news_service import NewsService
        from event_workbench.news_budget import now
        from event_workbench.document_pipeline import digest
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'service.db'
            def connect():
                c=sqlite3.connect(path);c.row_factory=sqlite3.Row;return c
            document=dict(title='Synthetic',text='Original body',source='fixture',publication_time=None)
            jid=digest(document)
            with closing(connect()) as c,c:
                c.executescript('CREATE TABLE news_tasks(id TEXT PRIMARY KEY,run_id TEXT,source_row TEXT,contract TEXT,state TEXT,reason TEXT,owner_pid INTEGER,owner_birth REAL,created TEXT,updated TEXT); CREATE TABLE news_task_limits(task_id TEXT PRIMARY KEY,submitted_at TEXT,deadline_at TEXT); CREATE TABLE news_task_retries(task_id TEXT PRIMARY KEY,previous_task_id TEXT UNIQUE); CREATE TABLE news_task_publications(task_id TEXT,event_id TEXT,change_kind TEXT,published_at TEXT,event_version INTEGER,material_version INTEGER); CREATE TABLE news_input_records(run_id TEXT,source_row TEXT,body TEXT); CREATE TABLE stages(job TEXT,stage TEXT,status TEXT,result TEXT);')
                c.execute('INSERT INTO news_tasks VALUES(?,?,?,?,?,?,?,?,?,?)',('old','run','source',news_joint.VERSION,'partial','fixture',os.getpid(),psutil.Process().create_time(),now(),now()))
                c.execute('INSERT INTO news_input_records VALUES(?,?,?)',('run','source',json.dumps(dict(document=document,received_at=now()))))
                c.execute('INSERT INTO stages VALUES(?,?,?,?)',(jid,'extract','ready',json.dumps(dict(admission=dict(parts=[dict(items=[dict(core=dict(summary='Initial summary'))])])))))
            service=NewsService.__new__(NewsService)
            service.app=SimpleNamespace(connect=connect,run=lambda c,run:dict(revision=4))
            service.run_id='run';service.contract_revision=news_joint.VERSION;service.send_model=True;service.budget=None
            service.pid=os.getpid();service.birth=psutil.Process().create_time();service._enqueue=Mock()
            payload=dict(run='run',revision=4,material_id='source',retry_of='old')
            first=service.submit(payload);second=service.submit(payload)
            self.assertEqual(first['task']['id'],second['task']['id'])
            self.assertTrue(second['duplicate']);service._enqueue.assert_called_once()
            with closing(connect()) as c,c:
                c.execute('UPDATE news_task_limits SET submitted_at=? WHERE task_id=?',((datetime.fromisoformat(now())-timedelta(seconds=61)).isoformat(),first['task']['id']))
            task=next(t for t in service.status()['tasks'] if t['id']==first['task']['id'])
            self.assertTrue(task['show_partial']);self.assertFalse(task['preview']['complete'])
            self.assertEqual(task['preview']['document']['text'],'Original body')
            self.assertEqual(task['preview']['summaries'],['Initial summary'])
            with closing(connect()) as c,c:
                c.execute('INSERT INTO news_task_limits VALUES(?,?,?)',('old',(datetime.fromisoformat(now())-timedelta(seconds=100)).isoformat(),(datetime.fromisoformat(now())-timedelta(seconds=11)).isoformat()))
            tasks=service.status()['tasks']
            self.assertNotIn('preview',next(t for t in tasks if t['id']=='old'))
            self.assertEqual(next(t for t in tasks if t['id']==first['task']['id'])['preview']['document']['text'],'Original body')
            service.budget=ModelBudget(Path(tmp)/'ledger.db',pricing=RATES)
            with closing(service.budget.connect()) as c,c:
                c.execute('INSERT INTO news_model_calls(id,plan,status) VALUES(?,?,?)',('reserved',service.budget.plan_id,'reserved_unknown_no_retry'))
                c.execute('INSERT INTO news_model_attempt_calls VALUES(?,?,?)',(service.budget.plan_id,first['task']['id'],'reserved'))
            task=next(t for t in service.status()['tasks'] if t['id']==first['task']['id'])
            self.assertEqual((task['model_calls_used'],task['model_calls_uncertain'],task['model_call_limit']),(1,1,2))
            self.assertFalse(task['analysis_complete'])

    def test_explicit_retry_reuses_extraction_and_preserves_old_stage_results(self):
        from event_workbench.news_processing import NewsProcessor
        from event_workbench import unified_pipeline as core
        with tempfile.TemporaryDirectory() as tmp:
            document=dict(title='Synthetic',text='Synthetic article',source='fixture',publication_time='2026-10-09')
            jid=core.digest(document)
            pipeline=core.Pipeline(Path(tmp)/'retry.db')
            with pipeline.connect() as c:
                c.execute('CREATE TABLE product_dependencies(job TEXT PRIMARY KEY,body TEXT)')
                c.execute('INSERT INTO product_dependencies VALUES(?,?)',(jid,'{}'))
                for stage in core.STAGES:pipeline._write(c,jid,stage,'ready' if stage in ('extract','mentions') else 'failed','fixture',stage,dict(saved=stage))
            processor=NewsProcessor.__new__(NewsProcessor)
            processor.pipeline=pipeline
            processor.scope=SimpleNamespace(entry=lambda identity:dict(available_at='2026-10-09'),document=lambda identity,cutoff:document)
            processor.restart_incomplete('source','manual-1')
            processor.restart_incomplete('source','manual-1')
            with pipeline.connect() as c:
                states=pipeline._states(c,jid)
                self.assertEqual(states['extract']['result'],dict(saved='extract'))
                self.assertEqual(states['mentions']['status'],'ready')
                self.assertTrue(all(states[x]['status']=='waiting' for x in core.STAGES[2:]))
                archived=json.loads(c.execute('SELECT body FROM imports').fetchone()[0])
                self.assertEqual(archived['stages']['verification']['result'],dict(saved='verification'))
                binding=json.loads(c.execute('SELECT body FROM product_dependencies').fetchone()[0])
                self.assertEqual(binding['not_sent_continuations']['verification'],1)

    def test_manual_attempt_can_send_after_failed_previous_attempt(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            budget=ModelBudget(root/'calls.db', pricing=RATES, send_model=True)
            with patch('event_workbench.news_budget.read_credential', return_value='SYN_KEY'), patch('event_workbench.news_budget.trial_transport.send', side_effect=[TimeoutError(),PROVIDER]) as send:
                with self.assertRaises(ValueError):budget.for_task('old').send('review',REQUEST,root/'old')
                budget.for_task('manual').send('review',REQUEST,root/'manual')
                self.assertEqual(send.call_count,2)

    def test_publication_rolls_back_when_deadline_expires(self):
        import sqlite3
        from contextlib import closing
        from unittest.mock import Mock
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp)/'publication.db'
            def connect():
                connection = sqlite3.connect(path)
                connection.row_factory = sqlite3.Row
                return connection
            with closing(connect()) as c, c:
                c.execute('CREATE TABLE writes(value TEXT)')
            app = SimpleNamespace(connect=connect)
            event = dict(member_id='event', evidence_sources=dict(members=[dict(document_id='job', mention_id='mention')]))
            processor = SimpleNamespace(app=app, scope=SimpleNamespace(run_id='run'), pipeline=None,
                impact_cause='new_material', check_deadline=Mock(side_effect=[None, TimeoutError('analysis_deadline_exceeded')]))
            joint = dict(envelope=dict(matters=[dict(ref='M0', mention_id='mention', index=0)], heads={}, cutoff='2026-10-09', binding_sha='binding'),
                relations=dict(proposals=[]), impacts=[dict(matters=['M0'], reason='synthetic', assessments=[])], origin='offline_model_fixture')
            current = dict(schema_version='synthetic', rule_version='synthetic', event_id='event', event_version=1,
                base_body_sha='synthetic', source_rows=[], cutoff='2026-10-09', reviewed_at='2026-10-09', documents={}, notified_history=[])
            class Publisher:
                def __init__(self, atomic_app, scope):self.app=atomic_app
                def sync(self, *args, **kwargs):
                    with closing(self.app.connect()) as c, c:c.execute("INSERT INTO writes VALUES('draft')")
                    return [event]
            def receipt(atomic_app, *args, **kwargs):
                with closing(atomic_app.connect()) as c, c:c.execute("INSERT INTO writes VALUES('assessment')")
                return {'saved':True}
            with patch('event_workbench.news_events.EventPublisher', Publisher), patch.object(news_joint.alert_review, 'input_for_review', return_value=current), patch.object(news_joint.alert_review, 'project'), patch.object(news_joint.review_import, 'receive_impact_review', side_effect=receipt):
                with self.assertRaises(TimeoutError):news_joint.publish_joint(processor, {'job':'job'}, joint)
                with closing(connect()) as c:self.assertEqual(c.execute('SELECT count(*) FROM writes').fetchone()[0], 0)
                processor.check_deadline=Mock()
                result=news_joint.publish_joint(processor, {'job':'job'}, joint)
                self.assertEqual(result['processing_status'], 'complete')
                with closing(connect()) as c:self.assertEqual(c.execute('SELECT count(*) FROM writes').fetchone()[0], 2)

    def test_absolute_transport_deadline_stops_slow_response(self):
        import threading
        import time
        from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
        from event_workbench import trial_transport
        reached = threading.Event()
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers.get('Content-Length', 0)))
                reached.set()
                body = json.dumps(PROVIDER).encode() + b' '*1000
                self.send_response(200)
                self.send_header('Content-Length', str(len(body)))
                self.end_headers()
                try:
                    for byte in body:
                        self.wfile.write(bytes([byte]));self.wfile.flush();time.sleep(.02)
                except OSError:pass
        server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True);thread.start()
        try:
            endpoint = 'http://127.0.0.1:'+str(server.server_port)
            started = time.monotonic()
            with patch.object(trial_transport.client.common, 'ENDPOINT', endpoint):
                with self.assertRaisesRegex(trial_transport.client.common.TransportFailure, 'deadline_exceeded'):
                    trial_transport.send(REQUEST, 'SYN_KEY', 3, [])
            self.assertTrue(reached.is_set(), 'Local fixture must receive the request')
            self.assertLess(time.monotonic()-started, 4.5)
        finally:
            server.shutdown();server.server_close();thread.join()

    def test_two_send_limit_survives_failure_and_reconstruction(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            def budget():
                return ModelBudget(root/'calls.db', pricing=RATES, send_model=True).for_task('explicit-attempt', max_calls=49)
            with patch('event_workbench.news_budget.read_credential', return_value='SYN_KEY'), patch('event_workbench.news_budget.trial_transport.send', side_effect=[PROVIDER, TimeoutError()]) as send:
                budget().send('extract', REQUEST, root/'first')
                with self.assertRaises(ValueError):
                    budget().send('review', REQUEST, root/'second')
                with self.assertRaisesRegex(ValueError, 'budget_or_batch_limit'):
                    budget().send('repair', REQUEST, root/'third')
                self.assertEqual(send.call_count, 2)
            self.assertEqual(len(budget().snapshot()['calls']), 2)

    def test_unbound_budget_cannot_send_or_read_credentials(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            budget=ModelBudget(root/'calls.db',pricing=RATES,send_model=True)
            with patch('event_workbench.news_budget.read_credential') as key, patch('event_workbench.news_budget.trial_transport.send') as send:
                with self.assertRaisesRegex(ValueError,'explicit_analysis_attempt_required_not_sent'):
                    budget.send('extract',REQUEST,root/'unbound')
                key.assert_not_called();send.assert_not_called()
            self.assertEqual(budget.snapshot()['calls'],[])
            self.assertFalse((root/'unbound').exists())

    def test_rejected_answer_does_not_call_model_again(self):
        with tempfile.TemporaryDirectory() as tmp:
            with patch('event_workbench.news_processing._send', return_value=PROVIDER) as send:
                def reject(raw):
                    raise ValueError('invalid_optional_or_required_field')
                with self.assertRaisesRegex(ValueError, 'invalid_optional'):
                    news_joint.checked_send(SimpleNamespace(budget=None), {}, 'review', REQUEST, Path(tmp), reject)
                self.assertEqual(send.call_count, 1)
                self.assertTrue((Path(tmp)/'rejected.json').exists())

    def test_expired_settlement_keeps_reservations_and_blocks_third_send(self):
        from time import monotonic
        from event_workbench import news_deadline
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp)
            def budget():
                return ModelBudget(root/'calls.db',pricing=RATES,send_model=True).for_task('same-attempt')
            with patch('event_workbench.news_budget.read_credential',return_value='SYN_KEY'):
                for index in range(2):
                    active=budget()
                    token=news_deadline.current.set(active.deadline)
                    def late(*args):
                        active.deadline=monotonic()-1
                        news_deadline.current.set(active.deadline)
                        return PROVIDER
                    try:
                        with patch('event_workbench.news_budget.trial_transport.send',side_effect=late) as send:
                            with self.assertRaises(TimeoutError):active.send(str(index),REQUEST,root/str(index))
                            self.assertEqual(send.call_count,1)
                    finally:news_deadline.current.reset(token)
                calls=budget().snapshot()['calls']
                self.assertEqual(len(calls),2)
                self.assertTrue(all(c['status']=='reserved_unknown_no_retry' and c['charged_micros']==c['reserved_micros'] for c in calls))
                with patch('event_workbench.news_budget.trial_transport.send') as send:
                    with self.assertRaisesRegex(ValueError,'budget_or_batch_limit'):
                        budget().send('third',REQUEST,root/'third')
                    send.assert_not_called()

    def test_review_does_not_split_many_claims_into_model_calls(self):
        content = dict(claims=[dict(ref='C'+str(i)) for i in range(40)], relation=dict(pairs=[]), objects=[dict(ref='O0')])
        plan = news_joint.review_plan(content, 1)
        self.assertEqual(len(plan), 1)
        self.assertEqual(len(plan[0]['scope']['claims']), 40)
        with self.assertRaises(ValueError):
            news_joint.review_plan(content, 2)
        with self.assertRaisesRegex(ValueError, 'capacity'):
            news_joint.review_plan(dict(content, text='x'*100000), 1)

    def test_unrelated_global_word_match_is_not_a_relation_candidate(self):
        from event_workbench.product_downstream import relation_batch
        def mention(identity, company):
            return dict(id=identity, body=dict(summary='项目取得技术专利', participants=[dict(name=company)], details=[]))
        result = relation_batch([mention('new', '甲公司')], [mention('old', '乙公司')], {})
        self.assertEqual(result['candidates'], [])
        self.assertEqual(result['coverage'][0]['status'], 'no_candidates')

    def test_missing_relation_is_retained_without_merging_or_blocking(self):
        batch=json.loads((HERE/'tests/fixtures/shengjie-relations.json').read_text('utf8'))['batch']
        result=news_joint.receive_relations(dict(pairs=[]),dict(job='synthetic-job'),dict(relation_candidates=dict(batch=batch)))
        self.assertEqual(result['processing_status'],'complete')
        self.assertEqual(len(result['proposals']),len(batch['content']['pairs']))
        self.assertTrue(all(p['status']=='recorded_insufficient' and p['raw']['k']=='insufficient' for p in result['proposals']))
        self.assertTrue(result['missing_candidates'])
        from event_workbench.incremental_relations import current_graph
        graph=current_graph(list(batch['mapping'].values()),result['proposals'])
        self.assertFalse(any(p['status']=='model_tentative' for p in graph['proposals']))

    def test_saved_patent_difference_passes_without_weakening_merge_identity(self):
        import copy
        from event_workbench import aggregation_v2
        fixture = json.loads((HERE/'tests/fixtures/shengjie-relations.json').read_text('utf8'))
        batch, answer = fixture['batch'], fixture['answer']
        row = answer['pairs'][0]
        pair = next(p for p in batch['content']['pairs'] if p['p']==row['p'])
        self.assertEqual(aggregation_v2.validate(row['aggregation'], pair, batch, row)['operation'], 'NEW_MATTER')
        unsafe = copy.deepcopy(row)
        unsafe['k'] = 'same_event'
        unsafe['aggregation'].update(identity='same', operation='ENRICH', node_relation='same', time_relation='same_node')
        for basis in unsafe['aggregation']['identity_basis']:
            basis['comparison'] = 'compatible'
        with self.assertRaisesRegex(ValueError, 'identity_cannot_use_company_or_quantity_only'):
            aggregation_v2.validate(unsafe['aggregation'], pair, batch, unsafe)
        relation=news_joint.receive_relations(dict(pairs=[unsafe]),dict(job='synthetic-job'),dict(relation_candidates=dict(batch=batch)))
        self.assertEqual(relation['processing_status'],'complete')
        self.assertTrue(relation['isolated'])
        self.assertFalse(any(p['status']=='model_tentative' and p['raw']['k']=='same_event' for p in relation['proposals']))


if __name__ == '__main__':
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(CoreChecks)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    (HERE/'core-check-result.json').write_text(json.dumps(dict(passed=result.wasSuccessful(), tests=result.testsRun,
        failures=len(result.failures), errors=len(result.errors), actual_model_calls=0), indent=2), 'utf-8')
    sys.exit(not result.wasSuccessful())
