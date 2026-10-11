"""ENG045: metadata-first, source-row-time-limited literal evidence candidates."""
import collections
import datetime as dt
import hashlib
import json
import re


def digest(value):
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':')).encode()).hexdigest()


def scope(groups, nodes, documents, blocked, cross_groups=()):
    members = collections.defaultdict(list)
    for node in nodes:
        members[node['group_id']].append(node)
    allowed, reasons = {}, collections.Counter()
    forbidden_contents = set()
    for group in groups:
        gid = group['group_id']; ns = members.pop(gid, [])
        why = []
        if group['purpose'] != 'unassigned': why.append('purpose_' + group['purpose'])
        if gid in blocked or group['retrieval_blocked']: why.append('blocked')
        if json.loads(group['exclusions_json']): why.append('abnormal')
        ids = [n['document_id'] for n in ns]
        if (len(ns) != group['size'] or len(set(ids)) != group['document_count']
                or any(x not in documents for x in ids)
                or any(n['node_id'] != 'doc:' + str(n['document_id']) for n in ns)
                or digest(sorted(n['node_id'] for n in ns)) != gid
                or group['representative'] not in ids or gid in cross_groups):
            why.append('incomplete_or_unknown_membership')
        if why:
            reasons.update(why)
            forbidden_contents.update(documents[x]['content_id'] for x in ids if x in documents)
        else:
            for x in ids: allowed[x] = gid
    unknown = [n for ns in members.values() for n in ns]
    reasons['unknown_group_nodes'] = len(unknown)
    forbidden_contents.update(documents[n['document_id']]['content_id'] for n in unknown
                              if n['document_id'] in documents)
    known = {n['document_id'] for n in nodes}
    orphan = set(documents) - known
    reasons['documents_without_node'] = len(orphan)
    forbidden_contents.update(documents[x]['content_id'] for x in orphan)
    allowed = {x:g for x,g in allowed.items() if documents[x]['content_id'] not in forbidden_contents}
    return allowed, dict(reasons)


def date_bound(value, upper=False):
    if not isinstance(value, str) or not value: raise ValueError('unknown_time')
    value = value.replace('Z', '+00:00')
    parsed = dt.datetime.fromisoformat(value)
    if len(value) == 10 and upper: parsed = parsed.replace(hour=23, minute=59, second=59, microsecond=999999)
    return parsed


def timed_rows(rows, cutoff):
    limit = date_bound(cutoff)
    accepted, counts = collections.defaultdict(list), collections.Counter()
    for row in rows:
        try:
            if row['date_status'] not in ('parsed_text', 'parsed_excel_date', 'parsed_excel_datetime'):
                raise ValueError('unknown_time')
            value = date_bound(row['published_iso'], upper=True)
            if (value.tzinfo is None) != (limit.tzinfo is None): raise ValueError('incomparable_timezone')
            if value > limit: counts['future_or_date_precision_not_proven'] += 1; continue
        except (ValueError, TypeError): counts['unknown_invalid_or_incomparable_time'] += 1; continue
        accepted[row['document_id']].append(row)
    for rs in accepted.values(): rs.sort(key=lambda r:(date_bound(r['published_iso'], True), r['row_key']))
    return dict(accepted), dict(counts)


def query_plan(diagnostic, document):
    """Up to two located names/detail values, then an exact selected-source fragment."""
    plans, skipped = [], []
    def add(phrase, field, unit, kind):
        if len(phrase) < 3 or any(p['phrase'] == phrase for p in plans): return
        for name in ('text', 'title'):
            raw = document.get(name) or ''; start = raw.find(unit['text'])
            offset = unit['text'].find(phrase)
            if start >= 0 and offset >= 0:
                plans.append(dict(phrase=phrase, kind=kind, field_path=field['path'], ref=unit['ref'],
                                  source_field=name, start=start+offset, end=start+offset+len(phrase),
                                  coordinate_system='Python Unicode codepoints, zero-based, end-exclusive',
                                  query_risk='single literal may match a different event; numbers, years and generic phrases have low discrimination',
                                  location_only_not_semantic_support=True))
                return
    fields = diagnostic['fields']
    for field in fields:
        if not ('.participants[' in field['path'] or '.details[' in field['path']): continue
        value = field['value']
        if not isinstance(value, str): continue
        if field['strict_issues']:
            skipped.append(dict(path=field['path'], reason='field_warning_not_used_as_entity')); continue
        for unit in field['selected_units']:
            if unit['exists'] and value in unit['text']: add(value[:48], field, unit, 'located_field_literal')
            if len(plans) >= 2: break
        if len(plans) >= 2: break
    for field in fields:
        for unit in field['selected_units']:
            if not unit['exists']: continue
            for phrase in re.findall(r'[\w\u4e00-\u9fff]{3,}', unit['text']):
                add(phrase[:16], field, unit, 'selected_source_literal')
                if len(plans) >= 3: return plans, skipped
    return plans[:3], skipped


