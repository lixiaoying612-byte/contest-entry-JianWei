"""Validate source-bound impact proofs shared by current and historical views."""
from event_workbench import event_dossiers as common

def verify_proof(proof,documents):
    doc=documents.get(proof['document_id'])
    if not doc or common.fingerprint(doc['document'])!=proof['document_sha256']:raise ValueError('proof_document_version')
    field='text' if proof['section']=='body' else 'title' if proof['section']=='title' else None
    if not field:raise ValueError('proof_section')
    text=doc['document'][field] or '';a,b=proof['start'],proof['end']
    if type(a) is not int or type(b) is not int or not 0<=a<b<=len(text) or text[a:b]!=proof['quote'] or common.sha(proof['quote'].encode('utf-8'))!=proof['quote_sha256']:raise ValueError('proof_position')

