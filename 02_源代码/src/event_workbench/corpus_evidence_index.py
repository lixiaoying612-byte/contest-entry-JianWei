"""Shared source-time parsing; legacy vector index execution is archived."""
import datetime as dt


def instant(value,*,end_of_day=False):
    if not isinstance(value,str) or not value:raise ValueError('explicit_evaluation_time_required')
    t=dt.datetime.fromisoformat(value.replace('Z','+00:00'))
    if len(value)==10 and end_of_day:t=t.replace(hour=23,minute=59,second=59,microsecond=999999)
    if t.tzinfo is None:t=t.replace(tzinfo=dt.timezone(dt.timedelta(hours=8)))
    return t.astimezone(dt.timezone.utc)

