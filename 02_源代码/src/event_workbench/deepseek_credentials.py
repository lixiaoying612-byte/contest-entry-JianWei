"""Portable credentials; never put credentials in logs or request artifacts."""
import json
import os
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CREDENTIAL_PATH = ROOT / 'config/deepseek.json'
ENDPOINT = 'https://api.deepseek.com/chat/completions'


class CredentialError(Exception):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def _valid(secret):
    return isinstance(secret, str) and 1 <= len(secret) <= 512 and all(33 <= ord(c) <= 126 for c in secret)


def read_credential():
    try:
        secret = os.environ.get('DEEPSEEK_API_KEY', '').strip()
        if not secret:
            if not CREDENTIAL_PATH.is_file(): raise CredentialError('credential_missing')
            secret = json.loads(CREDENTIAL_PATH.read_text('utf-8-sig')).get('api_key')
        if not _valid(secret): raise CredentialError('credential_format_invalid')
        return secret
    except CredentialError:
        raise
    except (OSError, ValueError, TypeError, AttributeError):
        raise CredentialError('credential_read_failed') from None


def credential_exists():
    try:
        read_credential()
        return True
    except CredentialError:
        return False


def save_credential(secret, *, overwrite=False):
    if not _valid(secret): raise CredentialError('credential_format_invalid')
    if credential_exists() and not overwrite: raise CredentialError('credential_already_exists')
    temporary = None
    try:
        config = json.loads(CREDENTIAL_PATH.read_text('utf-8-sig')) if CREDENTIAL_PATH.exists() else {}
        config['api_key'] = secret
        CREDENTIAL_PATH.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile('w', encoding='utf8', dir=CREDENTIAL_PATH.parent, delete=False) as stream:
            temporary = Path(stream.name)
            json.dump(config, stream, ensure_ascii=False, indent=2)
            stream.flush(); os.fsync(stream.fileno())
        os.replace(temporary, CREDENTIAL_PATH)
        temporary = None
    except (OSError, ValueError, TypeError):
        raise CredentialError('credential_write_failed') from None
    finally:
        if temporary is not None: temporary.unlink(missing_ok=True)
