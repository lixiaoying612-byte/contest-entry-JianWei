"""ENG010 explicit R1 continuation; controlled JSON preflight and bounded diagnostics."""
import json
import re
import urllib.error
import urllib.request

from event_workbench import compact_live_pipeline as base

common,v2,compact=base.common,base.v2,base.compact
VERSION='eng010-three-r1'


def preflight(payload):
    if payload.get('response_format',{}).get('type')=='json_object':
        controlled=[m.get('content','') for m in payload.get('messages',[]) if m.get('role')=='system']
        if not any(isinstance(s,str) and 'json' in s.casefold() and '{"' in s for s in controlled):
            raise ValueError('controlled_json_instruction_and_example_required')


def scrub_fields(value,key):
    result={}
    if isinstance(value,dict):
        for name in ['type','code','param','message']:
            item=value.get(name)
            if item is None:continue
            if type(item) not in (str,int,float,bool):continue
            text=common.redact(str(item),key)
            text=re.sub(r'(?i)Bearer\s+[^\s"\'<>]+','Bearer [REDACTED]',text)
            text=''.join(ch if ord(ch)>=32 else ' ' for ch in text)
            result[name]=text[:512 if name=='message' else 128]
    return result


def http_diagnostic(error,key):
    diag=dict(stage='http_error',http_status=error.code,send_status='response_received',parse_status='unreadable',server_error={})
    try:
        body=error.read(8193);diag['response_bytes_read']=len(body)
        if len(body)>8192:diag['parse_status']='over_limit';return diag
        try:value=common.strict_json(body.decode('utf-8'))
        except (UnicodeError,ValueError):diag['parse_status']='invalid_json_or_utf8';return diag
        diag['parse_status']='parsed'
        diag['server_error']=scrub_fields(value.get('error',value) if isinstance(value,dict) else None,key)
    except Exception:pass
    return diag


def transport(payload,key,timeout=180):
    preflight(payload);stage='request_construct'
    try:
        key.encode('ascii')
        if any(ord(ch)<33 or ord(ch)==127 for ch in key):raise ValueError('key_format')
        request=urllib.request.Request(common.ENDPOINT,data=json.dumps(payload,ensure_ascii=False).encode('utf-8'),
            headers={'Authorization':'Bearer '+key,'Content-Type':'application/json'},method='POST')
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),common.NoRedirect())
        stage='send'
        with opener.open(request,timeout=timeout) as response:
            stage='response_read';body=response.read(4*1024*1024+1)
            if len(body)>4*1024*1024:raise common.TransportFailure('response_too_large',stage=stage)
        stage='response_utf8_decode';text=body.decode('utf-8')
        stage='response_json_parse';return common.strict_json(text)
    except common.TransportFailure:raise
    except urllib.error.HTTPError as error:
        try:diag=http_diagnostic(error,key)
        finally:error.close()
        raise common.TransportFailure('http_'+str(error.code),**diag) from None
    except (TimeoutError,urllib.error.URLError):raise common.TransportFailure('timeout_or_network_error',stage=stage,send_status='unknown' if stage=='send' else 'response_received') from None
    except (ValueError,UnicodeError,OSError,TypeError):
        code={'request_construct':'request_construction_error','send':'request_send_error','response_read':'response_read_error','response_utf8_decode':'response_utf8_error','response_json_parse':'response_json_error'}[stage]
        raise common.TransportFailure(code,stage=stage,send_status='not_started' if stage=='request_construct' else 'unknown' if stage=='send' else 'response_received') from None
