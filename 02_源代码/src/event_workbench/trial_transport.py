"""Single-shot transport with an absolute wall-clock limit and no retries."""
import multiprocessing
from time import monotonic
import urllib.error
from unittest.mock import patch
from event_workbench import compact_live_recovery as client


def _observed_send(payload,key,timeout,captured):
    build=client.urllib.request.build_opener
    class Response:
        def __init__(self,response):self.response=response
        def __enter__(self):self.response.__enter__();return self
        def __exit__(self,*args):return self.response.__exit__(*args)
        def read(self,*args,**kwargs):
            data=self.response.read(*args,**kwargs);captured.append(data);return data
        def __getattr__(self,name):return getattr(self.response,name)
    class Opener:
        def __init__(self,opener):self.opener=opener
        def open(self,*args,**kwargs):
            try:return Response(self.opener.open(*args,**kwargs))
            except urllib.error.HTTPError as error:
                read=error.read
                def observed(*a,**kw):
                    data=read(*a,**kw);captured.append(data);return data
                error.read=observed;raise
    def observed_builder(*args,**kwargs):return Opener(build(*args,**kwargs))
    with patch.object(client.urllib.request,'build_opener',observed_builder):
        return client.transport(payload,key,timeout)


def _worker(payload, key, timeout, endpoint, pipe):
    captured = []
    client.common.ENDPOINT = endpoint
    try:
        provider = _observed_send(payload, key, timeout, captured)
        pipe.send(('received', provider, captured))
    except client.common.TransportFailure as error:
        pipe.send(('failed', error.diagnostics, captured))
    except Exception as error:
        pipe.send(('failed', dict(stage=type(error).__name__, send_status='unknown'), captured))
    finally:
        pipe.close()


def send(payload, key, timeout, captured):
    # A socket read timeout alone permits endless slow responses. Isolate only
    # network I/O so expiry can close it without killing a SQLite transaction.
    deadline = monotonic() + timeout
    if timeout <= 0:
        raise client.common.TransportFailure('deadline_exceeded', send_status='not_started')
    context = multiprocessing.get_context('spawn')
    receive, transmit = context.Pipe(duplex=False)
    process = context.Process(target=_worker, args=(payload, key, timeout, client.common.ENDPOINT, transmit), daemon=True)
    try:
        process.start()
        transmit.close()
        remaining = deadline - monotonic()
        if remaining <= 0 or not receive.poll(remaining):
            raise client.common.TransportFailure('deadline_exceeded', stage='send', send_status='unknown')
        try:
            state, result, chunks = receive.recv()
        except EOFError:
            raise client.common.TransportFailure('transport_worker_ended', send_status='unknown') from None
        captured.extend(chunks)
        if monotonic() >= deadline:
            raise client.common.TransportFailure('deadline_exceeded', stage='response_read', send_status='response_received')
        if state != 'received':
            raise client.common.TransportFailure('transport_failed', **result)
        return result
    finally:
        receive.close()
        transmit.close()
        if process.pid is not None:
            if process.is_alive():process.terminate()
            process.join(timeout=1)
            if process.is_alive():process.kill();process.join(timeout=1)
            process.close()
