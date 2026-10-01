"""Row predicates and value coercion, as data rather than code.

A metric definition arrives in `source.config`, which is tenant-supplied. PRD B3
rule 2 and SEC-4 require every ingested string to be treated as data and never as an
instruction, so **nothing here evaluates a string**. No `eval`, no `lambda` from
config, no expression compiler. A predicate is a small tree of dicts with a closed
set of operators, which can be read by a human reviewing a binding and cannot do
anything but answer true or false about one row.

Coercion exists because real eval data is not type-clean. In the reference fixture
`escalate` is a JSON boolean while `labelled_escalate` is the string `"true"`, and the
product's own gate relies on that asymmetry. Comparing them without coercion silently
reports every case as a mismatch, which would turn a passing metric into a breach.
"""

from __future__ import annotations

from typing import Any

from layer.metrics.pointer import MISSING, resolve_field

#: Strings that mean true when a field is coerced to bool. Compared case-folded.
_TRUE = frozenset({"true", "yes", "y", "1", "t"})
_FALSE = frozenset({"false", "no", "n", "0", "f", "", "none", "null"})


class BadDefinition(ValueError):
    """A metric definition the engine cannot act on.

    Raised rather than guessed at: a definition is an assertion a human confirmed
    about what a number means, so a malformed one must stop rather than be
    approximated into a different measurement.
    """


def coerce(value: Any, to: str | None) -> Any:
    if to is None or value is MISSING or value is None:
        return value
    if to == "bool":
        return to_bool(value)
    if to == "int":
        return int(value)
    if to == "float":
        return float(value)
    if to == "str":
        return str(value)
    if to == "lower":
        return str(value).strip().lower()
    raise BadDefinition(f"unknown coercion: {to!r}")


def truthy(value: Any) -> bool:
    """Is there something here? Presence, not boolean interpretation.

    For fields that carry content when something is wrong and nothing when it is not —
    an error message, a problem string, a list of violations. The reference fixture's
    own gate does exactly this with `if r["problem"]`.

    Deliberately separate from `to_bool`. Conflating them is how `"false"` ends up
    meaning true: see the note there.
    """
    if value is MISSING or value is None:
        return False
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        return value.strip() != ""
    if isinstance(value, (list, tuple, set, dict)):
        return len(value) > 0
    return True


def to_bool(value: Any) -> bool:
    """Interpret a field that *encodes* a boolean, including as a string.

    A bare `bool(value)` would read the string `"false"` as true, which is the single
    most expensive mistake available here: it would mark every correctly-labelled
    negative case as positive and invert a recall metric. So an unrecognised string
    raises rather than being guessed at — if a definition claims a field is a boolean
    and it holds `"bad json"`, the definition is wrong and must say so.
    """
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)):
        return value != 0
    if isinstance(value, str):
        folded = value.strip().lower()
        if folded in _TRUE:
            return True
        if folded in _FALSE:
            return False
        raise BadDefinition(f"not a boolean-shaped value: {value!r}")
    if value is None:
        return False
    if isinstance(value, (list, dict)):
        return len(value) > 0
    raise BadDefinition(f"not a boolean-shaped value: {value!r}")


def _compare(op: str, left: Any, right: Any) -> bool:
    if op == "eq":
        return left == right
    if op == "ne":
        return left != right
    if left is MISSING or right is MISSING or left is None or right is None:
        # An ordering comparison against an absent value is unanswerable. False is the
        # safe answer: it excludes the row rather than inventing a result for it.
        return False
    if op == "gt":
        return left > right
    if op == "gte":
        return left >= right
    if op == "lt":
        return left < right
    if op == "lte":
        return left <= right
    raise BadDefinition(f"unknown operator: {op!r}")


def evaluate(predicate: dict | None, row: Any, coercions: dict[str, str] | None = None) -> bool:
    """True when `row` satisfies `predicate`. An absent predicate matches everything.

    Supported shapes, all of them dicts:

        {"all": [...]} {"any": [...]} {"not": {...}}
        {"field": "critical", "op": "is_true"}      strict: a boolean, or "true"/"false"
        {"field": "critical", "op": "is_false"}
        {"field": "problem", "op": "is_blank"}      presence: empty, absent or zero
        {"field": "problem", "op": "is_present"}
        {"field": "status", "op": "exists"}
        {"field": "status", "op": "eq", "value": "no_answer"}
        {"field": "team", "op": "eq", "other_field": "labelled_team"}
        {"field": "confidence", "op": "gte", "value": 0.8}
        {"field": "retrieved", "op": "contains_any", "other_field": "expected_articles"}
    """
    if predicate is None:
        return True
    if not isinstance(predicate, dict):
        raise BadDefinition(f"a predicate must be an object: {predicate!r}")
    coercions = coercions or {}

    if "all" in predicate:
        return all(evaluate(p, row, coercions) for p in predicate["all"])
    if "any" in predicate:
        return any(evaluate(p, row, coercions) for p in predicate["any"])
    if "not" in predicate:
        return not evaluate(predicate["not"], row, coercions)

    field = predicate.get("field")
    op = predicate.get("op")
    if not field or not op:
        raise BadDefinition(f"a predicate needs a field and an op: {predicate!r}")

    left = coerce(resolve_field(row, field), coercions.get(field))

    # Two pairs, deliberately not one. `is_true` interprets a field that encodes a
    # boolean and refuses to guess at anything else; `is_present` asks only whether
    # there is content. A single operator covering both is how a message string like
    # "bad json" ends up being asked whether it is true.
    if op == "exists":
        return left is not MISSING
    if op == "is_true":
        return left is not MISSING and to_bool(left)
    if op == "is_false":
        return left is not MISSING and not to_bool(left)
    if op == "is_present":
        return truthy(left)
    if op == "is_blank":
        return not truthy(left)

    if "other_field" in predicate:
        other = predicate["other_field"]
        right = coerce(resolve_field(row, other), coercions.get(other))
    elif "value" in predicate:
        right = predicate["value"]
    else:
        raise BadDefinition(f"{op!r} needs a value or an other_field: {predicate!r}")

    if op == "contains_any":
        haystack = left if isinstance(left, (list, tuple, set)) else []
        needles = right if isinstance(right, (list, tuple, set)) else [right]
        return any(n in haystack for n in needles)

    return _compare(op, left, right)
