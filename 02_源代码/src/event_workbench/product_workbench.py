"""Shared source access and saved-result projections; old Product execution is retired."""
import copy
import json
from contextlib import closing
from pathlib import Path
from event_workbench import unified_execution as execution
from event_workbench import training_corpus as corpus
from event_workbench import development_input as boundary
from event_workbench import development_request as requests
from event_workbench import development_evidence_search as search_rules

ROOT=Path(__file__).resolve().parents[2]
base=execution.base


class Corpus:
    def __init__(self):
        # Verify frozen isolation once; later access checks stamps without scanning labels.
        guard=boundary.DevelopmentInputs(); self.allowed=dict(guard.allowed); self.stamps=guard.stamps; guard.close()
        with closing(corpus.connect(boundary.PATHS['groups'],True)) as g,closing(corpus.connect(corpus.DB,True)) as c:
            groups=[dict(r) for r in g.execute('SELECT group_id,size,document_count,representative,purpose,exclusions_json,retrieval_blocked FROM groups')]
            nodes=[dict(r) for r in g.execute('SELECT node_id,document_id,group_id FROM nodes')]
            documents={r['id']:dict(r) for r in c.execute('SELECT id,content_id FROM documents')}
            crossing={r[0] for r in g.execute('SELECT DISTINCT a.group_id FROM edges e JOIN nodes a ON a.node_id=e.left_node JOIN nodes b ON b.node_id=e.right_node WHERE a.group_id!=b.group_id')}
        self.evidence_ids,self.scope_audit=search_rules.scope(groups,nodes,documents,set(),crossing)
        self.visible=set(self.evidence_ids)|set(self.allowed)

    def unchanged(self):
        if any(boundary.stamp(p)!=self.stamps[k] for k,p in boundary.PATHS.items()):raise ValueError('文本库或隔离边界发生变化，请停止处理并检查版本')

    def document(self,did,source_row=None):
        self.unchanged()
        if did not in self.visible:raise ValueError('该材料未开放预览或属于隔离范围')
        with closing(corpus.connect(corpus.DB,True)) as c:
            row=c.execute('SELECT t.title,t.body FROM documents d JOIN contents t ON t.id=d.content_id WHERE d.id=?',(did,)).fetchone()
            origins=[dict(r) for r in c.execute('SELECT row_key,source_raw,published_iso,date_status FROM source_rows WHERE document_id=? ORDER BY id',(did,))]
        chosen=origins[0]
        if source_row is not None:
            key=source_row if isinstance(source_row,str) else source_row['row_key']
            chosen=next((r for r in origins if r['row_key']==key),None)
            if chosen is None or (isinstance(source_row,dict) and any(chosen[k]!=source_row.get(k) for k in chosen)):raise ValueError('selected_source_row_changed')
        return dict(id=did,document=dict(title=row['title'],text=row['body'],source=chosen['source_raw'],publication_time=chosen['published_iso']),origins=origins,selected_source_row=chosen,processable=did in self.allowed)

    def search(self,q='',source='',date_from='',date_to='',development=False):
        self.unchanged()
        # Whitelist is applied before any title/body is returned; no annotations query.
        with closing(corpus.connect(corpus.DB,True)) as c:
            c.execute('CREATE TEMP TABLE visible(id TEXT PRIMARY KEY)')
            ids=self.allowed if development or not q else self.visible
            c.executemany('INSERT INTO visible VALUES(?)',((i,) for i in ids))
            where=['d.id IN (SELECT id FROM visible)'];args=[]
            if q:
                if len(q)>=3:where.append('d.content_id IN (SELECT rowid FROM text_fts WHERE title LIKE ? UNION SELECT rowid FROM text_fts WHERE body LIKE ?)');args.extend(['%'+q+'%']*2)
                where.append('(instr(lower(coalesce(t.title,\'\')),lower(?))>0 OR instr(lower(coalesce(t.body,\'\')),lower(?))>0)');args.extend([q,q])
            for clause,value in [('r.source_raw=?',source),('r.published_iso>=?',date_from),('r.published_iso<=?',date_to+'T23:59:59' if date_to else '')]:
                if value:where.append(clause);args.append(value)
            sql=' FROM documents d JOIN contents t ON t.id=d.content_id JOIN source_rows r ON r.document_id=d.id WHERE '+' AND '.join(where)
            total=c.execute('SELECT count(DISTINCT d.id)'+sql,args).fetchone()[0]
            rows=[dict(r) for r in c.execute('SELECT d.id,t.title,r.source_raw AS source,r.published_iso AS publication_time'+sql+' GROUP BY d.id ORDER BY min(r.id) LIMIT 30',args)]
        for r in rows:r['processable']=r['id'] in self.allowed
        return dict(results=rows,total=total,visible_documents=len(self.visible),scope='隔离材料不显示；仅固定开发材料可处理；正文库未重新建设')



class Product:

        # Browsing/replay must not migrate or publish alert decisions.

    @staticmethod
    def extraction_result(view,doc,jid):
        rep,pos=requests.compact.source_representation(doc)
        prepared=dict(local_id=jid,document=doc,representation=rep,source_positions=pos,candidates=[],coverage='full_input')
        events=[dict(original_index=i['original_index'],facts=i['projection'],field_warnings=i.get('field_warnings',[]),confirmed=False) for i in view['items'] if i.get('projection')]
        return dict(view=view,prepared=prepared,preview=dict(events=events,matching=view['b'],source_document=doc,quarantined=[i for i in view['items'] if not i.get('projection')]),official_matching=view['b'])

    @staticmethod
    def mentions_result(e,jid):
        return dict(mentions=[dict(id=base.digest([jid,x['original_index']]),document_id=jid,original_index=x['original_index'],body=x['facts'],field_warnings=x.get('field_warnings',[])) for x in e['preview']['events']],prepared=e['prepared'],preview=e['preview'],**({'processing_scope':e['processing_scope']} if 'processing_scope' in e else {}))


