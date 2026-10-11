"""Fixed official development materials; every read uses the live replay guard."""
import copy
import hashlib
import json
import re
from contextlib import closing, nullcontext
from datetime import datetime
from pathlib import Path

from event_workbench.document_pipeline import digest

FIELDS = ('title', 'text', 'source', 'publication_time')


def moment(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('explicit_timezone_required')
    return parsed


def terms(text):
    words = set(re.findall(r'[a-zA-Z0-9][a-zA-Z0-9_-]+', text.lower()))
    for part in re.findall(r'[\u3400-\u9fff]+', text):
        words.update(part[i:i + 2] for i in range(len(part) - 1))
    return words


class MaterialScope:
    def __init__(self, app, manifest, run_id='official-development', *, partition='development'):
        self.dynamic=manifest is None
        if self.dynamic:
            from event_workbench.news_intake import is_workspace
            with closing(app.connect()) as c:
                if not is_workspace(c,run_id):raise ValueError('dynamic_scope_requires_news_workspace')
                manifest=dict(split=partition,documents=[json.loads(r[0]) for r in c.execute('SELECT body FROM replay_scope_rows WHERE run_id=?',(run_id,))])
        if partition not in ('development', 'acceptance') or (partition == 'acceptance' and run_id != 'official-acceptance'):
            raise ValueError('invalid_or_mixed_material_partition')
        if isinstance(manifest, (str, Path)):
            manifest = json.loads(Path(manifest).read_text(encoding='utf-8'))
        if isinstance(manifest, dict):
            if manifest.get('split') != partition:
                raise ValueError('manifest_does_not_match_development_or_acceptance_partition')
            documents = manifest.get('documents')
        elif isinstance(manifest, list):
            documents = manifest
            if any(not isinstance(r, dict) or r.get('partition') != partition for r in documents):
                raise ValueError('list_entries_require_explicit_matching_partition')
        else:
            raise ValueError('invalid_material_manifest')
        if not isinstance(documents, list) or (not documents and not self.dynamic):
            raise ValueError('empty_material_scope')
        self.app, self.run_id, self.partition = app, run_id, partition
        self.rows, self.identities = {}, {}
        for row in documents:
            if self.dynamic and isinstance(row,dict) and row.get('display_only'):continue
            if not isinstance(row, dict) or not isinstance(row.get('source_row'), str) or not row['source_row']:
                raise ValueError('source_row_required')
            if any(row.get(k) not in (None, partition) for k in ('partition', 'split_role')):
                raise ValueError('mixed_partition_material')
            if row['source_row'] in self.rows:
                raise ValueError('duplicate_manifest_source')
            self.rows[row['source_row']] = copy.deepcopy(row)
        self.version = digest(documents)
        if self.dynamic:
            with closing(app.connect()) as c:
                if c.execute("SELECT 1 FROM sqlite_master WHERE name='product_dependencies'").fetchone():
                    for record in c.execute('SELECT job,body FROM product_dependencies'):
                        source=json.loads(record['body']).get('source_row')
                        if source in self.rows:self.identities[record['job']]=source

    def _read(self, source_row, cutoff=None, *, connection=None):
        row = self.rows[source_row]
        current = self.app.document(self.run_id, source_row, include_analysis=False, **({'_connection':connection} if connection is not None else {}))
        if row.get('available_at') and moment(row['available_at']) != moment(current['available_at']):
            raise ValueError('manifest_available_time_changed')
        if cutoff is not None and moment(current['available_at']) > moment(cutoff):
            raise ValueError('future_material_at_processing_cutoff')
        for key, field in [('title_sha256', 'title'), ('body_sha256', 'text')]:
            if row.get(key) and hashlib.sha256((current[field] or '').encode('utf-8')).hexdigest() != row[key]:
                raise ValueError('manifest_source_content_changed')
        document = {key: current[key] for key in FIELDS}
        self.identities[digest(document)] = source_row
        return dict(row, **current, source_row=source_row)

    def _source(self, identity):
        if identity in self.rows:
            return identity
        if identity in self.identities:
            return self.identities[identity]
        for source in self.rows:
            try:
                self._read(source)
            except ValueError:
                continue
            if identity in self.identities:
                return self.identities[identity]
        raise ValueError('identity_outside_visible_development_scope')

    def entry(self, identity):
        return self._read(self._source(identity))

    def document(self, identity, cutoff=None):
        row = self._read(self._source(identity), cutoff)
        return {key: row[key] for key in FIELDS}

    def prepare(self, identity, *, content_only=True):
        if not content_only:raise ValueError('legacy_request_execution_removed_use_current_news')
        source = self._source(identity)
        entry=self._read(source)
        prepared=self.app.prepare_document(self.run_id, source)
        from event_workbench.development_request import plan_requests
        prepared['extraction_input']=plan_requests(prepared['plan'],prepared['definition_candidates'])
        related=entry.get('related_event_id')
        if related:
            with closing(self.app.connect()) as c:
                row=c.execute('SELECT body FROM replay_events WHERE id=? AND run_id=?',(related,self.run_id)).fetchone()
            if row:
                event=json.loads(row[0]);definitions=[m['definition'] for m in event.get('monitoring_scope',{}).get('matches',[]) if m.get('decision')=='matched']
                for request in prepared['extraction_input']['requests']:
                    known={d['definition_id'] for d in request['content']['c']}
                    for d in definitions:
                        if d['definition_id'] not in known:
                            request['content']['c'].append(copy.deepcopy(d));known.add(d['definition_id'])
                prepared['related_event_hint']=dict(event_id=related,role='candidate_only_requires_source_subject_project_phase_checks')
        return prepared

    def retrieve(self, document, mentions, cutoff):
        moment(cutoff)
        self.document(digest(document), cutoff)
        candidates, excluded = [], []
        # One read snapshot, same guard/hash/time checks for every material.
        # Opening thousands of separate SQLite connections dominated local retrieval.
        with (closing(self.app.connect()) if hasattr(self.app,'connect') else nullcontext()) as c:
            if c is not None:c.execute('BEGIN')
            for source in self.rows:
                try:
                    row = self._read(source, cutoff, connection=c)
                except ValueError as error:
                    excluded.append(dict(source_row=source, reason=str(error)))
                    continue
                other = {key: row[key] for key in FIELDS}
                if digest(other) == digest(document):continue
                candidates.append((row, other))
        items = []
        for mention in mentions:
            body = mention['body']
            names = [p['name'] for p in body.get('participants', []) if isinstance(p.get('name'), str)]
            query = '\n'.join([body.get('summary', '')] + names + [d.get('value_text', '') for d in body.get('details', []) if isinstance(d.get('value_text'), str)])
            query_terms = terms(query)
            from event_workbench.aggregation_v2 import identity_name, IDENTITY_NAMES
            anchors = names + [d.get('value_text', '') for d in body.get('details', [])
                               if identity_name(d) in IDENTITY_NAMES]
            anchors += [o.get('target_name', '') for o in mention.get('monitoring_scope', {}).get('business_objects', [])]
            anchors = {a.casefold().strip() for a in anchors if isinstance(a, str) and len(a.strip()) > 1}
            anchors -= {'公司', '本公司', '集团', '企业', '行业', '本集团'}
            ranked = []
            for row, other in candidates:
                # Shared words alone are not a reason to send another article.
                source_text = '\n'.join(other[k] or '' for k in ('title', 'text')).casefold()
                if not any(anchor in source_text for anchor in anchors):continue
                best = None
                for field in ('title', 'text'):
                    text = other[field] or ''
                    # ponytail: a fixed 60-document scope uses literal windows; replace only if measured recall needs it.
                    for start in range(0, len(text), 620):
                        quote = text[start:start + 700]
                        matches = sorted(query_terms & terms(quote))
                        objects = [name for name in names if name and name in quote]
                        score = len(matches) + 4 * len(objects)
                        if not matches:
                            continue
                        item = dict(document_id=digest(other), source_row_identity=row['source_row'], available_at=row['available_at'],
                                    excerpts=[dict(field=field, start=start, end=start + len(quote), text=quote)],
                                    score=score, candidate_reason=dict(matched_terms=matches, object_names=objects),
                                    source_relation='same_source_relay' if other['text']==document['text'] or (other['source'] and other['source']==document['source']) else 'dependency_unknown', semantic_status='candidate_not_verified')
                        if best is None or score > best['score']:
                            best = item
                if best:
                    ranked.append(best)
            ranked.sort(key=lambda item: (-item['score'], item['available_at'], item['document_id']))
            items.append(dict(original_index=mention['original_index'], mention_id=mention.get('id'), candidates=ranked[:3],
                              candidate_total=len(ranked), omitted=max(0, len(ranked) - 3), query=query))
        return dict(items=items, evaluation_time=cutoff, cutoff=cutoff, scope_version=self.version,
                    time_basis='received_at' if self.dynamic else 'published_available_at',
                    excluded=excluded, method='bounded_literal_object_project_terms', semantics_verified=False,
                    independence='unknown; repeated text and same-source reports do not establish corroboration')
