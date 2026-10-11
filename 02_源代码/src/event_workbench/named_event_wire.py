"""ENG032: reversible, offline A representation; never repairs business values.

Only the frozen compact/named structures are checked. Source existence, support,
event granularity and semantic correctness remain outside this adapter.
"""

import json
import math
from pathlib import Path

from event_workbench import compact_model_io as compact


VERSION = "eng032-named-a-v1"
_EVENT_FIELDS = {
    "s": "summary", "p": "participants", "st": "status", "t": "times",
    "d": "details", "im": "impact_mentions", "q": "evidence_refs",
    "u": "uncertainties",
}
_ROW_FIELDS = {
    "p": ("name", "role", "evidence_refs"),
    "st": ("phase", "polarity", "mode", "attributed_to", "context", "evidence_refs"),
    "t": ("role", "text", "normalized", "precision", "basis", "evidence_refs"),
    "d": ("name", "value_text", "evidence_refs"),
    "im": ("target_name", "direction", "mode", "attributed_to", "evidence_ref"),
    "n": ("code", "message"),
}
_ENUMS = {
    "st": {1: compact.POL, 2: compact.MODE, 4: compact.CONTEXT},
    "t": {0: compact.TIME_ROLE, 3: compact.PRECISION, 4: compact.BASIS},
    "im": {1: compact.DIRECTION, 2: compact.MODE},
}
_REVERSE_ENUMS = {
    row: {index: {v: k for k, v in table.items()} for index, table in fields.items()}
    for row, fields in _ENUMS.items()
}
_ROOT = Path(__file__).resolve().parents[2]
_SCHEMAS = {
    "compact": json.loads((_ROOT / "engineering/contracts/eng009/a_wire.schema.json").read_text(encoding="utf-8")),
    "named": json.loads((_ROOT / "engineering/contracts/eng032/named_a.schema.json").read_text(encoding="utf-8")),
}


class WireValidationError(ValueError):
    """Invalid input with field/code/message issues; no partial converted value."""

    def __init__(self, issues):
        self.issues = sorted(issues, key=lambda item: (item["field"], item["code"]))
        super().__init__("; ".join(f'{i["field"]}: {i["code"]} ({i["message"]})' for i in self.issues))


class _ObjectPairs(list):
    """Keep JSON pairs until their full path is known, including duplicate keys."""


def _fail(path, code, message):
    raise WireValidationError([dict(field=path, code=code, message=message)])


def _field(path, key):
    return path + "." + key if key.isidentifier() else path + "[" + json.dumps(key, ensure_ascii=True) + "]"


def _copy_json(value, path, ancestors, from_text):
    """Detach mutable values without coercing Python objects or losing JSON keys."""
    kind = type(value)
    is_pairs = from_text and kind is _ObjectPairs
    if kind in (dict, list) or is_pairs:
        if id(value) in ancestors:
            _fail(path, "circular_reference", "循环对象不是 JSON。")
        ancestors.add(id(value))
        try:
            if kind is list:
                return [_copy_json(child, f"{path}[{i}]", ancestors, from_text) for i, child in enumerate(value)]
            result = {}
            for key, child in (value if is_pairs else value.items()):
                if type(key) is not str:
                    _fail(path, "non_string_key", "JSON 对象键必须是字符串。")
                child_path = _field(path, key)
                _copy_json(key, child_path, ancestors, from_text)
                if key in result:
                    _fail(child_path, "duplicate_key", "重复 JSON 键，不覆盖前值。")
                result[key] = _copy_json(child, child_path, ancestors, from_text)
            return result
        finally:
            ancestors.remove(id(value))
    if kind is str:
        if any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            _fail(path, "invalid_unicode_scalar", "包含孤立 Unicode 代理码点。")
    elif kind is float:
        if not math.isfinite(value):
            _fail(path, "nonfinite", "非有限数不是有效 JSON 数值。")
    elif kind not in (int, bool, type(None)):
        _fail(path, "non_json_type", "只接受 JSON 对应的内置对象类型，不自动强转。")
    return value


def _load(raw):
    try:
        from_text = type(raw) is str
        # Nonstandard constants are retained as floats only long enough to report
        # their exact path. _copy_json always rejects them, including 1e999 overflow.
        value = json.loads(raw, object_pairs_hook=_ObjectPairs, parse_constant=float) if from_text else raw
        return _copy_json(value, "$", set(), from_text)
    except json.JSONDecodeError as exc:
        _fail("$", "invalid_json", f"JSON 语法错误：行 {exc.lineno}，列 {exc.colno}；不修补。")
    except RecursionError:
        _fail("$", "nesting_too_deep", "嵌套深度超出运行环境限制。")
    except ValueError as exc:
        if isinstance(exc, WireValidationError):
            raise
        _fail("$", "invalid_json", "JSON 数值无法解析；不转换或截断。")


def _validate(value, representation):
    errors = compact.structural_errors(value, _SCHEMAS[representation])
    if errors:
        raise WireValidationError([
            dict(field=item["field"], code=item["code"], message="不符合 ENG032 对应的冻结 A 结构。")
            for item in errors
        ])


def _row(value, group, to_named):
    fields = _ROW_FIELDS[group]
    tables = (_ENUMS if to_named else _REVERSE_ENUMS).get(group, {})
    values = value if to_named else [value[field] for field in fields]
    converted = [tables[i][item] if i in tables else item for i, item in enumerate(values)]
    return dict(zip(fields, converted)) if to_named else converted


def _event(value, to_named):
    keys = _EVENT_FIELDS if to_named else {v: k for k, v in _EVENT_FIELDS.items()}
    result = {}
    for key, item in value.items():
        group = key if to_named else keys[key]
        if group == "st":
            item = _row(item, group, to_named)
        elif group in _ROW_FIELDS:
            item = [_row(row, group, to_named) for row in item]
        result[keys[key]] = item
    return result


def _convert(raw, to_named):
    source, target = ("compact", "named") if to_named else ("named", "compact")
    value = _load(raw)
    _validate(value, source)
    keys = {"e": "events", "n": "document_notes"} if to_named else {"events": "e", "document_notes": "n"}
    event_key = "e" if to_named else "events"
    result = {
        keys[key]: [_event(item, to_named) if key == event_key else _row(item, "n", to_named) for item in items]
        for key, items in value.items()
    }
    _validate(result, target)
    return result


def compact_to_named(raw: str | dict) -> dict:
    """Expand compact A names/tuples/enums; preserve omissions and all values."""
    return _convert(raw, True)


def named_to_compact(raw: str | dict) -> dict:
    """Restore compact A without inference, defaults, repair or input mutation."""
    return _convert(raw, False)
