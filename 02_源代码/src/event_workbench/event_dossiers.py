"""Offline, explicit evidence decisions over immutable extraction versions."""
import hashlib
import json
from datetime import datetime, timezone


def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'))
def sha(data):return hashlib.sha256(data).hexdigest()
def fingerprint(value):return sha(packed(value).encode('utf-8'))
def now():return datetime.now(timezone.utc).isoformat()


def locate(document,section,quote):
    if section not in ('title','body') or not isinstance(quote,str) or not quote:raise ValueError('invalid_quote')
    text=document.get('text' if section=='body' else 'title') or ''
    start=text.find(quote)
    if start<0:raise ValueError('quote_not_in_document')
    return dict(section=section,quote=quote,start=start,end=start+len(quote))


