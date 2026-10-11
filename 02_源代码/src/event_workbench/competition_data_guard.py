"""Content fingerprints only: no protected text or reference labels are read."""
import hashlib,json,sqlite3
from contextlib import closing
from functools import lru_cache
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]


@lru_cache(maxsize=4)
def _evaluation_groups(stamp):
    manifest=ROOT/'artifacts/competition-20260923/evaluation/manifest.json'
    rows=json.loads(manifest.read_text('utf-8'))['documents'];groups={r['group_id'] for r in rows}
    if len(rows)!=24 or len(groups)!=24:raise ValueError('competition_evaluation_manifest_changed')
    db=ROOT/'artifacts/eng038/partition/v1/groups.sqlite';result=[]
    with closing(sqlite3.connect(db.as_uri()+'?mode=ro',uri=True)) as c:
        for r in c.execute("SELECT json_extract(features_json,'$.fingerprints') FROM nodes WHERE group_id IN ("+','.join('?' for _ in groups)+')',sorted(groups)):
            if r[0]:result.append(json.loads(r[0]))
    return result


def evaluation_fingerprints():
    manifest=ROOT/'artifacts/competition-20260923/evaluation/manifest.json'
    if not manifest.exists():return []
    return _evaluation_groups((manifest.stat().st_size,manifest.stat().st_mtime_ns))


@lru_cache(maxsize=2)
def _bundle(stamp):
    root=ROOT/'data';raw=(root/'protection.json').read_bytes()
    manifest=json.loads((root/'package-manifest.json').read_text('utf-8'))
    if hashlib.sha256(raw).hexdigest()!=manifest.get('protection_sha256'):raise ValueError('packaged_protection_changed')
    data=json.loads(raw)
    return {k:set(v) for k,v in data['hashes'].items()},set(data['whitespace'])


def packaged_fingerprints():
    root=ROOT/'data'
    if not (root/'package-manifest.json').exists():return None
    paths=[root/'package-manifest.json',root/'protection.json']
    return _bundle(tuple((p.stat().st_size,p.stat().st_mtime_ns) for p in paths))
