"""ENG039 offline development-only input boundary. No retrieval or transport."""
import json
from pathlib import Path
from event_workbench import training_corpus as corpus
from event_workbench import compact_model_io as compact

ROOT=corpus.ROOT
BASE=ROOT/'artifacts/eng038/partition/v1'
PATHS={'corpus':ROOT/'artifacts/eng037/corpus/v1/corpus.sqlite','groups':BASE/'groups.sqlite','manifest':BASE/'sample_manifest.json','freeze':ROOT/'artifacts/eng038/implementation/freeze_manifest.json'}
HASHES={'corpus':'17352e6dccd2d012ca428ef3a15c8b68a6e8e163c6f64db0177e134524de5bff','groups':'3b8deb06483b309b807a3ad273598ea595f05454b587ffc209f7c93e081a6011','manifest':'6d80d0d1d1f77a7797d91bc36ada45baf24f1d48ccb2c6209972f06075faaa5d','freeze':'92f0c160b749b9b19b538b29b2973169602ee33ee409137079cc55a51488ac66'}

class ScopeError(ValueError):pass

def stamp(path):
    s=Path(path).stat();return (s.st_size,s.st_mtime_ns,s.st_ctime_ns)

class DevelopmentInputs:
    def __init__(self,paths=None,hashes=None,expected_count=40):
        self.paths=dict(PATHS if paths is None else paths)
        self.hashes=dict(HASHES if hashes is None else hashes)
        self.stamps={k:stamp(v) for k,v in self.paths.items()}
        for k,path in self.paths.items():
            if corpus.filehash(path)!=self.hashes[k]:raise ScopeError(k+'_fingerprint_drift')
        self._unchanged()
        manifest=json.loads(Path(self.paths['manifest']).read_text(encoding='utf-8'))
        self.allowed={s['document_id']:s for s in manifest['samples'] if s.get('purpose')=='development'}
        if len(self.allowed)!=expected_count:raise ScopeError('development_count_conflict')
        self.groups=corpus.connect(self.paths['groups'],True)
        self.source=None
        try:
            for did in self.allowed:self.check_scope(did)
        except Exception:
            self.close();raise

    def _unchanged(self):
        if any(stamp(v)!=self.stamps[k] for k,v in self.paths.items()):raise ScopeError('input_changed_during_session')

    def close(self):
        if self.source:self.source.close()
        if getattr(self,'groups',None):self.groups.close()

    def list(self):
        self._unchanged();return sorted(self.allowed)

    def check_scope(self,document_id,purpose='input'):
        self._unchanged()
        if purpose!='input':raise ScopeError('requires_explicit_evidence_allowlist' if purpose in ('evidence','retrieval') else 'unsupported_use')
        if document_id not in self.allowed:raise ScopeError('not_fixed_development_representative')
        chosen=self.allowed[document_id]
        row=self.groups.execute('SELECT * FROM groups WHERE group_id=?',(chosen['group_id'],)).fetchone()
        if not row or row['purpose']!='development' or row['representative']!=document_id or row['pool']!='development':raise ScopeError('group_purpose_conflict')
        if json.loads(row['exclusions_json']):raise ScopeError('excluded_group')
        members=self.groups.execute('SELECT node_id,document_id FROM nodes WHERE group_id=? ORDER BY node_id',(row['group_id'],)).fetchall()
        if corpus.digest([m['node_id'] for m in members])!=row['group_id'] or len(members)!=row['size'] or document_id not in [m['document_id'] for m in members]:raise ScopeError('group_membership_conflict')
        for m in members:
            if m['document_id'] is None:raise ScopeError('unknown_group_member')
        if self.groups.execute('SELECT 1 FROM edges e JOIN nodes a ON a.node_id=e.left_node JOIN nodes b ON b.node_id=e.right_node WHERE (a.group_id=? OR b.group_id=?) AND a.group_id!=b.group_id LIMIT 1',(row['group_id'],row['group_id'])).fetchone():raise ScopeError('edge_group_conflict')
        return dict(allowed=True,use='input_only',document_id=document_id)

    def prepare(self,document_id,purpose='input'):
        self.check_scope(document_id,purpose) # Must precede opening/reading source contents.
        if self.source is None:self.source=corpus.connect(self.paths['corpus'],True)
        row=self.source.execute('SELECT d.id,t.title,t.body,t.sha FROM documents d JOIN contents t ON t.id=d.content_id WHERE d.id=?',(document_id,)).fetchone()
        if not row or not row['body'] or not row['body'].strip():raise ScopeError('missing_body')
        node=self.groups.execute('SELECT features_json FROM nodes WHERE document_id=?',(document_id,)).fetchone()
        fp=json.loads(node[0])['fingerprints']
        import hashlib
        if fp['body']!=hashlib.sha256(row['body'].encode()).hexdigest() or fp['content']!=corpus.digest([row['title'],row['body']]):raise ScopeError('body_fingerprint_conflict')
        origins=[dict(r) for r in self.source.execute('SELECT r.row_key,r.file_sha,f.path,r.sheet,r.row_number,r.source_raw,r.published_raw,r.published_iso,r.date_status FROM source_rows r JOIN files f ON f.sha=r.file_sha WHERE r.document_id=? ORDER BY r.file_sha,r.sheet,r.row_number',(document_id,))]
        if not origins:raise ScopeError('missing_provenance')
        selected=origins[0];warnings=[]
        if len(origins)>1:warnings.append('multiple_source_rows_not_independent_evidence')
        if len({r['source_raw'] for r in origins})>1:warnings.append('source_values_differ')
        if len({(r['published_raw'],r['published_iso']) for r in origins})>1:warnings.append('publication_values_differ')
        if any('.' in (r['published_iso'] or '') for r in origins):warnings.append('derived_datetime_fraction_preserved_not_verified_precision')
        document=dict(title=row['title'],text=row['body'],source=selected['source_raw'],publication_time=selected['published_iso'])
        rep,positions=compact.source_representation(document)
        roles={r:('context_only' if r.startswith('T') else 'core') for r,_ in rep['u']}
        self._unchanged()
        return dict(model_input=dict(document=document,source_representation=rep,unit_roles=roles),envelope=dict(document_id=document_id,group_id=self.allowed[document_id]['group_id'],input_use='fixed_development_only',evidence_use='requires_explicit_allowlist',body_sha256=fp['body'],content_sha256=fp['content'],bindings=self.hashes,positions=positions,selected_origin=selected,all_origins=origins,warnings=warnings,publication_time_policy='derived_source_time_not_event_time; raw values retained; fractional precision unverified'))
