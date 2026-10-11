"""Source proof validation retained for saved review compatibility."""
from event_workbench import event_dossiers as versions


def check_proof(documents,p):
    d=documents.get(p['document_id'])
    if not d or p['document_sha256']!=versions.fingerprint(d['document']):raise ValueError('proof_document_version')
    if p['section'] not in ('title','body'):raise ValueError('proof_section')
    text=d['document']['title' if p['section']=='title' else 'text'] or ''
    if type(p['start']) is not int or type(p['end']) is not int or not 0<=p['start']<p['end']<=len(text) or text[p['start']:p['end']]!=p['quote']:raise ValueError('proof_position')
    if versions.sha(p['quote'].encode('utf-8'))!=p['quote_sha256']:raise ValueError('proof_quote_digest')
    return d