def search_query(connection, phrase, allowed, documents, sources):
    """Temporary whitelist contains only scope AND time-qualified content ids.

    FTS returns ids only. Application never selects title/body for rejected ids.
    Literal LIKE is the existing ENG037 trigram strategy; order is deterministic,
    deliberately not a truth score or semantic ranking.
    """
    connection.execute('CREATE TEMP TABLE IF NOT EXISTS eng045_allowed(id INTEGER PRIMARY KEY)')
    connection.execute('DELETE FROM eng045_allowed')
    content_docs = collections.defaultdict(list)
    for did in sources:
        if did in allowed: content_docs[documents[did]['content_id']].append(did)
    connection.executemany('INSERT INTO eng045_allowed VALUES (?)', [(x,) for x in content_docs])
    literal = '%' + phrase.replace('\\', '\\\\').replace('%', '\\%').replace('_', '\\_') + '%'
    hits = connection.execute("SELECT rowid FROM text_fts WHERE rowid IN (SELECT id FROM eng045_allowed) "
                              "AND (title LIKE ? ESCAPE '\\' OR body LIKE ? ESCAPE '\\') ORDER BY rowid",
                              (literal, literal)).fetchall()
    ranked = []
    for hit in hits:
        for did in content_docs[hit[0]]:
            row = sources[did][0]
            ranked.append((date_bound(row['published_iso'], True), allowed[did], did, row))
    # Most recent eligible first, stable group/document tie break.
    ranked.sort(key=lambda x:(x[1],x[2])); ranked.sort(key=lambda x:x[0], reverse=True)
    output, seen, fetched = [], set(), []
    for _, gid, did, row in ranked:
        if gid in seen: continue
        cid = documents[did]['content_id']
        text = connection.execute('SELECT title,body,sha FROM contents WHERE id=?', (cid,)).fetchone()
        fetched.append(cid)
        if digest([text['title'], text['body']]) != text['sha']: raise ValueError('content_hash_mismatch')
        excerpts = []
        for field in ('title','body'):
            raw = text[field] or ''; match = re.search(re.escape(phrase),raw,re.IGNORECASE)
            if match is None: continue
            pos = match.start()
            start, end = max(0,pos-80), min(len(raw),match.end()+120)
            excerpts.append(dict(field=field, start=start, end=end, text=raw[start:end],
                                 match_start=pos, match_end=match.end()))
        if not excerpts: continue
        seen.add(gid)
        output.append(dict(document_id=did, group_id=gid, content_id=cid, content_sha256=text['sha'],
                           body_sha256=hashlib.sha256((text['body'] or '').encode()).hexdigest(),
                           source_row=row, excerpts=excerpts, query=phrase, rank=len(output)+1,
                           source_dependence='within_group_versions_not_independent; across_groups_independence_unknown',
                           same_event_verified=False, fact_verified=False))
        if len(output) == 5: break
    return dict(candidates=output, eligible_hit_documents=len(ranked), fetched_content_ids=fetched)


def merge_candidates(results):
    seen, merged = set(), []
    for result in results:
        for candidate in result['candidates']:
            if candidate['group_id'] not in seen:
                seen.add(candidate['group_id']); merged.append(candidate)
    return merged[:5]


def conclusion(cutoff, candidates):
    return f'截至{cutoff}：' + (f'找到{len(candidates)}组候选' if candidates else '未找到合格候选') + '，仍待核验'
