"""The five findings. Plain SQL over what onboarding loaded.

No metric name and no product name appears in any query here. Everything they know comes
from `clause`, `binding`, `observation`, `enforcement_fact` and `link`, which is what makes a
condition detected in one product detectable in a product nobody has seen.

**Drift compares the recorded number; the verdict compares the interval.** This is the one
place the distinction matters and it is deliberate. "Did a run come in under the bar" is a
factual question about a number that was written down, and PRD B6 sets drift detection recall
at 100% precisely because it is deterministic. "Can we say the product is meeting its bar
right now" is a question about evidence, and small samples cannot answer it — which is why a
clause can read `cannot_confirm` while drift reports seven breaches against it. Both are
true, and collapsing them would lose one of them.

**No summary states a cause.** B3 rule 6: "X first failed at v3 and the prompt sha changed at
v3" is permitted, "the prompt change caused it" is forbidden. Every sentence below is built
from counts, values and timestamps.

**A finding resting on an old measurement says so, in words.** Every set carries
`stale_sources`, every finding that rests on a measurement carries `as_of`, and one outside
its source's window carries `stale` and a sentence naming the source and the age (B3 rule
12, AC-29). The window is the source's own: a global threshold would be the Layer inventing
a cadence for somebody else's eval suite, and the one that used to live here — 30 days, for
every product alike — is gone.

**A drift finding cites the cases that missed the bar.** B3 rule 10: an empty
`failing_cases` beside `runs_missed > 0` "is a defect, not a terse answer". It can also be
an honest answer — a source that reports a metric as a single number has no per-case layer
for the Layer to have stored — so the two are distinguished by name in
`failing_cases_state` rather than by an empty list that could mean either.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from layer.core import freshness
from layer.core.errors import Refusal
from layer.db.models import (
    Binding,
    CaseResult,
    Clause,
    EnforcementFact,
    Observation,
    Product,
    Source,
)
from layer.metrics.engine import ERROR, FAIL
from layer.findings import traces
from layer.findings.shapes import (
    DRIFT,
    METRIC_WITHOUT_CLAUSE,
    NO_ASSERTION,
    NO_METRIC,
    NOT_MEASURED_RECENTLY,
    STALLED_DECISION,
    UNCOVERED,
    UNDERSPECIFIED,
    UNENFORCED,
    Finding,
    FindingSet,
)
from layer.onboarding import bindings as binding_gate

#: How many individual cases a drift finding carries. A cap, because a finding is read by a
#: human and fifty cases is already more than anyone reviews in one sitting — and
#: `failing_cases_total` always states the real number, so the cap narrows the list and
#: never the claim.
MAX_FAILING_CASES = 50

#: `failing_cases_state`, which says what an empty list means.
CITED = "cited"
#: The source reports this metric as a number and records no per-case rows beneath it, so
#: there is nothing the Layer could have stored. AC-24's `not_applicable`.
NOT_APPLICABLE = "not_applicable"
#: Per-case rows are stored beneath these runs and none of them records a failure. Stated
#: rather than left as an empty list, which would read as B3 rule 10's defect and hide the
#: one thing a reader needs: that the proof exists and does not contain what was expected.
NO_FAILING_CASES = "no_failing_cases"

#: Trace states, from `layer.findings.traces`, which is also what the `case` resolver
#: reads: a finding saying the body is gone while its own citation hands over the dead
#: link would be B3 rule 11 broken in the space of one screen.
TRACE_AVAILABLE = traces.AVAILABLE
TRACE_PAST_RETENTION = traces.PAST_RETENTION
TRACE_NOT_APPLICABLE = traces.NOT_APPLICABLE


@dataclass(frozen=True)
class _Bar:
    """A clause and the metric bound to it, which is all a query needs."""

    clause: Clause
    metric: str


# -- drift -----------------------------------------------------------------------


def find_drift(session: Session, *, product: Product, registry=None) -> FindingSet:
    """Any observation in the full sequence that violates its clause.

    The whole sequence, not the latest. A gate that reads one run cannot see a breach sitting
    mid-history, and a dashboard showing the current value cannot either — which is the
    condition this product exists to surface.
    """
    out: list[Finding] = []
    windows = freshness.windows_for(session, product.id)
    now = _now(session)
    for bar in _bars(session, product):
        rows = _series(session, product.id, bar.metric)
        if not rows:
            continue
        breaching = [r for r in rows if _violates(bar.clause, r.value)]
        if not breaching:
            continue

        worst = _worst(bar.clause, breaching)
        latest = rows[-1]
        first = breaching[0]
        current = _violates(bar.clause, latest.value)
        cases, cases_total, cases_state = _failing_cases(session, product, breaching)
        staleness = _staleness(windows, latest, now)

        out.append(Finding(
            kind=DRIFT,
            product=product.key,
            clause_ref=bar.clause.ref,
            first_seen=first.measured_at,
            current=current,
            as_of=latest.measured_at,
            stale=bool(staleness and staleness.stale),
            summary=_drift_summary(
                bar, rows, breaching, worst, latest, current, cases, cases_total,
                cases_state, staleness,
            ),
            evidence=(
                [f"clause:{bar.clause.ref}"]
                + [f"obs:{r.id}" for r in breaching[:12]]
                # The cases are evidence in their own right, not a decoration on the
                # runs: B3 rule 10 is that a bar is never asserted missed without them.
                + [c["ref"] for c in cases]
            ),
            detail={
                "metric": bar.metric,
                "stated": _bar_text(bar.clause),
                "observed": latest.value,
                "worst": worst.value,
                "worst_run": worst.run_id,
                "runs_missed": len(breaching),
                "runs_total": len(rows),
                "latest_run": latest.run_id,
                "latest_verdict": bar.clause.verdict,
                "first_breaching_run": first.run_id,
                "case_ids": _case_ids(breaching),
                "failing_cases": cases,
                "failing_cases_total": cases_total,
                "failing_cases_state": cases_state,
                "as_of": latest.measured_at.isoformat(),
                "staleness": staleness.as_dict() if staleness else None,
                "breach_revisions": sorted({r.code_rev for r in breaching if r.code_rev}),
                "clean_revisions": sorted(
                    {r.code_rev for r in rows if r.code_rev}
                    - {r.code_rev for r in breaching if r.code_rev}
                ),
            },
        ))
    return _resolved(session, product, DRIFT, out, registry)


def _drift_summary(
    bar, rows, breaching, worst, latest, current, cases, cases_total, cases_state,
    staleness=None,
) -> str:
    parts = [
        f"{bar.clause.ref} {bar.clause.label or bar.metric} {_bar_text(bar.clause)} "
        f"was missed in {len(breaching)} of {len(rows)} runs, "
        f"worst {_value_text(bar.clause, worst.value)}"
        + (f" ({worst.passed} of {worst.total})" if worst.total else "")
        + (f" in run {worst.run_id}" if worst.run_id else "")
        + "."
    ]
    if not current:
        # The sentence that makes condition 1 legible. It states what a narrower check can
        # see, which is a fact about the check rather than a claim about the product.
        parts.append(
            f"The latest run, {latest.run_id or 'the most recent'}, is at "
            f"{_value_text(bar.clause, latest.value)}, so a check on the latest run alone "
            f"shows nothing."
        )
    elif staleness is not None and staleness.stale:
        # "still misses" is a claim about now, and the next sentence is about to say this
        # measurement is not current. Caught by reading the output: the two sentences
        # together asserted and withdrew the same thing.
        parts.append(
            f"The newest run on record, {latest.run_id or 'the most recent'}, is at "
            f"{_value_text(bar.clause, latest.value)}, below the bar."
        )
    else:
        parts.append(
            f"The latest run, {latest.run_id or 'the most recent'}, is at "
            f"{_value_text(bar.clause, latest.value)} and still misses the bar."
        )
    parts.append(_cases_sentence(cases, cases_total, cases_state))
    if staleness is not None and staleness.stale:
        # AC-29: the age in the finding's own words, not only in a field. Placed before
        # the revisions so a reader meets the caveat before the detail it qualifies.
        parts.append(f"The newest run here is not current — {staleness.describe()}.")

    breach_revs = sorted({r.code_rev for r in breaching if r.code_rev})
    clean_revs = sorted({r.code_rev for r in rows if r.code_rev} - set(breach_revs))
    if breach_revs:
        # Recorded, never interpreted. H1's discipline: the revisions are stated beside the
        # numbers and no relationship between them is asserted.
        parts.append(f"Breaching runs record {', '.join(breach_revs[:4])}.")
        if clean_revs:
            parts.append(f"Runs at {', '.join(clean_revs[:4])} do not breach.")
    return " ".join(parts)


def _failing_cases(
    session: Session, product: Product, breaching: list[Observation]
) -> tuple[list[dict], int, str]:
    """The individual cases beneath the runs that missed the bar. **This is the proof.**

    PRD AC-22 and B3 rule 10: a finding that asserts a bar was missed cites the cases
    that missed it, and an empty list beside `runs_missed > 0` is a defect rather than a
    terse answer. It is read from `case_result` and never from the eval platform, which
    is AC-24's whole point — the proof outlives the source's retention window.

    **The cases are the ones the metric recorded as `fail` or `error`, which is a
    statement about the metric and not about the bar.** For a floor — accuracy at least
    85% — those are the same rows. For a cap expressed over a per-case predicate they
    would not be, so the summary says "recorded as failing" rather than "caused the
    breach", and no sentence anywhere claims one produced the other (B3 rule 6).
    """
    if not breaching:
        return [], 0, NOT_APPLICABLE

    ids = [row.id for row in breaching]
    rows = session.execute(
        select(
            CaseResult.observation_id,
            CaseResult.case_id,
            CaseResult.outcome,
            CaseResult.trace_id,
            CaseResult.trace_url,
            CaseResult.measured_at,
            Observation.run_id,
            Observation.run_url,
            Observation.source_id,
        )
        .join(Observation, Observation.id == CaseResult.observation_id)
        .where(
            CaseResult.observation_id.in_(ids),
            CaseResult.outcome.in_((FAIL, ERROR)),
        )
        # Oldest first, matching `first_seen`: a reader following a degradation wants the
        # earliest proof of it, not an arbitrary slice of the newest.
        .order_by(CaseResult.measured_at, CaseResult.case_id)
    ).all()

    if not rows:
        stored = session.execute(
            select(func.count())
            .select_from(CaseResult)
            .where(CaseResult.observation_id.in_(ids))
        ).scalar_one()
        return [], 0, NO_FAILING_CASES if stored else NOT_APPLICABLE

    retention = traces.retention_for(session, product.id)
    now = _now(session) if retention else None

    cases = []
    for row in rows[:MAX_FAILING_CASES]:
        state = traces.state_of(
            trace_id=row.trace_id, trace_url=row.trace_url,
            measured_at=row.measured_at,
            retention=retention.get(row.source_id), now=now,
        )
        cases.append({
            "ref": f"case:{row.observation_id}/{row.case_id}",
            "case_id": row.case_id,
            "outcome": row.outcome,
            "run_id": row.run_id,
            "run_url": row.run_url,
            "trace_url": row.trace_url if state == TRACE_AVAILABLE else None,
            "trace_available": state == TRACE_AVAILABLE,
            "trace_state": state,
        })
    return cases, len(rows), CITED


def _cases_sentence(cases: list[dict], total: int, state: str) -> str:
    """One sentence about the proof, including when there is none and why.

    B3 rule 10 makes the absence of cases a reportable condition rather than a quiet
    omission, so every state gets a sentence and none of them is silence.
    """
    if state == NOT_APPLICABLE:
        return (
            "No per-case rows are stored beneath these runs: this source reports the "
            "metric as a number and records no cases, so the runs are the finest "
            "evidence available."
        )
    if state == NO_FAILING_CASES:
        return (
            "Per-case rows are stored beneath these runs and none of them is recorded "
            "as a failure, so the number and the cases beneath it should be read "
            "together before this is acted on."
        )
    # Distinct ids, because the same case failing in seven runs is one case a human
    # goes and looks at, and a list reading "14, 14, 14, 14, 14, 14, 14" says less than
    # the count already did.
    ids: list[str] = []
    for case in cases:
        if case["case_id"] not in ids:
            ids.append(case["case_id"])
    sentence = (
        f"{total} case(s) across these runs are recorded as failing"
        + (f", {len(cases)} of them cited here" if len(cases) < total else "")
        + f". Case ids: {', '.join(ids[:10])}"
        + ("…" if len(ids) > 10 else "")
        + "."
    )
    without = sum(1 for c in cases if c["trace_state"] == TRACE_NOT_APPLICABLE)
    gone = sum(1 for c in cases if c["trace_state"] == TRACE_PAST_RETENTION)
    if gone:
        sentence += (
            f" {gone} of the cited cases point at a trace the source no longer keeps; "
            f"the recorded outcome is shown instead of a dead link."
        )
    if without == len(cases) and cases:
        sentence += " This source records no trace pointers, so each case cites its run."
    return sentence


# -- unenforced ------------------------------------------------------------------


def find_unenforced(session: Session, *, product: Product, registry=None) -> FindingSet:
    """A stated bar that CI does not check, or checks against the wrong run set.

    AC-15 turns on the second case. `enforced: true` with a narrowed scope is not a weaker
    version of enforcement; it is a check that passes while a breach sits in history.
    """
    facts = {
        fact.metric: fact
        for fact in session.execute(
            select(EnforcementFact).where(EnforcementFact.product_id == product.id)
        ).scalars()
    }
    scanned = session.execute(
        select(func.count()).select_from(EnforcementFact).where(
            EnforcementFact.product_id == product.id
        )
    ).scalar_one()

    out: list[Finding] = []
    for bar in _bars(session, product, require_binding=False):
        fact = facts.get(bar.metric)
        if fact is not None and fact.enforced and fact.scope == "all_runs" and not fact.partial:
            continue

        evidence = [f"clause:{bar.clause.ref}"]
        if fact is not None:
            evidence.append(
                f"file:{fact.file}#L{fact.line}" if fact.line else f"file:{fact.file}"
            )

        out.append(Finding(
            kind=UNENFORCED,
            product=product.key,
            clause_ref=bar.clause.ref,
            first_seen=None,
            current=True,
            summary=_unenforced_summary(bar, fact, scanned),
            evidence=evidence,
            detail={
                "metric": bar.metric,
                "stated": _bar_text(bar.clause),
                "threshold": fact.threshold if fact else None,
                "enforced": bool(fact and fact.enforced),
                "partial": bool(fact and fact.partial),
                "partial_note": fact.partial_note if fact else None,
                "scope": fact.scope if fact else None,
                "known_failing": bool(fact and fact.known_failing),
                "file": fact.file if fact else None,
                "line": fact.line if fact else None,
                "ci_files": sorted({f.file for f in facts.values()}),
            },
        ))
    return _resolved(session, product, UNENFORCED, out, registry)


def _unenforced_summary(bar, fact, scanned: int) -> str:
    name = f"{bar.clause.ref} {bar.clause.label or bar.metric}"
    stated = _bar_text(bar.clause)

    if fact is None:
        if not scanned:
            return (
                f"{name} states {stated}, and no CI file has been scanned for this product, "
                f"so whether anything checks it is unknown."
            )
        return (
            f"{name} states {stated}, and no scanned CI file checks it. A stated bar that "
            f"nothing checks cannot fail a build."
        )
    if not fact.enforced:
        return (
            f"{name} states {stated} and {fact.file} contains a check for it, but "
            f"{fact.partial_note or 'the check cannot fail a build'}."
        )
    if fact.scope == "latest_only":
        return (
            f"{name} states {stated} and is checked in {fact.file}, but only against the "
            f"newest run. A breach in any earlier run of the same prompt is never seen."
        )
    if fact.scope == "undetermined":
        return (
            f"{name} states {stated} and is checked in {fact.file}. The run set that check "
            f"reads could not be determined from the file, so whether an earlier breach "
            f"would be caught is unknown."
        )
    return (
        f"{name} states {stated} and is checked in {fact.file}, but the check does not "
        f"fully cover the clause: {fact.partial_note or 'the enforced value could not be read'}."
    )


# -- uncovered -------------------------------------------------------------------


def find_uncovered(session: Session, *, product: Product, registry=None) -> FindingSet:
    """A promise nothing measures, or a measurement nothing promises.

    Four reasons, and the difference between them is the finding. "Nothing measures this
    promise" is a gap in the eval suite; "something is measured that nothing promised" is a
    gap in the specification, and H16 is explicit that there "the gap is the absent clause,
    not the metric".
    """
    out: list[Finding] = []
    bound = binding_gate.confirmed_metrics(session, product=product)
    bound_refs = set(bound.values())
    now = _now(session)
    windows = freshness.windows_for(session, product.id)

    for clause in _measurable_clauses(session, product):
        if clause.ref not in bound_refs:
            out.append(_uncovered(
                product, clause, NO_ASSERTION,
                f"{clause.ref} {clause.label or ''} states {_bar_text(clause)} and no "
                f"measurement is bound to it, so nothing in the connected sources answers "
                f"this promise.".replace("  ", " "),
            ))
            continue

        metric = next(m for m, ref in bound.items() if ref == clause.ref)
        rows = _series(session, product.id, metric)
        if not rows:
            out.append(_uncovered(
                product, clause, NO_METRIC,
                f"{clause.ref} is bound to {metric}, and no run in the connected sources "
                f"carries a value for it. The bar stands unmeasured.",
                metric=metric,
            ))
            continue

        latest = rows[-1]
        staleness = _staleness(windows, latest, now)
        if staleness is not None and staleness.stale:
            # Reported against the source's own window rather than a number chosen here.
            # A quarterly review source is not overdue at 31 days, and a nightly eval is
            # overdue long before that — one threshold cannot be right for both.
            out.append(_uncovered(
                product, clause, NOT_MEASURED_RECENTLY,
                f"{clause.ref} was last measured in run "
                f"{latest.run_id or 'the most recent'}, and {staleness.phrase()}. "
                f"A number that stopped being taken is not evidence that nothing changed.",
                metric=metric,
                evidence_extra=[f"obs:{latest.id}"],
                detail_extra={
                    "latest_run": latest.run_id,
                    "as_of": latest.measured_at.isoformat(),
                    "staleness": staleness.as_dict(),
                },
                as_of=latest.measured_at,
                stale=True,
            ))

    # The other direction. H16: measured, promised nowhere.
    for metric in _unbound_metrics(session, product, set(bound)):
        rows = _series(session, product.id, metric)
        latest = rows[-1]
        out.append(Finding(
            kind=UNCOVERED,
            product=product.key,
            clause_ref=None,
            first_seen=rows[0].measured_at,
            current=True,
            summary=(
                f"{metric} is measured across {len(rows)} run(s), latest "
                f"{_plain(latest.value)}, and no clause states a bar for it. The gap is the "
                f"absent clause rather than the measurement."
            ),
            evidence=[f"eval_metric:{product.key}/{metric}", f"obs:{latest.id}"],
            detail={
                "reason": METRIC_WITHOUT_CLAUSE,
                "metric": metric,
                "runs": len(rows),
                "latest": latest.value,
            },
        ))
    return _resolved(session, product, UNCOVERED, out, registry)


def _uncovered(
    product, clause, reason, summary, *, metric=None, evidence_extra=None,
    detail_extra=None, as_of=None, stale=False,
) -> Finding:
    return Finding(
        kind=UNCOVERED,
        product=product.key,
        clause_ref=clause.ref,
        first_seen=None,
        current=True,
        as_of=as_of,
        stale=stale,
        summary=summary,
        evidence=[f"clause:{clause.ref}", *(evidence_extra or [])],
        detail={
            "reason": reason,
            "metric": metric,
            "stated": _bar_text(clause),
            **(detail_extra or {}),
        },
    )


# -- the two that need sources nothing has bound yet -----------------------------


def find_stalled_decisions(session: Session, *, product: Product, registry=None) -> FindingSet:
    """C7. A decision that produced no pull request, ticket or spec change.

    Refused rather than answered empty. There is no `decision` source to read, and "no
    stalled decisions" would be a claim about decisions the Layer has never seen.
    """
    from layer.onboarding import state

    if not state.sources_by_role(session, product=product).get("decision"):
        return FindingSet(STALLED_DECISION, stale_sources=freshness.stale_sources(
            session, product=product,
        ), refusal=Refusal(
            reason=(
                f"{product.key} has no decision source bound, so decisions that produced no "
                f"action cannot be found. An empty answer here would be a claim about "
                f"decisions the Layer has never seen."
            ),
            missing=["a source with role 'decision'"],
        ))
    return _resolved(session, product, STALLED_DECISION, [], registry)


def find_underspecified(session: Session, *, product: Product, registry=None) -> FindingSet:
    """C6 and H8. The eval passes and production is out of band.

    The highest-value finding in the specification, and the one that needs a source this
    phase does not have. Refused by name rather than answered empty.
    """
    from layer.onboarding import state

    if not state.sources_by_role(session, product=product).get("production"):
        return FindingSet(UNDERSPECIFIED, stale_sources=freshness.stale_sources(
            session, product=product,
        ), refusal=Refusal(
            reason=(
                f"{product.key} has no production source bound. An under-specified "
                f"requirement is one whose eval passes while production sits outside its "
                f"band, and without production readings neither half of that can be seen."
            ),
            missing=["a source with role 'production'", "a declared band for a production metric"],
        ))
    return _resolved(session, product, UNDERSPECIFIED, [], registry)


# -- everything, for the overview ------------------------------------------------


def find_all(session: Session, *, product: Product) -> dict[str, FindingSet]:
    """Every query, sharing one resolver registry.

    Shared because building it opens the product's repository, and doing that five times to
    answer one question is waste rather than caution.
    """
    from layer.findings.citations import registry_for

    registry = registry_for(session, product=product)
    out: dict[str, FindingSet] = {}
    for query in (
        find_drift, find_unenforced, find_uncovered,
        find_stalled_decisions, find_underspecified,
    ):
        result = query(session, product=product, registry=registry)
        out[result.kind] = result
    return out


def _resolved(
    session: Session, product: Product, kind: str, findings: list[Finding], registry
) -> FindingSet:
    """Attach the URLs before the findings leave this module.

    Resolution is not left to the caller. A query used on its own returned findings whose
    `evidence_links` were empty and whose `unresolved` was empty too, which reads as "every
    citation resolved" when in truth none had been tried — AC-7 and AC-14 broken by omission
    rather than by a wrong URL.
    """
    if findings:
        if registry is None:
            from layer.findings.citations import registry_for

            registry = registry_for(session, product=product)
        for finding in findings:
            finding.resolve(registry)
    return FindingSet(
        kind, findings, stale_sources=freshness.stale_sources(session, product=product)
    )


# -- shared ----------------------------------------------------------------------


def _bars(session: Session, product: Product, *, require_binding: bool = True) -> list[_Bar]:
    """Clauses with a numeric bar, paired with the metric that answers them.

    A clause read from prose carries no metric name, so its metric comes from the confirmed
    binding. `require_binding` is False for the enforcement query, which can look for a
    clause's own metric name in CI whether or not anything measures it yet.
    """
    bound = binding_gate.confirmed_metrics(session, product=product)
    by_ref = {ref: metric for metric, ref in bound.items()}
    out: list[_Bar] = []
    for clause in _measurable_clauses(session, product):
        metric = by_ref.get(clause.ref) or (None if require_binding else clause.metric)
        if metric:
            out.append(_Bar(clause, metric))
    return out


def _measurable_clauses(session: Session, product: Product) -> list[Clause]:
    return list(session.execute(
        select(Clause)
        .where(
            Clause.product_id == product.id,
            Clause.status == "active",
            Clause.comparator.is_not(None),
            Clause.value.is_not(None),
        )
        .order_by(Clause.ref)
    ).scalars())


def _series(session: Session, product_id: uuid.UUID, metric: str) -> list[Observation]:
    """Every observation for a metric, oldest first. The full sequence is the point."""
    return list(session.execute(
        select(Observation)
        .where(Observation.product_id == product_id, Observation.metric == metric)
        .order_by(Observation.measured_at, Observation.id)
    ).scalars())


def _unbound_metrics(session: Session, product: Product, bound: set[str]) -> list[str]:
    rows = session.execute(
        select(Observation.metric)
        .where(Observation.product_id == product.id)
        .distinct()
        .order_by(Observation.metric)
    ).scalars().all()
    rejected = {
        metric for metric, in session.execute(
            select(Binding.metric).where(
                Binding.product_id == product.id, Binding.decision == "rejected"
            )
        ).all()
    }
    # A rejected pairing is a decision, not a gap: a human looked and said this number does
    # not answer that promise. Reporting it as uncovered would re-open a closed question.
    return [m for m in rows if m not in bound and m not in rejected]


def _violates(clause: Clause, value: float) -> bool:
    if clause.comparator == ">=":
        return value < clause.value
    if clause.comparator == "<=":
        return value > clause.value
    if clause.comparator == "==":
        return abs(value - clause.value) > 1e-9
    if clause.comparator == "between" and clause.value_high is not None:
        return not (clause.value <= value <= clause.value_high)
    return False


def _worst(clause: Clause, rows: list[Observation]) -> Observation:
    if clause.comparator == "<=":
        return max(rows, key=lambda r: r.value)
    if clause.comparator == "between" and clause.value_high is not None:
        middle = (clause.value + clause.value_high) / 2
        return max(rows, key=lambda r: abs(r.value - middle))
    return min(rows, key=lambda r: r.value)


def _case_ids(rows: list[Observation]) -> list[str]:
    out: list[str] = []
    for row in rows:
        for key in ("missed_ids", "failing_ids"):
            for identifier in (row.detail or {}).get(key, []) or []:
                if str(identifier) not in out:
                    out.append(str(identifier))
    return out


def _bar_text(clause: Clause) -> str:
    if clause.value is None:
        return "no bar"
    if clause.comparator == "between":
        return f"between {_plain(clause.value)} and {_plain(clause.value_high)}"
    return f"{clause.comparator} {_value_text(clause, clause.value)}"


def _value_text(clause: Clause, value: float) -> str:
    if clause.unit == "ratio":
        return f"{value * 100:.1f}%".replace(".0%", "%")
    if clause.unit in ("duration_s", "duration_ms", "currency", "count", "tokens"):
        return _plain(value)
    return _plain(value)


def _now(session: Session):
    """The database's clock, not the process's. Every other timestamp here came from it."""
    return session.execute(select(func.now())).scalar_one()


def _staleness(windows: dict, observation, now):
    """How old one observation is against the window of the source that delivered it.

    Per observation rather than per product, because a product's sources do not share a
    cadence: a nightly eval and a quarterly review are both normal, and a finding resting
    on one says nothing about the other. An observation with no source, or a source with
    no stated window, yields None and nothing is degraded or reported.

    `windows` and `now` are read once per query and passed in. Deliberately not cached on
    the session: a cache that outlived the query would answer with the window a source had
    before somebody changed it, which is the same class of mistake as a stale measurement
    read as current.
    """
    window = windows.get(observation.source_id)
    if window is None or window.window is None:
        return None
    return freshness.staleness_of(
        observation.measured_at, window=window.window, now=now, source=window.label
    )


def _plain(value: float | None) -> str:
    if value is None:
        return "n/a"
    return f"{value:g}"
