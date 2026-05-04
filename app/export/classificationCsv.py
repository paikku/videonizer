"""Classification dataset CSV.

One row per (image, class). Multi-class images emit multiple rows.
Unlabeled images are dropped at the bundle layer, so they never reach
this writer.
"""
from __future__ import annotations

import re
from typing import Any


_NEEDS_QUOTE = re.compile(r'[",\n\r]')


def _csv_escape(s: str) -> str:
    if _NEEDS_QUOTE.search(s):
        return '"' + s.replace('"', '""') + '"'
    return s


def classification_csv(
    rows: list[dict[str, Any]],
    *,
    include_split: bool,
) -> str:
    header = (
        "filename,class_name,class_id,split"
        if include_split
        else "filename,class_name,class_id"
    )
    lines: list[str] = [header]
    for r in rows:
        cols = [
            _csv_escape(r["fileName"]),
            _csv_escape(r["className"]),
            str(r["classIndex"]),
        ]
        if include_split:
            cols.append(r.get("split") or "")
        lines.append(",".join(cols))
    return "\n".join(lines) + "\n"
