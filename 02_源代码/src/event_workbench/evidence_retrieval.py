"""Shared source/query helpers; obsolete experiment execution is archived."""
from event_workbench import retrieval_representation as rep
old=rep.old


def query(body):
    fields=[('summary',body.get('summary'))]
    fields += [(f'participants[{i}].name',p.get('name')) for i,p in enumerate(body.get('participants',[]))]
    fields += [('status.phase',(body.get('status') or {}).get('phase'))]
    fields += [(f'times[{i}].text',t.get('text')) for i,t in enumerate(body.get('times',[]))]
    used=[dict(field=k,value=v) for k,v in fields if isinstance(v,str) and v.strip()]
    return dict(text='\n'.join(v['value'] for v in used),fields=used,missing=[k for k,v in fields if not isinstance(v,str) or not v.strip()],contains_relation_decisions=False)
