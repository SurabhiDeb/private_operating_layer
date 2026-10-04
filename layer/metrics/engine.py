"""Turning a document into a measured number, or declining to.

The problem this solves is not in either specification. The reference fixtures'
committed run files contain **no metric values at all**: every headline number is
derived by iterating per-case rows, and one product's numbers come from a module
carrying a hardcoded article blacklist and rank semantics. A Layer that computed
those itself would be reimplementing each customer's scorer, which is building the
eval half — the thing handoff section 4 forbids outright.

So measurement comes in three tiers, and the third is a refusal.

**Tier 1, the source names a number.** A run-level field, a sidecar carrying a
pass/total pair, a score from an eval platform, a column in a CSV. Read at a
configured pointer. No product knowledge whatsoever.

**Tier 2, per-case rows plus a declarative definition.** A closed set of
aggregations over rows, with an optional filter. Enough for every bar-carrying metric
in both fixtures, including the subset metric that hides a breach behind a rising
headline.

**Tier 3, neither works.** Mean reciprocal rank, recall@k with rank semantics,
bespoke exposure rules. The engine says `Unmeasurable` and the clause is reported as
`uncovered` with reason `no_metric`, verdict `not_measured`. That is an honest answer
in the specification's own vocabulary, and it is the line that stops this file growing
a scorer per customer.

Everything product-specific is in the definition, which lives in `source.config` and
is confirmed by a human alongside the binding, because it is itself an assertion about
what a number means.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field as dc_field
from typing import Any

from layer.core.redact import redact
from layer.metrics.pointer import MISSING, resolve, resolve_field
from layer.metrics.predicates import BadDefinition, coerce, evaluate, to_bool


@dataclass(frozen=True)
class CaseOutcome:
    """One case's result inside one run. **This is the evidence a finding cites.**

    An aggregate cannot be proof: "escalation recall was missed in 7 of 47 runs, worst
    80%" is not derivable from a per-run average, and PRD AC-22 requires a drift finding
    to name the cases that missed the bar. So every aggregation that has a per-case
    notion of passing emits one of these per case.

    `input_redacted` is masked at this point, not later. The field is named for what it
    holds and there is no path that puts raw text into it, so a customer's own words
    never reach a candidate object or a traceback. It is populated only where the
    outcome is not `pass`, per AC-26: nobody asks to see the input of a case that
    passed, and at a typical pass rate that removes roughly 90% of stored text.

    `case_id` beginning with `#` is positional, used only where the source names no id
    for its rows. It is marked rather than silently formatted like a real id, because a
    positional reference stops being correct the moment the row order changes — but
    dropping the row instead would break the counts that drift detection reads.
    """

    case_id: str
    outcome: str
    input_redacted: str | None = None
    trace_id: str | None = None
    trace_url: str | None = None

    @property
    def counted(self) -> bool:
        """Whether this case is in the metric's denominator.

        `skipped` means the case was present in the run and outside what this metric
        measures — a case irrelevant to recall, for instance. It is stored so the run
        is fully accounted for, and excluded from the arithmetic so that
        `passed == number of passes` and `total == number of counted cases` both hold.
        """
        return self.outcome != SKIPPED


@dataclass(frozen=True)
class MetricValue:
    """One measured number, with the sample behind it where there is one.

    `passed` and `total` are present for anything proportion-shaped, because the
    verdict rule is a Wilson interval and it needs n. For a percentile or a mean they
    are None, and such a clause can only ever reach `cannot_confirm` — which is
    correct: a p95 from eleven requests is not evidence about a latency promise.

    `cases` is the per-case layer beneath the number. It is empty for tier 1, where the
    source hands over a number and no rows, and for `count`, `mean` and `percentile`,
    where no per-case notion of passing exists — a case does not pass or fail a p95.
    Emitting `skipped` rows there would store a row per case asserting nothing.
    """

    metric: str
    value: float
    unit: str | None = None
    passed: int | None = None
    total: int | None = None
    detail: dict = dc_field(default_factory=dict)
    cases: tuple[CaseOutcome, ...] = ()

    @property
    def measurable(self) -> bool:
        return True

    @property
    def counted_cases(self) -> tuple[CaseOutcome, ...]:
        return tuple(case for case in self.cases if case.counted)


@dataclass(frozen=True)
class Unmeasurable:
    """The engine declining, with a reason the caller can report.

    `reason` maps onto the `uncovered` finding's own enum, so a refusal here becomes a
    finding rather than an error swallowed in a log.
    """

    metric: str
    reason: str
    note: str | None = None

    @property
    def measurable(self) -> bool:
        return False


Result = MetricValue | Unmeasurable

#: PRD B2's `case_result.outcome`. Mirrored here rather than imported from
#: `layer.db.models`, because an adapter must not depend on the database — that
#: separation is what lets every adapter be tested without one.
PASS = "pass"
FAIL = "fail"
ERROR = "error"
SKIPPED = "skipped"
CASE_OUTCOMES = (PASS, FAIL, ERROR, SKIPPED)

#: Reasons, matching the `uncovered` detail vocabulary in PRD B1.
NO_METRIC = "no_metric"
MISSING_DATA = "missing_data"
BAD_DEF = "bad_definition"

#: Tier 1 and tier 2 aggregations. Anything absent from here is tier 3 by definition,
#: which is the mechanism rather than an oversight.
SUPPORTED_KINDS = (
    "read",       # tier 1: a number at a pointer
    "ratio_of",   # tier 1: a passed/total pair at two pointers
    "rate",       # tier 2: share of rows satisfying a predicate
    "accuracy",   # tier 2: share of rows where two fields agree
    "recall",     # tier 2: tp / (tp + fn)
    "precision",  # tier 2: tp / (tp + fp)
    "count",      # tier 2: how many rows satisfy a predicate
    "mean",       # tier 2: average of a numeric field
    "percentile", # tier 2: nearest-rank percentile of a numeric field
)


def supports(definition: dict) -> bool:
    """Whether a definition is computable, for onboarding to ask before confirming."""
    return isinstance(definition, dict) and definition.get("kind") in SUPPORTED_KINDS


def compute(document: Any, definition: dict) -> Result:
    """Measure `document` according to `definition`.

    Never raises for data reasons. A document that does not carry what the definition
    asks for yields `Unmeasurable`, because one unreadable run must not abort a
    backfill of ninety (EC-4: a partial result, clearly marked, never a silent one).
    """
    if not isinstance(definition, dict):
        return Unmeasurable("unknown", BAD_DEF, "definition is not an object")

    metric = definition.get("metric")
    if not metric:
        return Unmeasurable("unknown", BAD_DEF, "definition names no metric")

    kind = definition.get("kind")
    if kind not in SUPPORTED_KINDS:
        return Unmeasurable(
            metric, NO_METRIC,
            f"no aggregation named {kind!r}. The connected sources do not measure this, "
            f"and the Layer does not compute it on their behalf.",
        )

    unit = definition.get("unit")
    try:
        if kind == "read":
            return _read(document, definition, metric, unit)
        if kind == "ratio_of":
            return _ratio_of(document, definition, metric, unit)
        return _over_rows(document, definition, metric, unit, kind)
    except BadDefinition as exc:
        return Unmeasurable(metric, BAD_DEF, str(exc))


# -- tier 1 ---------------------------------------------------------------------


def _read(document: Any, definition: dict, metric: str, unit: str | None) -> Result:
    pointer = definition.get("pointer")
    if not pointer:
        raise BadDefinition("'read' needs a pointer")
    raw = resolve(document, pointer)
    if raw is MISSING or raw is None:
        return Unmeasurable(metric, MISSING_DATA, f"nothing at {pointer}")
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return Unmeasurable(metric, MISSING_DATA, f"{raw!r} at {pointer} is not a number")
    scaled = value * float(definition.get("scale", 1))
    return MetricValue(metric, scaled, unit, detail={"pointer": pointer})


def _ratio_of(document: Any, definition: dict, metric: str, unit: str | None) -> Result:
    """A pass/total pair the source already counted.

    Worth its own aggregation rather than being folded into `read`, because carrying n
    is what lets the verdict rule say anything at all.
    """
    for key in ("passed", "total"):
        if key not in definition:
            raise BadDefinition(f"'ratio_of' needs a {key} pointer")
    passed_raw = resolve(document, definition["passed"])
    total_raw = resolve(document, definition["total"])
    if MISSING in (passed_raw, total_raw) or None in (passed_raw, total_raw):
        return Unmeasurable(metric, MISSING_DATA, "a passed/total pointer resolved to nothing")
    passed, total = int(passed_raw), int(total_raw)
    if total <= 0:
        return Unmeasurable(metric, MISSING_DATA, "total is zero, so there is nothing to measure")
    return _proportion(metric, passed, total, unit)


# -- tier 2 ---------------------------------------------------------------------


def _rows(document: Any, definition: dict) -> list:
    pointer = definition.get("rows", "/results")
    rows = resolve(document, pointer)
    if rows is MISSING:
        raise BadDefinition(f"no rows at {pointer}")
    if not isinstance(rows, list):
        raise BadDefinition(f"{pointer} is not a list of rows")
    return rows


def _over_rows(document: Any, definition: dict, metric: str, unit: str | None, kind: str) -> Result:
    rows = _rows(document, definition)
    coercions = definition.get("coerce") or {}

    # The subset filter. This is what makes a metric on "the critical cases" expressible
    # without the engine knowing what critical means.
    where = definition.get("filter")
    if where is not None:
        rows = [r for r in rows if evaluate(where, r, coercions)]

    if not rows:
        return Unmeasurable(
            metric, MISSING_DATA,
            "no rows remain after the filter, so there is nothing to measure. "
            "Reporting zero here would assert a failure that was never observed.",
        )

    if kind == "count":
        matched = [r for r in rows if evaluate(definition.get("where"), r, coercions)]
        return MetricValue(metric, float(len(matched)), unit or "count",
                           detail={"rows": len(rows)})

    if kind in ("mean", "percentile"):
        return _numeric(rows, definition, metric, unit, kind)

    if kind == "rate":
        if "where" not in definition:
            raise BadDefinition("'rate' needs a where predicate")
        passed = sum(1 for r in rows if evaluate(definition["where"], r, coercions))
        return _proportion(metric, passed, len(rows), unit,
                           failing=_ids(rows, definition, coercions, definition["where"], False),
                           cases=_cases_by_predicate(rows, definition, coercions, definition["where"]))

    if kind == "accuracy":
        actual, expected = _pair(definition)
        predicate = {"field": actual, "op": "eq", "other_field": expected}
        passed = sum(1 for r in rows if evaluate(predicate, r, coercions))
        return _proportion(metric, passed, len(rows), unit,
                           failing=_ids(rows, definition, coercions, predicate, False),
                           cases=_cases_by_predicate(rows, definition, coercions, predicate))

    return _confusion(rows, definition, metric, unit, kind, coercions)


def _confusion(rows, definition, metric, unit, kind, coercions) -> Result:
    """Recall and precision over a named positive class.

    The positive class has to be stated, not inferred. One fixture treats abstention as
    the positive class and another treats escalation as it, and guessing from field
    names would produce a confidently inverted metric.
    """
    actual_field, expected_field = _pair(definition)
    positive = definition.get("positive", True)
    is_bool = definition.get("positive_is_bool", isinstance(positive, bool))

    def classify(row) -> tuple[bool, bool]:
        actual = coerce(resolve_field(row, actual_field), coercions.get(actual_field))
        expected = coerce(resolve_field(row, expected_field), coercions.get(expected_field))
        if is_bool:
            return to_bool(actual) is True, to_bool(expected) is True
        return actual == positive, expected == positive

    tp = fp = fn = 0
    missed: list = []
    cases: list[CaseOutcome] = []
    for ordinal, row in enumerate(rows, start=1):
        actual_pos, expected_pos = classify(row)
        if actual_pos and expected_pos:
            tp += 1
            in_denominator, satisfied = True, True
        elif actual_pos and not expected_pos:
            fp += 1
            # A false positive is in precision's denominator and outside recall's.
            in_denominator, satisfied = kind == "precision", False
        elif not actual_pos and expected_pos:
            fn += 1
            missed.append(_id_of(row, definition))
            in_denominator, satisfied = kind == "recall", False
        else:
            # A true negative is in neither denominator: the run exercised the case and
            # this metric does not measure it. Stored as `skipped` so the run is fully
            # accounted for, and excluded from the arithmetic.
            in_denominator, satisfied = False, False
        outcome = (
            _outcome_for(row, definition, coercions, satisfied)
            if in_denominator
            else SKIPPED
        )
        cases.append(_case(row, definition, ordinal, outcome))

    denominator = tp + fn if kind == "recall" else tp + fp
    if denominator == 0:
        # No case in this run belongs to the class, so the metric is undefined for it.
        # Returning 0.0 would read as a total failure of something never exercised.
        return Unmeasurable(
            metric, MISSING_DATA,
            f"no case in this run is {'relevant' if kind == 'recall' else 'predicted'} "
            f"for the positive class, so {kind} is undefined here",
        )
    detail = {"tp": tp, "fp": fp, "fn": fn}
    if kind == "recall" and missed:
        detail["missed_ids"] = [m for m in missed if m is not None]
    return _proportion(metric, tp, denominator, unit, extra=detail, cases=cases)


def _numeric(rows, definition, metric, unit, kind) -> Result:
    field = definition.get("field")
    if not field:
        raise BadDefinition(f"'{kind}' needs a field")
    values = []
    for row in rows:
        raw = resolve_field(row, field)
        if raw is MISSING or raw is None:
            continue
        try:
            values.append(float(raw))
        except (TypeError, ValueError):
            continue
    if not values:
        return Unmeasurable(metric, MISSING_DATA, f"no numeric values at {field!r}")

    if kind == "mean":
        value = sum(values) / len(values)
    else:
        p = definition.get("p")
        if p is None:
            raise BadDefinition("'percentile' needs p")
        value = _nearest_rank(values, float(p))
    # No passed/total: a percentile is not a proportion, so the verdict rule cannot
    # build an interval and the clause stays at cannot_confirm. That is the honest
    # outcome rather than a limitation to work around.
    return MetricValue(metric, value, unit, detail={"n": len(values), "kind": kind})


def _nearest_rank(values: list[float], p: float) -> float:
    """Nearest-rank percentile, no interpolation. Honest on tiny samples, where
    interpolation invents a value between two observations that never occurred."""
    ordered = sorted(values)
    index = math.ceil(p / 100 * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


# -- helpers --------------------------------------------------------------------


def _proportion(metric, passed, total, unit, extra=None, failing=None, cases=()) -> MetricValue:
    detail = dict(extra or {})
    if failing:
        detail["failing_ids"] = failing
    return MetricValue(
        metric, passed / total, unit or "ratio", passed=passed, total=total,
        detail=detail, cases=tuple(cases),
    )


def _pair(definition: dict) -> tuple[str, str]:
    actual, expected = definition.get("actual"), definition.get("expected")
    if not actual or not expected:
        raise BadDefinition("this aggregation needs 'actual' and 'expected' fields")
    return actual, expected


def _id_of(row: Any, definition: dict) -> Any:
    return resolve_field(row, definition.get("id_field", "id"))


# -- the per-case layer ---------------------------------------------------------


def _case_id(row: Any, definition: dict, ordinal: int) -> str:
    """The source's own id for this row, or a marked positional one.

    A row with no id cannot be dropped: the counts are what drift detection reads, and
    a missing row would make `runs_total` disagree with the stored evidence. So the
    position is used and marked with `#`, which says plainly that it is positional and
    stops being meaningful if the rows are reordered.
    """
    identifier = _id_of(row, definition)
    if identifier is MISSING or identifier is None or identifier == "":
        return f"#{ordinal}"
    return str(identifier)


def _case_input(row: Any, definition: dict, outcome: str) -> str | None:
    """The redacted input, for a case this metric measured and that did not pass.

    Three things are deliberate. There is **no default** field name, unlike `id_field`
    and `rows`: guessing which field holds the customer's own words and storing it
    would be the one default in this codebase whose failure mode is retaining other
    people's personal data nobody asked for. Redaction happens here rather than at the
    database, so raw text never travels. And a case nobody will ask about stores
    nothing, which the schema also refuses (AC-26).

    **`skipped` stores no input either, and that was a defect for an hour.** AC-26 and
    PRD B2 phrase the rule as "only where `outcome` is not `pass`", so the first
    version of this function suppressed the input on a pass alone — and then stored the
    text of every true negative, which for one fixture's recall metric was nine of
    fourteen cases per run. The reasoning behind the rule is that nobody asks to see
    the input of a case that passed; nobody asks to see the input of a case this metric
    never measured either. Caught by printing real output rather than by a test, which
    is why the CHECK now covers it too.
    """
    if outcome not in (FAIL, ERROR):
        return None
    field = definition.get("input_field")
    if not field:
        return None
    raw = resolve_field(row, field)
    if raw is MISSING or raw is None:
        return None
    return redact(str(raw), extra_patterns=_extra_redactions(definition))


def _extra_redactions(definition: dict) -> list[tuple[str, str]] | None:
    """Tenant-supplied patterns, as (name, regex) pairs. No fixed list covers what
    another company's domain leaks — a policy number, an internal customer reference."""
    extra = definition.get("redact_patterns")
    if not extra:
        return None
    if isinstance(extra, dict):
        return list(extra.items())
    return [tuple(pair) for pair in extra]


