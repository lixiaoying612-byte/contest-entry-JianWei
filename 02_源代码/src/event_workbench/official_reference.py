"""Presentation-only reference lookup. Never imported by processing adapters."""
import hashlib
import json
from pathlib import Path

REFERENCE_PATH=Path(__file__).resolve().parents[2]/'engineering/demos/zhiyan-demo/reference/official.json'


def content_identity(title,text):
    return hashlib.sha256(json.dumps([title,text],ensure_ascii=False,separators=(',',':')).encode()).hexdigest()


def lookup(document):
    if not REFERENCE_PATH.exists():return dict(available=False,items=[])
    saved=json.loads(REFERENCE_PATH.read_text('utf-8'))
    identity=content_identity(document.get('title'),document.get('text'))
    items=[x for x in saved['items'] if x['content_identity']==identity]
    return dict(available=bool(items),items=items,
                notice='官方参考答案，仅用于对照写法；不是本系统输出或核验证据。')
