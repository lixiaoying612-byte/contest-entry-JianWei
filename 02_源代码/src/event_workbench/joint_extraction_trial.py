"""ENG014 fixed J versus A/B trial; bounded attempts and immutable evidence."""
import hashlib
import json
from pathlib import Path
from event_workbench import compact_model_io as compact
from event_workbench import extraction_compatibility as compat
from event_workbench import compact_live_recovery as client

ROOT=compact.ROOT;CONTRACT=ROOT/'engineering/contracts/eng014'
common=compact.common;v2=compact.v2
VERSION='eng014-joint-separated-v1'


def digest_bytes(value):return hashlib.sha256(value).hexdigest()
def wire_bytes(value):return json.dumps(value,ensure_ascii=False).encode('utf-8')
def save(path,value):compact.checks.immutable_json(Path(path),value)


def payload(stage,content):
    extract=(CONTRACT/'extract_instruction.txt').read_text(encoding='utf-8')
    match=(CONTRACT/'match_instruction.txt').read_text(encoding='utf-8')
    if stage=='A':prompt=extract
    elif stage=='B':prompt=match
    elif stage=='J':
        prompt='以下是联合响应中a抽取部分与b匹配部分的子协议，最后的联合封装优先。先完整保留开放事项，再判断匹配；看到候选不能限制抽取范围。\n【a抽取子协议】\n'+extract+'\n【b匹配子协议】\n'+match+'\n【联合封装】只输出JSON：{"a":{"e":[],"n":[]},"b":{"m":[],"n":[]}}。a.e中的数组下标从0开始，是b使用的本次提及编号i；不要向a.e事件新增i或x字段。b必须且只能引用同一次响应的a.e。c为全部共享候选，全文u供两部分使用。空匹配仍完整保留a.e，不因目录外事项删除它。上面的子协议输出分别放在a、b键下。'
    else:raise ValueError('unknown_stage')
    result=dict(model='deepseek-v4-pro',thinking={'type':'disabled'},stream=False,response_format={'type':'json_object'},max_tokens=8192,messages=[dict(role='system',content=prompt),dict(role='user',content=common.json_text(content,separators=(',',':')))])
    client.preflight(result);return result


def a_view(raw,doc,complete,parent_stage):
    wrapper=dict(raw_final_answer=raw,identity=v2.digest([VERSION,parent_stage,raw]),response_complete=complete)
    view=compat.derive(dict(document_id=doc['local_id'],document=doc['document']),wire_bytes(wrapper))
    view['eng014_lineage']=dict(parent_stage=parent_stage,subobject_serialization=parent_stage=='J',parent_input_document_sha256=v2.digest(doc['document']))
    decoded=view.get('decoded') or {};checks=decoded.get('checks') or {}
    view['eng014_eligible']=bool(complete and view.get('replay_equal') and not view.get('adapter_issues') and not decoded.get('issues') and decoded.get('projection') is not None and checks.get('schema_status')=='passed' and checks.get('evidence_status')=='passed')
    return view


def b_content(doc,view):
    projection=view['decoded']['projection'];before=v2.digest(projection)
    events,_=compact.compact_existing_a(dict(parsed_response=projection,identity=view['identity'],evidence_issues=view['decoded']['checks']['evidence_issues']),doc['document'])
    content=dict(doc['representation'],a=events,n=[[x['code'],x['message']] for x in projection['document_notes']],c=doc['candidates'],per=[[i,[d['definition_id'] for d in doc['candidates']]] for i in range(len(events))])
    if v2.digest(projection)!=before:raise ValueError('B_changed_A_facts')
    return content


def decode(stage,raw,doc,complete,content=None):
    if stage=='A':return dict(a=a_view(raw,doc,complete,'A'),b=None)
    if stage=='B':return dict(a=None,b=compact.decode_b(raw,content))
    try:joint=common.strict_json(raw)
    except (ValueError,TypeError):return dict(a=None,b=None,joint_issues=['invalid_joint_json'])
    strict=compact.structural_errors(joint,v2.read_json(CONTRACT/'joint_wire.schema.json'))
    if not isinstance(joint,dict) or set(joint)!={'a','b'} or not isinstance(joint['a'],dict):return dict(a=None,b=None,joint_issues=strict or ['joint_envelope_invalid'])
    view=a_view(common.json_text(joint['a']),doc,complete,'J');decoded=view.get('decoded') or {};match=None
    if decoded.get('projection') is not None:
        match=compact.decode_b(common.json_text(joint['b']),b_content(doc,view))
    return dict(a=view,b=match,joint_issues=strict,parent_content_sha256=digest_bytes(raw.encode('utf-8',errors='surrogatepass')))
