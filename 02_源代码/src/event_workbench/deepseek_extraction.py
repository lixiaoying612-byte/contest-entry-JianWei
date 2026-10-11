"""eng007-v1: offline preparation, strict evidence checks, and bounded transport."""
import hashlib
import json
import os
import urllib.request
from pathlib import Path


CONTRACT_VERSION = 'eng007-v1'
PROMPT_VERSION = 'eng007-p1.1'
ENDPOINT = 'https://api.deepseek.com/chat/completions'
MODEL = 'deepseek-v4-pro'
CONFIG = dict(model=MODEL, thinking={'type': 'disabled'}, stream=False, response_format={'type': 'json_object'}, max_tokens=6000)
STAGES = {'planned', 'ongoing', 'reported_occurred', 'conditional', 'negated', 'uncertain'}
ROLES = {'discloser', 'contractor', 'project_name_association', 'unresolved'}


def issue(field, code, message):
    return dict(field=field, code=code, message=message)


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError('duplicate JSON key')
        result[key] = value
    return result


def reject_constant(value):
    raise ValueError('non-finite JSON constant')


def strict_json(text):
    return json.loads(text, object_pairs_hook=unique_object, parse_constant=reject_constant)


def sha256(text):
    return hashlib.sha256(text.encode('utf-8')).hexdigest()


def locate_quote(text, quote):
    positions = []
    if isinstance(quote, str) and quote:
        start = text.find(quote)
        while start >= 0:
            positions.append(dict(start=start, end=start + len(quote)))
            start = text.find(quote, start + 1)
    return dict(quote=quote, status='unique' if len(positions) == 1 else 'ambiguous' if positions else 'missing', candidates=positions)


class TransportFailure(Exception):
    def __init__(self, code, **diagnostics):
        self.code = code
        self.diagnostics = diagnostics
        super().__init__(code)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None




def redact(value, secret):
    if isinstance(value, str):
        return value.replace(secret, '[REDACTED]') if secret else value
    if isinstance(value, list):
        return [redact(x, secret) for x in value]
    if isinstance(value, dict):
        return {redact(k, secret): redact(v, secret) for k, v in value.items()}
    return value


def json_text(value, **options):
    """Readable UTF8 normally; lossless JSON escapes for isolated surrogate code points."""
    text = json.dumps(value, ensure_ascii=False, **options)
    try:
        text.encode('utf-8')
    except UnicodeEncodeError:
        text = json.dumps(value, ensure_ascii=True, **options)
    return text


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.tmp')
    temporary.write_text(json_text(value, indent=2) + '\n', encoding='utf-8')
    os.replace(temporary, path)
