"""Shared immutable source helpers retained from the original retrieval experiment."""
import hashlib
import json
from event_workbench import compact_model_io as baseline


def packed(value):return json.dumps(value,ensure_ascii=False,sort_keys=True,separators=(',',':'),allow_nan=False)

def sha(data):return hashlib.sha256(data).hexdigest()

def fingerprint(value):return sha(packed(value).encode('utf-8'))

def selected_json_field(raw,field):
    """Materialize only the allowed top-level value; skip other values lexically."""
    text=raw.decode('utf-8-sig');decoder=json.JSONDecoder();i=0
    def space(pos):
        while pos<len(text) and text[pos].isspace():pos+=1
        return pos
    def skip(pos):
        depth=0;string=False;escape=False
        while pos<len(text):
            ch=text[pos]
            if string:
                if escape:escape=False
                elif ch=='\\':escape=True
                elif ch=='"':string=False
            elif ch=='"':string=True
            elif ch in '[{':depth+=1
            elif ch in ']}':
                if depth==0:return pos
                depth-=1
            elif ch==',' and depth==0:return pos
            pos+=1
        raise ValueError('unterminated_unselected_value')
    i=space(i)
    if text[i]!='{':raise ValueError('expected_object')
    i+=1
    while True:
        i=space(i)
        if text[i]=='}':break
        key,i=decoder.raw_decode(text,i);i=space(i)
        if text[i]!=':':raise ValueError('expected_colon')
        i=space(i+1)
        if key==field:return decoder.raw_decode(text,i)[0]
        i=space(skip(i))
        if text[i]==',':i+=1
    raise ValueError('allowed_field_missing')