def _case_trace(row: Any, definition: dict) -> tuple[str | None, str | None]:
    """The pointer to the conversation itself, which stays at the source.

    Copied at observation time precisely because the source will delete the body: a
    finding citing a deleted trace is PRD B5 item 8, and the pointer plus the recorded
    outcome are what let the Layer say the body is gone while still showing what
    happened (AC-23). Where a source records no trace at all, both are None and AC-24
    reports `not_applicable` rather than a silent zero.
    """
    trace_id = resolve_field(row, definition.get("trace_id_field", "trace_id"))
    trace_url = resolve_field(row, definition.get("trace_url_field", "trace_url"))
    return (
        None if trace_id in (MISSING, None, "") else str(trace_id),
        None if trace_url in (MISSING, None, "") else str(trace_url),
    )


def _case(row: Any, definition: dict, ordinal: int, outcome: str) -> CaseOutcome:
    trace_id, trace_url = _case_trace(row, definition)
    return CaseOutcome(
        case_id=_case_id(row, definition, ordinal),
        outcome=outcome,
        input_redacted=_case_input(row, definition, outcome),
        trace_id=trace_id,
        trace_url=trace_url,
    )


def _outcome_for(row: Any, definition: dict, coercions: dict, satisfied: bool) -> str:
    """`pass`, or `fail` unless the source itself recorded an error on this row.

    An error is distinguished from a failure because they mean different things to a
    human triaging the run: one is the product being wrong, the other is the harness
    not having run. It never overrides a pass — the metric counted that row as passing,
    and relabelling it here would make the stored cases disagree with the number they
    are the evidence for.
    """
    if satisfied:
        return PASS
    field = definition.get("error_field")
    if field:
        raw = resolve_field(row, field)
        if raw not in (MISSING, None, "", False):
            return ERROR
    return FAIL


def _cases_by_predicate(rows, definition, coercions, predicate) -> tuple[CaseOutcome, ...]:
    """One case per row in the metric's population, for `rate` and `accuracy`.

    The population is the rows that survived the subset filter, which is why a metric
    on "the critical cases" stores the critical cases and not the whole run. The rest
    of the run belongs to whatever metric measures it.
    """
    out = []
    for ordinal, row in enumerate(rows, start=1):
        satisfied = evaluate(predicate, row, coercions) is True
        out.append(_case(row, definition, ordinal, _outcome_for(row, definition, coercions, satisfied)))
    return tuple(out)


def _ids(rows, definition, coercions, predicate, wanted: bool) -> list:
    out = []
    for row in rows:
        if evaluate(predicate, row, coercions) is wanted:
            identifier = _id_of(row, definition)
            if identifier is not MISSING and identifier is not None:
                out.append(identifier)
    return out
