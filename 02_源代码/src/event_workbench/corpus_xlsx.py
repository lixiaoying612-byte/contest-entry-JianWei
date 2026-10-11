"""Disk-backed XLSX strings and callback rows, reusing frozen training_input XML helpers."""
import json
import posixpath
import sqlite3
import zipfile
from pathlib import Path
from event_workbench import training_input as old


def parts(archive):
    book=old._metadata(archive,'xl/workbook.xml')
    rels=old._metadata(archive,'xl/_rels/workbook.xml.rels')
    targets={}
    for rel in rels:
        target=rel.get('Target','')
        if rel.get('TargetMode')=='External':continue
        member=posixpath.normpath(target.lstrip('/') if target.startswith('/') else posixpath.join('xl',target))
        if not member.startswith('xl/'):raise ValueError('invalid_relationship')
        targets[rel.get('Id')]=member
    sheets=[];system='1900'
    for node in book.iter():
        if old._local(node.tag)=='workbookPr':
            system='1904' if node.get('date1904') in ('1','true') else '1900'
        if old._local(node.tag)=='sheet':
            rid=next(v for k,v in node.attrib.items() if old._local(k)=='id')
            sheets.append((node.get('name'),targets[rid]))
    shared=next((targets[x.get('Id')] for x in rels if x.get('Type','').endswith('/sharedStrings')),None)
    if shared is None and 'xl/sharedStrings.xml' in archive.namelist():shared='xl/sharedStrings.xml'
    return sheets,shared,system


class Workbook:
    def __init__(self,path,cache_path,fingerprint,progress=None):
        self.archive=zipfile.ZipFile(path)
        self.sheets,self.shared,self.system=parts(self.archive)
        Path(cache_path).parent.mkdir(parents=True,exist_ok=True)
        self.cache=sqlite3.connect(cache_path)
        self.cache.execute('PRAGMA cache_size=-8192')
        self.cache.executescript('CREATE TABLE IF NOT EXISTS strings(i INTEGER PRIMARY KEY,value TEXT); CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);')
        meta=dict(self.cache.execute('SELECT key,value FROM meta'))
        if meta.get('sha256') not in (None,fingerprint):raise ValueError('cache_source_changed')
        if meta.get('ready')!='true':
            self.cache.execute('DELETE FROM strings')
            self.cache.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('sha256',fingerprint))
            self.cache.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('ready','false'))
            self.cache.commit()
            state=dict(index=-1,stack=[],parts=[])
            def start(name,attrs):
                tag=old._local(name);state['stack'].append(tag)
                if tag=='si':state['index']+=1;state['parts']=[]
            def text(value):
                if state['stack'][-1]=='t' and 'rPh' not in state['stack']:state['parts'].append(value)
            def end(name):
                if old._local(name)=='si':
                    self.cache.execute('INSERT INTO strings VALUES (?,?)',(state['index'],old._decode_string(''.join(state['parts']))))
                    if (state['index']+1)%2000==0:self.cache.commit()
                    if progress and (state['index']+1)%50000==0:progress(dict(stage='shared_strings',count=state['index']+1))
                state['stack'].pop()
            if self.shared:old._stream(self.archive,self.shared,start,end,text)
            self.cache.execute('INSERT OR REPLACE INTO meta VALUES (?,?)',('ready','true'));self.cache.commit()

    def close(self):
        self.cache.close();self.archive.close()

    def rows(self,member,callback,after=0,only=None):
        state=dict(row=0,cell=None,cells={},stack=[],column=0,active=False)
        def start(name,attrs):
            tag=old._local(name);state['stack'].append(tag)
            if tag=='row':
                row=int(attrs.get('r',state['row']+1))
                if row<=state['row']:raise ValueError('nonmonotonic_row')
                state.update(row=row,cells={},column=0,active=row>after and (only is None or row in only))
            elif tag=='c' and state['active']:
                ref=attrs.get('r');col=state['column']+1
                if ref:
                    match=old._CELL.fullmatch(ref)
                    if not match or int(match[2])!=state['row']:raise ValueError('cell_coordinate')
                    col=0
                    for ch in match[1]:col=col*26+ord(ch)-64
                if col in state['cells']:raise ValueError('duplicate_cell')
                state['column']=col
                state['cell']=dict(type=attrs.get('t','n'),v=[],text=[],formula=False,formula_text=[],has_v=False,has_is=False)
            elif state['cell'] is not None:
                if tag=='f':state['cell']['formula']=True
                if tag=='v':state['cell']['has_v']=True
                if tag=='is':state['cell']['has_is']=True
        def text(value):
            cell=state['cell']
            if cell is None:return
            tag=state['stack'][-1]
            if tag=='v':cell['v'].append(value)
            elif tag=='f':cell['formula_text'].append(value)
            elif tag=='t' and 'rPh' not in state['stack']:cell['text'].append(value)
        def end(name):
            tag=old._local(name);cell=state['cell']
            if tag=='c' and cell is not None:
                kind=cell['type'];value=''.join(cell['v']) if cell['has_v'] else None
                if kind=='inlineStr':value=old._decode_string(''.join(cell['text'])) if cell['has_is'] else None
                elif kind=='str' and value is not None:value=old._decode_string(value)
                elif kind=='s' and value is not None:
                    found=self.cache.execute('SELECT value FROM strings WHERE i=?',(int(value),)).fetchone()
                    if found is None:raise ValueError('shared_string_missing')
                    value=found[0]
                state['cells'][state['column']]=dict(type=kind,value=value,formula=cell['formula'],
                    formula_text=''.join(cell['formula_text']) if cell['formula'] else None)
                state['cell']=None
            if tag=='row' and state['active']:callback(state['row'],state['cells'])
            state['stack'].pop()
        old._stream(self.archive,member,start,end,text)

    def header(self,member):
        found={}
        def take(row,cells):
            found.update(cells);raise old._StopStream()
        self.rows(member,take,only={1})
        return {col:cell['value'] for col,cell in found.items()}
