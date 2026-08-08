"""A pure, readable-summary formatter shared by trace instrumentation.

`workflows/_shared.py::ToolTrace` uses this to summarize tool arguments and
results when recording a `ToolCall`; `evidence/trace.py` uses it to
summarize `CalcStep` inputs/output when building `steps[]`. One
implementation, no dependency in either direction between `workflows/` and
`evidence/` beyond this module.

Never dumps raw rows: a `DataFrame` becomes its row count, long dicts/lists
are truncated with a count marker, long strings are truncated with an
ellipsis. Structured values (dataclasses, pydantic models) are summarized
recursively so a caller never has to hand-write a summary for each tool's
bespoke result type.
"""

import dataclasses
from typing import Any

import pandas as pd
from pydantic import BaseModel

_DEFAULT_MAX_ITEMS = 8
_DEFAULT_MAX_STR_LEN = 200


def summarize_for_trace(value: Any, max_items: int = _DEFAULT_MAX_ITEMS, max_str_len: int = _DEFAULT_MAX_STR_LEN) -> Any:
    if isinstance(value, pd.DataFrame):
        return len(value)

    if isinstance(value, pd.Timestamp):
        return str(value)

    try:
        na_result = pd.isna(value)
    except (TypeError, ValueError):
        na_result = False
    if pd.api.types.is_scalar(na_result) and bool(na_result):
        return None

    if isinstance(value, BaseModel):
        return summarize_for_trace(value.model_dump(), max_items, max_str_len)

    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {
            f.name: summarize_for_trace(getattr(value, f.name), max_items, max_str_len)
            for f in dataclasses.fields(value)
        }

    if isinstance(value, dict):
        items = list(value.items())
        out = {str(k): summarize_for_trace(v, max_items, max_str_len) for k, v in items[:max_items]}
        if len(items) > max_items:
            out["__truncated__"] = f"+{len(items) - max_items} more"
        return out

    if isinstance(value, (list, tuple, set, frozenset)):
        items = list(value)
        out = [summarize_for_trace(v, max_items, max_str_len) for v in items[:max_items]]
        if len(items) > max_items:
            out.append(f"+{len(items) - max_items} more")
        return out

    if isinstance(value, str) and len(value) > max_str_len:
        return value[: max_str_len - 1] + "…"

    return value
