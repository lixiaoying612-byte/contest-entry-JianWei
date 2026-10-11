"""The worker's deadline also bounds SQLite work; readers have no task deadline."""
from contextvars import ContextVar
from time import monotonic

current = ContextVar('news_deadline', default=None)


def bind(connection, default_ms):
    deadline = current.get()
    if deadline is None:
        connection.execute('PRAGMA busy_timeout='+str(default_ms))
        return connection
    remaining = deadline-monotonic()
    if remaining <= 0:
        connection.close()
        raise TimeoutError('analysis_deadline_exceeded')
    connection.execute('PRAGMA busy_timeout='+str(min(default_ms, max(1,int(remaining*1000)))))
    connection.set_progress_handler(lambda: int(monotonic() >= deadline), 1000)
    return connection
