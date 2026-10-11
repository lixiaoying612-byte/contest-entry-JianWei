"""Shared XLSX XML, string and date helpers; legacy training loader retired."""

import re
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation
from xml.etree import ElementTree
from xml.parsers import expat

CONTRACT_VERSION = "eng003-v1"
TEXT_TYPES = ("s", "inlineStr", "str")
_DAY = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_CELL = re.compile(r"([A-Z]+)([1-9][0-9]*)")
_ESCAPE = re.compile(r"_x([0-9a-fA-F]{4})_")


def _issue(field, code, message):
    return {"field": field, "code": code, "message": message}


class _WorkbookProblem(Exception):
    def __init__(self, field, code, message):
        super().__init__(message)
        self.issue = _issue(field, code, message)


class _StopStream(Exception):
    pass


def _local(name):
    return name.rsplit("}", 1)[-1]


def _decode_string(value):
    # Single substitution preserves escaped literal sequences such as _x005F_x0041_.
    value = _ESCAPE.sub(lambda match: chr(int(match.group(1), 16)), value)
    return value.encode("utf-16-le", "surrogatepass").decode("utf-16-le", "surrogatepass")


def _stream(archive, member, start, end, characters):
    parser = expat.ParserCreate(namespace_separator="}")
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = characters

    def reject_dtd(*args):
        raise _WorkbookProblem(member, "xml_error", "不接受 DTD 或外部实体。")

    parser.StartDoctypeDeclHandler = reject_dtd
    parser.ExternalEntityRefHandler = reject_dtd
    with archive.open(member) as stream:
        try:
            while chunk := stream.read(65536):
                parser.Parse(chunk, False)
            parser.Parse(b"", True)
        except _StopStream:
            pass


def _metadata(archive, member):
    raw = archive.read(member)
    if b"<!DOCTYPE" in raw:
        raise _WorkbookProblem(member, "xml_error", "不接受 DTD。")
    return ElementTree.fromstring(raw)


def _publication(cell, system):
    value = cell["value"]
    if value is None:
        return None, _issue("article_publish_time", "missing_publication_date", "缺少发布时间。")
    if cell["formula"]:
        return None, None  # Formula is already reported; its cached value is never used.
    try:
        if cell["type"] == "n":
            serial = Decimal(value)
            base = date(1904, 1, 1) if system == "1904" else date(1899, 12, 31)
            max_day = (date.max - base).days + (1 if system == "1900" else 0)
            if not serial.is_finite() or serial < 0 or serial >= max_day + 1:
                raise ValueError("序数为负、非有限或超出日期范围。")
            day = int(serial)
            if system == "1900" and day == 60:
                raise ValueError("1900 系统的序数 60 是不存在的 1900-02-29。")
            offset = day - 1 if system == "1900" and day > 60 else day
            return (base + timedelta(days=offset)).isoformat(), None
        if cell["type"] in TEXT_TYPES + ("d",) and _DAY.fullmatch(value):
            return date.fromisoformat(value).isoformat(), None
        raise ValueError("只支持数值日期或严格 YYYY-MM-DD 文本，不从文本数字猜序数。")
    except (InvalidOperation, ValueError, OverflowError) as error:
        return None, _issue("article_publish_time", "invalid_publication_date", str(error))


