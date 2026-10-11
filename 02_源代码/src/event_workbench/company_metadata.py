"""Versioned company attributes, kept separate from event judgments and impact objects."""
import json
from event_workbench import training_corpus as corpus
from event_workbench.news_materials import moment

PATH=corpus.ROOT/'data/company-industry-metadata.json'


def resolve(matches, as_of):
    if not PATH.exists():return []
    data=json.loads(PATH.read_text(encoding='utf-8'))
    if moment(data['available_at'])>moment(as_of):return []
    found={}
    for item in data['companies']:
        code=item['company_code']
        if code in found:raise ValueError('company_metadata_duplicate_code')
        if len(code)!=6 or not code.isdigit() or not item.get('industry_code','').isdigit() or not item.get('industry_name') or not item.get('company_name'):raise ValueError('company_metadata_fields_required')
        if not item.get('sources') or any(s.get('split')!='train' or s.get('guard')!='allowed' or not s.get('raw_sha256') for s in item['sources']):raise ValueError('company_metadata_source_required')
        found[code]=item
    result=[]
    for match in matches:
        d=match.get('definition',{});item=found.get(str(d.get('target_code')))
        if match.get('decision') not in ('matched','m','supplementary') or d.get('target_type')=='industry' or not item or item['company_name']!=d.get('target_name'):continue
        if any(d.get(k) and str(d[k])!=item[k] for k in ('industry_code','industry_name')):raise ValueError('company_metadata_conflicts_with_saved_definition')
        value=dict(item,metadata_version=data['version'],metadata_sha=corpus.digest(data),available_at=data['available_at'],taxonomy=data['taxonomy'],basis='approved_training_company_metadata',not_model_prediction=True)
        if value not in result:result.append(value)
    return result
