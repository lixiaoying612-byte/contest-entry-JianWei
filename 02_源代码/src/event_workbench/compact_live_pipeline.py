"""ENG010 bounded compact A/B execution. Records raw usage; never computes money."""

from event_workbench import compact_model_io as compact

v2,common=compact.v2,compact.common
VERSION='eng010-three-v1'


def eligible_a(result):
    return bool(result and result['response_complete'] and (result.get('decoded') or {}).get('projection') is not None
                and not result['decoded']['issues'] and result['decoded']['projection']['events'])
