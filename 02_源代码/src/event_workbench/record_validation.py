"""Validate the frozen eng001-v1 contract without I/O or input mutation.

An empty issue list means only that the nine-field structure is acceptable.
It does not confirm any event, entity relationship, source, or claim.
"""

import re
from datetime import date

CONTRACT_VERSION = "eng001-v1"
FIELDS = (
    "document_id", "source_id", "subject_id", "event_type", "affected_ids",
    "event_time", "publication_time", "claim_status", "unknown_reasons",
)
UNKNOWN_FIELDS = (
    "subject_id", "event_type", "affected_ids", "event_time", "publication_time",
)
_STRING_FIELDS = ("document_id", "source_id", "subject_id", "event_type")
_DATE_FIELDS = ("event_time", "publication_time")
_CLAIM_STATUSES = ("unverified", "insufficient_evidence")
_DAY_PATTERN = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")


def _issue(field: str, code: str, message: str) -> dict[str, str]:
    return {"field": field, "code": code, "message": message}


def _check_string(value: object, field: str, issues: list[dict]) -> None:
    """Check, but never strip or rewrite, a known string or unknown reason."""
    if not isinstance(value, str):
        issues.append(_issue(field, "invalid_type", "必须是非空白字符串。"))
    elif not value.strip():
        issues.append(_issue(field, "empty_string", "字符串不能是空串或纯空白。"))


def validate_event(record: object) -> list[dict]:
    """Return field/code/message issues in stable order; never repair input."""
    if not isinstance(record, dict):
        return [_issue("$", "invalid_record_type", "事件记录必须是字典。")]

    issues: list[dict] = []
    for field in FIELDS:
        if field not in record:
            issues.append(_issue(field, "missing_field", "缺少契约要求的顶层字段。"))
    for field in record:
        if field not in FIELDS:
            label = field if isinstance(field, str) and field.strip() else repr(field)
            issues.append(_issue(label, "unexpected_field", "本契约不允许此顶层字段。"))

    for field in _STRING_FIELDS:
        if field not in record or (field in UNKNOWN_FIELDS and record[field] is None):
            continue
        _check_string(record[field], field, issues)

    if "affected_ids" in record and record["affected_ids"] is not None:
        affected = record["affected_ids"]
        if not isinstance(affected, list):
            issues.append(_issue("affected_ids", "invalid_type", "必须是字符串列表或有原因的 null。"))
        elif not affected:
            issues.append(_issue("affected_ids", "empty_list", "列表至少包含一个影响对象；空列表不表示无影响。"))
        else:
            for index, value in enumerate(affected):
                _check_string(value, f"affected_ids[{index}]", issues)

    for field in _DATE_FIELDS:
        if field not in record or record[field] is None:
            continue
        value = record[field]
        if not isinstance(value, str):
            issues.append(_issue(field, "invalid_type", "日期必须是字符串或有原因的 null。"))
        elif _DAY_PATTERN.fullmatch(value) is None:
            issues.append(_issue(field, "invalid_date_format", "日期须为 ASCII 数字的 YYYY-MM-DD，不接受时间戳或省略位数。"))
        else:
            try:
                date.fromisoformat(value)
            except ValueError:
                issues.append(_issue(field, "invalid_calendar_date", "该年月日不是有效日历日期。"))

    if "claim_status" in record:
        status = record["claim_status"]
        if not isinstance(status, str):
            issues.append(_issue("claim_status", "invalid_type", "主张状态必须是字符串。"))
        elif status not in _CLAIM_STATUSES:
            issues.append(_issue("claim_status", "invalid_value", "仅允许 unverified 或 insufficient_evidence。"))

    if "unknown_reasons" in record:
        reasons = record["unknown_reasons"]
        if not isinstance(reasons, dict):
            issues.append(_issue("unknown_reasons", "invalid_type", "未知原因必须是字典。"))
        else:
            for field in UNKNOWN_FIELDS:
                if field in record and record[field] is None and field not in reasons:
                    issues.append(_issue(f"unknown_reasons.{field}", "missing_unknown_reason", "null 字段必须有对应的非空白未知原因。"))
            for field, reason in reasons.items():
                path = f"unknown_reasons.{field}"
                if field not in UNKNOWN_FIELDS or field not in record or record[field] is not None:
                    issues.append(_issue(path, "unexpected_unknown_reason", "原因只能对应实际存在且为 null 的五个可未知字段。"))
                _check_string(reason, path, issues)

    return issues
