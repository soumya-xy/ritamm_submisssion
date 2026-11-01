"""JSON that keeps money exact.

simplejson with use_decimal=True reads numbers into Decimal and writes Decimal
back as the exact literal (e.g. 1250000.00) - no float rounding anywhere.
"""
from __future__ import annotations

import simplejson


def dumps(obj, **kw) -> str:
    return simplejson.dumps(obj, use_decimal=True, **kw)


def loads(text: str | bytes):
    return simplejson.loads(text, use_decimal=True)


def write_json(path, obj) -> None:
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(dumps(obj, indent=2, ensure_ascii=False))
        fh.write("\n")
