"""Runtime content fingerprints; offline guard generation stays in frozen tools."""
import hashlib
import unicodedata
from event_workbench import training_corpus as corpus


def fingerprints(title, body):
    normalized = ''.join(unicodedata.normalize('NFKC', body or '').split())
    return {'content': corpus.digest([title, body]),
            'body': hashlib.sha256((body or '').encode()).hexdigest(),
            'normalized': hashlib.sha256(normalized.encode()).hexdigest()}
