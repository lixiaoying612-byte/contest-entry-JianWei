"""Bounded local file preview and row-by-row intake; no model or URL fetching."""
import base64
import csv
import hashlib
import io
import zipfile
from contextlib import closing
from pathlib import Path
from xml.parsers.expat import ExpatError
from xml.etree.ElementTree import ParseError
from event_workbench import news_intake, corpus_xlsx

MAX_BYTES=1024*1024
MAX_ROWS=30
HEADERS={name:key for key,names in {
    'title':('标题','新闻标题','title','article_title'),
    'text':('正文','新闻正文','text','content'),
    'source':('来源','来源名称','source','article_source'),
    'publication_time':('发布时间','披露时间','日期','publication_time','article_publish_time'),
    'source_url':('链接','原文链接','source_url'),
}.items() for name in names}


def preview(payload):
    if not isinstance(payload,dict) or set(payload)-{'run','filename','data'}:raise ValueError('文件字段不合法')
    name=payload.get('filename');encoded=payload.get('data')
    if not isinstance(name,str) or len(name)>255 or not isinstance(encoded,str) or len(encoded)>MAX_BYTES*4//3+4:raise ValueError('文件最大1MB')
    try:raw=base64.b64decode(encoded,validate=True)
    except ValueError:raise ValueError('文件内容无效') from None
    if not raw or len(raw)>MAX_BYTES:raise ValueError('请选择非空且不超过1MB的文件')
    rows=[];header=None
    def take(number,values,errors=()):
        nonlocal header
        if not any(str(v or '').strip() for v in values.values()):return
        if header is None:
            if errors:raise ValueError('列名不能包含公式')
            header={col:HEADERS[str(value).strip().lower()] for col,value in values.items() if str(value).strip().lower() in HEADERS}
            if 'text' not in header.values() or len(set(header.values()))!=len(header):raise ValueError('需包含唯一的正文列；请使用导入模板')
            return
        if len(rows)>=MAX_ROWS:raise ValueError('每批最多30篇，请拆分文件后导入；尚未保存任何新闻')
        fields={key:(str(values.get(col) or '') if key=='text' else str(values.get(col) or '').strip()) for col,key in header.items()}
        try:
            if errors:raise ValueError('不接受公式单元格，请粘贴为值')
            news_intake.validate(fields)
            rows.append(dict(row=number,fields=fields,status='valid',reason='可导入'))
        except ValueError as error:rows.append(dict(row=number,fields=fields,status='invalid',reason=str(error)))
    suffix=Path(name).suffix.lower()
    if suffix=='.csv':
        try:content=raw.decode('utf-8-sig')
        except UnicodeDecodeError:
            try:content=raw.decode('gb18030')
            except UnicodeDecodeError:raise ValueError('CSV编码无法读取，请另存为UTF-8 CSV') from None
        try:
            for number,values in enumerate(csv.reader(io.StringIO(content),strict=True),1):take(number,dict(enumerate(values)))
        except csv.Error:raise ValueError('CSV引号或行格式不正确') from None
    elif suffix=='.xlsx':
        try:
            with zipfile.ZipFile(io.BytesIO(raw)) as archive:
                entries=archive.infolist()
                if len(entries)>500 or sum(x.file_size for x in entries)>16*MAX_BYTES:raise ValueError('Excel解压内容过大，请使用小批量模板')
            with closing(corpus_xlsx.Workbook(io.BytesIO(raw),':memory:',hashlib.sha256(raw).hexdigest())) as book:
                if len(book.sheets)!=1:raise ValueError('每次只导入一个工作表，请按模板整理')
                def take_cells(number,cells):
                    values={col:cell['value'] for col,cell in cells.items()}
                    if header is not None:
                        for col,key in header.items():
                            if key=='publication_time' and cells.get(col,{}).get('type')=='n':
                                value,issue=corpus_xlsx.old._publication(cells[col],book.system)
                                values[col]=value if not issue else cells[col]['value']
                    take(number,values,[col for col,cell in cells.items() if cell.get('formula')])
                book.rows(book.sheets[0][1],take_cells)
        except (zipfile.BadZipFile,KeyError,UnicodeError,ExpatError,ParseError,StopIteration):raise ValueError('Excel文件无法读取，请使用有效的XLSX文件') from None
    else:raise ValueError('支持CSV和XLSX文件，请先转换旧版XLS文件')
    if not rows:raise ValueError('文件中没有新闻正文行')
    return dict(filename=name,rows=rows,valid=sum(r['status']=='valid' for r in rows),invalid=sum(r['status']=='invalid' for r in rows))


def save_batch(app,payload):
    if not isinstance(payload,dict) or set(payload)-{'run','revision','rows'}:raise ValueError('批量字段不合法')
    rows=payload.get('rows')
    if not isinstance(rows,list) or not 1<=len(rows)<=MAX_ROWS:raise ValueError('每批须为1至30篇')
    revision=payload.get('revision');run=payload.get('run')
    with closing(app.connect()) as c:
        if app.run(c,run)['revision']!=revision:raise ValueError('工作区已变化，请刷新后重试')
    results=[]
    for row in rows:
        try:
            if not isinstance(row,dict) or set(row)!={'row','fields'} or type(row['row']) is not int or not isinstance(row['fields'],dict) or set(row['fields'])-set(HEADERS.values()):raise ValueError('行字段不合法')
            saved=app.save_news(dict(row['fields'],run=run,revision=revision));revision=saved['revision']
            results.append(dict(row=row['row'],status='duplicate' if saved['duplicate'] else 'saved',material_id=saved['material_id'],reason='已保存，待分析' if not saved['duplicate'] else '重复材料，复用已有记录'))
        except (ValueError,TypeError) as error:
            results.append(dict(row=row.get('row') if isinstance(row,dict) else None,status='failed',reason=str(error)))
            with closing(app.connect()) as c:revision=app.run(c,run)['revision']
    return dict(rows=results,revision=revision,saved=sum(r['status']=='saved' for r in results),duplicates=sum(r['status']=='duplicate' for r in results),failed=sum(r['status']=='failed' for r in results))
