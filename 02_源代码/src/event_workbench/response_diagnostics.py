"""Read-only layered diagnostics, never repairs or releases a failed response."""
import copy,json
from event_workbench import development_request as old

def diagnose(raw,document,content):
    result=dict(version='eng042-v1',raw_answer=raw,json_status='failed',a_items=[],strict_document=None,b=dict(raw=None,processing_status='not_checked',downstream_usable=False),semantic_review='implementation_AI_and_command_AI_required_not_expert_gold',business_calls=0)
    try:joint=old.compact.common.strict_json(raw)
    except (ValueError,TypeError):
        result['json_issue']='invalid_json';result['b']['item_checks']='cannot_determine';return result
    result['json_status']='passed'
    if not isinstance(joint,dict):
        result['envelope_issue']='not_object';result['b']['item_checks']='cannot_determine';return result
    result['raw_a']=copy.deepcopy(joint.get('a'));result['b']['raw']=copy.deepcopy(joint.get('b'))
    try:strict=old.decode_named_joint(raw,document,content)
    except Exception:strict=dict(usable=False,issues=['strict_parser_exception'],a=None,b=None)
    result['strict_document']=strict
    a=joint.get('a');items=a.get('events') if isinstance(a,dict) else None
    items_known=isinstance(items,list)
    units=dict(content['u'])
    if items_known:
        for i,event in enumerate(items):
            record=dict(index=i,raw=copy.deepcopy(event),format_status='failed',issues=[],fields=[],semantic_status='not_verified')
            try:
                compact=old.named_event_wire.named_to_compact(dict(events=[event]))
                decoded=old.compact.decode_a(old.compact.common.json_text(compact),document)
                record['format_status']='passed';record['issues']=copy.deepcopy(decoded['issues']+decoded.get('checks',{}).get('schema_issues',[])+decoded.get('checks',{}).get('evidence_issues',[]))
                for issue in record['issues']:
                    if isinstance(issue,dict) and isinstance(issue.get('field'),str):issue['field']=issue['field'].replace('$.events[0]',f'$.events[{i}]',1)
            except old.named_event_wire.WireValidationError as exc:record['issues']=copy.deepcopy(exc.issues)
            except (ValueError,TypeError,KeyError,IndexError):record['issues']=[dict(code='item_parser_failed')]
            if isinstance(event,dict):
                fields=[('summary',event.get('summary'),event.get('evidence_refs'))]
                status=event.get('status')
                if isinstance(status,dict):fields.append(('status',status,status.get('evidence_refs')))
                for group,key in [('participants','name'),('details','value_text'),('times','text')]:
                    rows=event.get(group,[])
                    if isinstance(rows,list):
                        for n,row in enumerate(rows):
                            if isinstance(row,dict):fields.append((f'{group}[{n}].{key}',row.get(key),row.get('evidence_refs')))
                for field,value,refs in fields:
                    selected=[dict(ref=r,text=units.get(r),exists=r in units) for r in refs if isinstance(r,str)] if isinstance(refs,list) else []
                    path=f'$.events[{i}].{field}'
                    issues=[x for x in record['issues'] if isinstance(x,dict) and x.get('field')==path]
                    outside=any(x.get('code')=='outside_selected_evidence' for x in issues)
                    explanation=('missing_selected_name_evidence; selected refs do not locate name; no full-document repair' if field.startswith('participants') else 'literal_value_not_located; paraphrase or cross-unit summary requires AI semantic review; not proof of fabrication') if outside else 'no semantic verdict; identical numbers alone do not establish object or period'
                    record['fields'].append(dict(path=path,value=copy.deepcopy(value),evidence_refs=copy.deepcopy(refs),selected_units=selected,strict_issues=issues,explanation=explanation,semantic_support_verified=False))
            result['a_items'].append(record)
    # B diagnosis uses only known array indices, not fabricated A facts or business judgments.
    b=result['b'];raw_b=joint.get('b')
    schema=old.compact.v2.read_json(old.compact.CONTRACT/'b_wire.schema.json')
    b['format_issues']=old.compact.structural_errors(raw_b,schema)
    a_ready=bool(strict.get('a',{}).get('ready_for_review')) if isinstance(strict.get('a'),dict) else False
    b['processing_status']='blocked_by_A' if not a_ready else 'diagnostic_only'
    b['item_checks']='available' if items_known else 'cannot_determine'
    b['coverage']=None;b['index_issues']=[];b['shared_candidate_issues']=[]
    if items_known:
        counts={i:0 for i in range(len(items))}
        if isinstance(raw_b,dict):
            for kind in ('m','n'):
                rows=raw_b.get(kind)
                if not isinstance(rows,list):continue
                for n,row in enumerate(rows):
                    if not isinstance(row,list) or not row:continue
                    index=row[0]
                    if type(index)!=int or index not in counts:b['index_issues'].append(dict(code='unknown_mention_index',field=f'{kind}[{n}]',value=copy.deepcopy(index)))
                    else:counts[index]+=1
                    if kind=='n' and len(row)>1 and row[1]=='no_candidate' and content.get('c'):b['shared_candidate_issues'].append(dict(code='candidate_reason_conflict',field=f'n[{n}]',index=index))
        b['coverage']=[dict(index=i,records=n,status='missing' if n==0 else 'record_present_not_business_verified') for i,n in counts.items()]
        if not b['format_issues']:
            b['diagnostic_decode']=old.monitoring_consistency.check(old.compact.common.json_text(raw_b),dict(u=content['u'],c=content['c'],a=[dict(i=i,x=['diagnostic_only']) for i in counts]),candidate_scope='shared_c')
            b['diagnostic_decode']['checks_passed_without_A_release']=b['diagnostic_decode'].pop('b_usable')
            b['diagnostic_decode']['downstream_usable']=False
            b['diagnostic_decode']['processing_status']=b['processing_status']
    b['downstream_usable']=False # Diagnostic interface never releases results, even if its B checks pass.
    return result

def markdown(result):
    lines=['# 原答分层离线诊断','',f'JSON：{result["json_status"]}；严格整文可用：{bool((result.get("strict_document") or {}).get("usable"))}。诊断不修答、不放行业务。','']
    for item in result['a_items']:
        lines += [f'## 原始事项 {item["index"]}','', '原始内容：'+json.dumps(item['raw'],ensure_ascii=False),'', '格式：'+item['format_status'],'检查问题：'+json.dumps(item['issues'],ensure_ascii=False),'']
        for f in item['fields']:
            lines += [f'- {f["path"]}：'+json.dumps(f['value'],ensure_ascii=False), '  - 原选引用：'+json.dumps(f['selected_units'],ensure_ascii=False), '  - 检查：'+json.dumps(f['strict_issues'],ensure_ascii=False)+'；'+f['explanation']]
        lines.append('')
    b=result['b'];lines += ['## B原始内容与处理状态','',json.dumps(b.get('raw'),ensure_ascii=False),'', '状态：'+b['processing_status']+'；下游不可用。','事项覆盖：'+json.dumps(b.get('coverage'),ensure_ascii=False),'下标问题：'+json.dumps(b.get('index_issues'),ensure_ascii=False),'共享候选问题：'+json.dumps(b.get('shared_candidate_issues'),ensure_ascii=False)]
    return '\n'.join(lines)

