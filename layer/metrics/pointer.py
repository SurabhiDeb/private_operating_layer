"""Addressing a value inside a document the Layer has never seen before.

RFC 6901 JSON Pointer, because a metric definition has to say *where* a number lives
without the Layer knowing anything about the document's shape. `/meta/p95_seconds`
and `/results` are the whole vocabulary needed to read a run record from any product.

Deliberately not a path expression language. A pointer addresses one location and
cannot compute, which keeps a tenant-supplied `source.config` incapable of doing
anything but naming a place.
"""

from __future__ import annotations

from typing import Any

MISSING = object()


def resolve(document: Any, pointer: str) -> Any:
    """The value at `pointer`, or `MISSING`.

    `MISSING` rather than None, because a document may legitimately hold a null and
    "absent" and "present but null" lead to different findings: one is a source that
    does not carry the metric, the other is a source that measured nothing.
    """
    if pointer in ("", "/"):
        return document
    if not pointer.startswith("/"):
        raise ValueError(f"a json pointer must start with '/': {pointer!r}")

    current = document
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, dict):
            if token not in current:
                return MISSING
            current = current[token]
        elif isinstance(current, list):
            if not token.lstrip("-").isdigit():
                return MISSING
            index = int(token)
            if index < 0 or index >= len(current):
                return MISSING
            current = current[index]
        else:
            return MISSING
    return current


def resolve_field(row: Any, field: str) -> Any:
    """A field on a row, by name or by pointer.

    Most definitions name a plain key (`escalate`), so that is the common path. A
    pointer is accepted for nested rows without needing a second syntax.
    """
    if field.startswith("/"):
        return resolve(row, field)
    if isinstance(row, dict):
        return row.get(field, MISSING)
    return MISSING
