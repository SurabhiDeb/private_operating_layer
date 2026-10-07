"""Findings into proposals.

**Neither document specifies this**, which is why the rules are written out here rather
than left to taste. The generators are thin over phase 2's findings — the expensive part
was detecting the conditions, and that exists — but *which* proposal a finding justifies
is a judgement, and an undocumented judgement in this position would be the Layer quietly
deciding what the specification should say.

**Every generator is deterministic.** No model is called, so EC-10's "fail closed, no
partial proposal is written" holds structurally: a run is one transaction, and a raise
part-way through writes nothing. When the LLM pass over spec prose arrives it sits above
this, never inside it, for the same reason.

**Four finding kinds produce proposals and two deliberately do not.**

| Finding | Proposal | Why |
|---|---|---|
| `drift` | `clause_change` or `ticket` | US-2, and never both in one proposal |
| `unenforced` | `ci_change` | US-3, US-12. Including H14, where the gate's run set is the change |
| `uncovered`, `metric_without_clause` | `new_clause` | H16: "the gap is the absent clause, not the metric" |
| a bar comfortably cleared for months | `clause_change` raising it | EC-12, the one teams never build |
| `uncovered`, `no_assertion` / `no_metric` | **nothing** | See `_why_not_no_assertion` |
| `underspecified` | **nothing** | H8 asks for the condition to be reported. What proposal it justifies is not specified anywhere, and choosing one here would be the Layer deciding what the spec should say |

**The cap is read from the product and nothing is deleted by it.** AC-33: `harvest_cap`,
default twenty. Candidates beyond it are stored with their rank and stay queryable, because
US-1 is explicit that the cap is a priority order rather than a deletion (AC-34).

**The rank is a queue order and is never presented as importance or severity** (AC-35).
Every candidate carries the signals behind its rank so a human can disagree with the
ordering rather than only with the proposals.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from layer.answers import writes
from layer.answers.shapes import ProposedChange
from layer.core.errors import NotLive, Refusal
from layer.db.models import Clause, Product
from layer.findings import queries
from layer.findings.shapes import (
    DRIFT,
    METRIC_WITHOUT_CLAUSE,
    UNCOVERED,
    UNENFORCED,
)
from layer.onboarding import bindings as binding_gate
from layer.onboarding import state

#: Which generator produced a candidate, recorded on `proposal.capability` as provenance
#: for the acceptance rate. A rate of 40% means something different when one generator
#: produced every rejection.
FROM_DRIFT = "G1"
FROM_UNENFORCED = "G2"
FROM_UNCOVERED = "G3"
FROM_EASY_BAR = "G4"

#: EC-12. How long a bar has to have been comfortably cleared before clearing it is worth
#: a proposal, and by what margin. Both are deliberately conservative: a bar cleared by a
#: hair is not an easy bar, and a bar cleared for three runs is not a trend. Named here
#: rather than inlined because they are the kind of number this product exists to find in
#: other people's specifications, and a reader must be able to see and argue with them.
EASY_BAR_MIN_RUNS = 10
EASY_BAR_MARGIN = 0.10


@dataclass
class GeneratorRun:
    """What one run produced, including what it refused and what it declined to propose.

    `declined` is not an error list. It is the findings a generator read and deliberately
    produced nothing for, with the reason — so "the Layer saw this and said nothing" is
    distinguishable from "the Layer did not look", which is the distinction EC-4 and the
    `failing_cases_state` column both exist to preserve.
    """

    product: str
    proposals: list[ProposedChange] = field(default_factory=list)
    refusals: list[Refusal] = field(default_factory=list)
    declined: list[dict] = field(default_factory=list)
    cap: int = 0
    #: Candidates ranked beyond the cap. Stored, ranked and queryable; never deleted.
    beyond_cap: int = 0

    def as_dict(self) -> dict:
        return {
            "shape": "findings" if not self.proposals else "proposal",
            "product": self.product,
            "generated": len(self.proposals),
            "cap": self.cap,
            "beyond_cap": self.beyond_cap,
            "refused": [r.as_dict() for r in self.refusals],
            "declined": list(self.declined),
            "proposals": [p.as_dict() for p in self.proposals],
        }


def generate(
    session: Session, *, product: Product, actor: str = "generator"
) -> GeneratorRun | Refusal:
    """Every proposal the record justifies for one product, ranked, capped and stored.

    B3 rule 8 first: nothing is proposed for a product that is not `live` with a confirmed
    binding, and a fresh install says so rather than generating suggestions about a
    product that does not exist (AC-17, AC-20). The check is here as well as inside
    `_create` so that the refusal names the product once rather than once per candidate.
    """
    try:
        state.require_live(session, product=product)
    except NotLive as exc:
        return Refusal(
            reason=str(exc), missing=["a confirmed binding on this product"]
        )

    run = GeneratorRun(product=product.key, cap=product.harvest_cap)
    candidates: list[_Candidate] = []
    for build in (_from_drift, _from_unenforced, _from_uncovered, _from_easy_bars):
        candidates.extend(build(session, product, run))

    # Ranked before anything is written, so the cap applies to the whole run rather than
    # to whichever generator happened to go first.
    candidates.sort(key=lambda c: (-c.score, c.target, c.field))
    for position, candidate in enumerate(candidates, start=1):
        result = writes.propose(
            session,
            product=product,
            kind=candidate.kind,
            target=candidate.target,
            field=candidate.field,
            old_value=candidate.old_value,
            new_value=candidate.new_value,
            reason=candidate.reason,
            evidence=candidate.evidence,
            actor=actor,
            if_rejected=candidate.if_rejected,
            target_version=candidate.target_version,
            rank=position,
            rank_signals=candidate.signals,
            payload=candidate.payload,
            capability=candidate.capability,
        )
        if isinstance(result, Refusal):
            run.refusals.append(result)
            continue
        if position <= run.cap:
            run.proposals.append(result)
        else:
            run.beyond_cap += 1
    return run


@dataclass
class _Candidate:
    kind: str
    capability: str
    target: str
    field: str
    new_value: str | None
    reason: str
    evidence: list[str]
    if_rejected: str
    score: float
    signals: dict
    old_value: str | None = None
    target_version: int | None = None
    payload: dict | None = None


# -- G1, drift ---------------------------------------------------------------------


def _from_drift(session: Session, product: Product, run: GeneratorRun) -> list[_Candidate]:
    """US-2: a threshold change **or** a ticket, never both in one proposal.

    **The rule, written down so it is not left to an implementer's taste.** The two are
    opposite claims about who is wrong — the specification or the product — and US-2
    forbids hedging by proposing both and letting the human pick. Three cases, because
    the first draft of this had two and produced a sentence that was false:

    * **No run ever cleared the bar, and the clause is not `ratified`.** The bar was
      stated rather than derived, and H9 is that a threshold nobody measured must never
      read as a met or missed bar. The proposal is a `clause_change` to what the record
      actually shows.
    * **No run ever cleared it and the clause *is* `ratified`.** Somebody signed this
      promise off, so the Layer does not propose lowering it on its own initiative. The
      product has never met a ratified promise, which is work. The proposal is a `ticket`.
    * **The history cleared it before and is breaching now.** The bar is demonstrably
      reachable. The proposal is a `ticket`.

    **The branch and the sentence have to agree**, which is the defect that made this
    three cases. Keyed off `clause.state` alone, a `measured` bar that no run ever
    cleared took the ticket branch and asserted "the history clears the bar elsewhere" —
    of a clause missed in 3 of 3 runs. The question is what the record shows, not what
    the clause's state column says, so `ever_cleared` decides and each branch states only
    what its own condition established.

    None of them states a cause. "First breached at this run, and these revisions differ"
    is the ceiling (B3 rule 6).
    """
    out: list[_Candidate] = []
    found = queries.find_drift(session, product=product)
    if found.refusal is not None:
        run.refusals.append(found.refusal)
        return out

    for finding in found:
        clause = _active(session, product, finding.clause_ref)
        if clause is None:
            continue
        detail = finding.detail
        missed, total = detail["runs_missed"], detail["runs_total"]
        ever_cleared = missed < total
        # The record decides, not the state column. A `measured` bar can be one that no
        # run has ever reached: B2 defines `measured` as "a baseline run exists", which
        # says a measurement happened, not that it cleared the bar.
        unjustified_bar = not ever_cleared and clause.state != "ratified"

        signals = {
            # Every one of these is a count or a comparison a reader can check against
            # the finding. None is a judgement of impact (AC-35).
            "runs_missed": missed,
            "runs_total": total,
            "share_missed": round(missed / total, 3) if total else None,
            "still_breaching": finding.current,
            "clause_state": clause.state,
            "ever_cleared": ever_cleared,
            "stale_evidence": finding.stale,
        }
        # A breach across more of the history ranks above one across less of it; one that
        # is still breaching above one that has stopped; and a ratified bar above a
        # provisional one, because a promise someone signed off matters more than a
        # placeholder. Stale evidence sinks: it should be refreshed before it is decided.
        score = (missed / total if total else 0) * 100
        score += 30 if finding.current else 0
        score += 20 if clause.state == "ratified" else 0
        score -= 40 if finding.stale else 0

        if unjustified_bar:
            out.append(_Candidate(
                kind="clause_change",
                capability=FROM_DRIFT,
                target=f"clause:{clause.ref}",
                field="value",
                old_value=None if clause.value is None else str(clause.value),
                new_value=_plain(detail["worst"]),
                reason=(
                    f"{clause.ref} states {detail['stated']} and no run on record has "
                    f"reached it: {missed} of {total} runs are below, the furthest at "
                    f"{_plain(detail['worst'])} in run {detail['worst_run']}. The clause "
                    f"is {clause.state} and not ratified, so the bar was stated rather "
                    f"than agreed against the record."
                ),
                evidence=finding.evidence[:8],
                if_rejected=(
                    "the bar stands as stated, so every run continues to be measured "
                    "against a number no run on record has met."
                ),
                score=score + 10,
                signals=signals | {"rule": "bar_never_cleared"},
                target_version=clause.version,
            ))
        else:
            out.append(_Candidate(
                kind="ticket",
                capability=FROM_DRIFT,
                target=f"clause:{clause.ref}",
                field="ticket",
                new_value=f"{clause.ref} is below its stated bar",
                reason=(
                    f"{clause.ref} states {detail['stated']} and is missed in {missed} of "
                    f"{total} runs, worst {_plain(detail['worst'])} in run "
                    f"{detail['worst_run']}, first in run {detail['first_breaching_run']}. "
                    + (
                        "The history clears the bar in other runs, so the bar is "
                        "reachable."
                        if ever_cleared else
                        "No run on record has reached it, and the clause is ratified, so "
                        "the bar is not the Layer's to propose lowering."
                    )
                ),
                evidence=finding.evidence[:8],
                if_rejected=(
                    "no work is raised and the clause keeps its stated bar, so the gap "
                    "between the promise and the runs stays open and unassigned."
                ),
                score=score,
                signals=signals | {
                    "rule": "bar_was_met_before" if ever_cleared
                    else "ratified_bar_never_cleared"
                },
                target_version=clause.version,
            ))
    return out


# -- G2, unenforced ----------------------------------------------------------------


def _from_unenforced(
    session: Session, product: Product, run: GeneratorRun
) -> list[_Candidate]:
    """US-3 and US-12: a `ci_change`, and for H14 the change is the run set.

    H14 is the case worth being careful about. A gate with `enforced: true` and
    `scope: latest_only` checks the right metric at the right value and still lets a
    mid-history breach through, so proposing a *threshold* change there would be
    proposing to fix a number that is already correct. The change is which runs the gate
    reads.

    **Nothing is proposed when the scan read no gate at all**, and this is the second
    defect running the generators against a real product found. `find_unenforced` is
    right to report a clause as unenforced when no fact covers it — unenforced in effect
    is the finding. But a *proposal* to add a gate asserts that no gate exists, and where
    the scan stored no facts for the product there are two possibilities the Layer cannot
    tell apart from this side: no code source was ever bound, or one was and the scan
    could not read a bar in it. Proposing against either is proposing from an absence of
    evidence, which is the same mistake `scope: undetermined` and `failing_cases_state`
    both exist to prevent. So it is declined, and the reason says which of the two it is.
    """
    out: list[_Candidate] = []
    found = queries.find_unenforced(session, product=product)
    if found.refusal is not None:
        run.refusals.append(found.refusal)
        return out

    read_a_gate = any(f.detail["ci_files"] for f in found)
    #: H14 candidates are grouped by the file whose run set would change. Two clauses
    #: checked by one gate need **one** change to one file, and B4 item 1 is one change
    #: to one field — emitting it per clause produced a duplicate the index then refused,
    #: which is how this came to light.
    narrowed_by_file: dict[str, list] = {}

    for finding in found:
        detail = finding.detail
        if not read_a_gate:
            run.declined.append({
                "finding": UNENFORCED,
                "clause_ref": finding.clause_ref,
                "reason": _why_no_gate_read(session, product),
            })
            continue
        enforced, scope = detail["enforced"], detail["scope"]
        signals = {
            "enforced": enforced,
            "scope": scope,
            "partial": detail["partial"],
            "has_ci_file": bool(detail["file"]),
            "ci_files_scanned": len(detail["ci_files"]),
        }
        if enforced and scope in ("latest_only", "latest_shipped"):
            # H14. The threshold is right; the run set is not. Collected and emitted once
            # per file below.
            narrowed_by_file.setdefault(detail["file"], []).append((finding, detail))
        elif scope == "undetermined":
            # The scan could not tell what the gate reads. Proposing a change to it would
            # be proposing against a guess, so this is declined with the reason rather
            # than turned into a confident-looking proposal.
            run.declined.append({
                "finding": UNENFORCED,
                "clause_ref": finding.clause_ref,
                "reason": (
                    f"the enforcement scan could not determine which runs "
                    f"{detail['file']} checks, so there is nothing here to propose a "
                    f"change to. Reported as a finding instead."
                ),
            })
        elif not enforced:
            out.append(_Candidate(
                kind="ci_change",
                capability=FROM_UNENFORCED,
                target=f"clause:{finding.clause_ref}",
                field="enforced",
                old_value="false",
                new_value="true",
                reason=(
                    f"{finding.clause_ref} states {detail['stated']} and no file in the "
                    f"code source checks {detail['metric']}: "
                    f"{len(detail['ci_files'])} CI file(s) were scanned."
                ),
                evidence=finding.evidence[:8],
                if_rejected=(
                    "the clause stays unenforced, so nothing fails a build when the bar "
                    "is missed and the promise is kept by convention alone."
                ),
                score=60,
                signals=signals | {"rule": "no_gate_at_all"},
            ))

    for path, group in sorted(narrowed_by_file.items()):
        refs = sorted({f.clause_ref for f, _ in group if f.clause_ref})
        first, detail = group[0]
        scope = detail["scope"]
        checks = ", ".join(
            sorted({f"{d['metric']} at {d['threshold']}" for _, d in group})
        )
        out.append(_Candidate(
            kind="ci_change",
            capability=FROM_UNENFORCED,
            target=f"file:{path}",
            field="scope",
            old_value=scope,
            new_value="all_runs",
            reason=(
                f"{path} checks {checks} against {scope.replace('_', ' ')}, so a run "
                f"that missed the bar earlier in the sequence never fails a build. "
                f"{'; '.join(refs)} state bars it is meant to hold."
            ),
            # One file's worth of evidence, deduplicated across the clauses it covers.
            evidence=_dedupe(ref for f, _ in group for ref in f.evidence)[:8],
            if_rejected=(
                "the gate keeps reading one run, and a breach anywhere but the newest "
                "run stays invisible to CI."
            ),
            # Ranked above a wholly unchecked clause: a gate that reports success while a
            # breach sits in history is worse than one that reports nothing, because
            # somebody is relying on it.
            score=90,
            signals={
                "enforced": True,
                "scope": scope,
                "clauses_covered": refs,
                "metrics_checked": sorted({d["metric"] for _, d in group}),
                "rule": "narrowed_scope",
            },
        ))
    return out


def _dedupe(refs) -> list[str]:
    """Order-preserving, because the first citation on a finding is its subject."""
    seen: set[str] = set()
    out: list[str] = []
    for ref in refs:
        if ref not in seen:
            seen.add(ref)
            out.append(ref)
    return out


def _why_no_gate_read(session: Session, product: Product) -> str:
    """Which of the two it is: no code source bound, or one bound that read nothing.

    They ask different things of the reader — bind a source, or go and look at why the
    gate could not be read — so collapsing them into one message sends half of them to
    the wrong place.
    """
    from layer.db.models import Source

    bound = session.execute(
        select(Source).where(Source.product_id == product.id, Source.role == "code")
    ).scalars().first()
    if bound is None:
        return (
            "no code source is bound to this product, so nothing has looked at its CI. "
            "A proposal to add a gate would be asserting that no gate exists on evidence "
            "nobody gathered. Bind a code source and re-run the scan."
        )
    return (
        "a code source is bound and the enforcement scan stored no fact for it, so the "
        "Layer has read no gate on this product and cannot assert that none exists. "
        "Reported as a finding; the scan's own notes say which files it could not read a "
        "bar in."
    )


# -- G3, uncovered -----------------------------------------------------------------


def _from_uncovered(
    session: Session, product: Product, run: GeneratorRun
) -> list[_Candidate]:
    """H16's direction only: a metric measured that no clause promises.

    The other reasons are declined with their reasons — see `_why_not_no_assertion`.
    """
    out: list[_Candidate] = []
    found = queries.find_uncovered(session, product=product)
    if found.refusal is not None:
        run.refusals.append(found.refusal)
        return out

    for finding in found:
        reason = finding.detail.get("reason")
        if reason != METRIC_WITHOUT_CLAUSE:
            run.declined.append({
                "finding": UNCOVERED,
                "clause_ref": finding.clause_ref,
                "detail": reason,
                "reason": _why_not_no_assertion(reason),
            })
            continue

        metric = finding.detail["metric"]
        latest = finding.detail["latest"]
        runs = finding.detail["runs"]
        out.append(_Candidate(
            kind="new_clause",
            capability=FROM_UNCOVERED,
            target=f"clause:{_ref_for(product, metric)}",
            field="statement",
            new_value=f"{metric} is measured and no clause states a bar for it",
            reason=(
                f"{metric} is measured across {runs} run(s), latest {_plain(latest)}, and "
                f"no clause states a bar for it. H16: the gap is the absent clause rather "
                f"than the measurement."
            ),
            evidence=finding.evidence[:8],
            if_rejected=(
                "the metric keeps being measured with nothing to measure it against, so "
                "no movement in it can ever be a breach."
            ),
            # Below drift and H14. A missing clause is a gap in the specification, not a
            # promise currently being broken, and more runs of history makes it a better
            # candidate rather than a more urgent one.
            score=40 + min(runs, 20),
            signals={
                "runs_observed": runs,
                "latest_value": latest,
                "has_clause": False,
                "rule": "metric_without_clause",
            },
            payload={
                # What `_apply_new_clause` builds the row from. No bar is proposed: the
                # Layer does not know what this metric ought to be, and inventing one
                # would be setting a threshold automatically, which B10 rules out.
                "kind": "threshold",
                "metric": metric,
                "statement": (
                    f"{metric} is measured by the eval source and this clause states its "
                    f"bar. The bar is not set by the Layer: latest observed "
                    f"{_plain(latest)} across {runs} run(s)."
                ),
            },
        ))
    return out


def _why_not_no_assertion(reason: str | None) -> str:
    """Why `no_assertion`, `no_metric` and `not_measured_recently` produce nothing.

    The plan for this step said a `no_assertion` finding justifies "a link where the
    clause exists but is unbound". Building it showed that it does not: to propose a link
    the Layer has to name the metric that answers the promise, and a `no_assertion`
    finding is precisely the case where no name matched. Onboarding step 5's own matcher
    already proposes every pairing that *is* knowable by name, so a generator here would
    either repeat step 5 or guess — and a guessed binding is how a wrong number becomes
    the definition of correct.
    """
    return {
        "no_assertion": (
            "nothing measures this promise, and proposing what should measure it means "
            "naming a metric. No name matched at onboarding step 5, so any pairing "
            "proposed here would be a guess at which number answers the promise. "
            "Reported as a finding."
        ),
        "no_metric": (
            "the clause is bound to a metric no run carries a value for. The gap is in "
            "the runs rather than in the specification, and the Layer cannot propose a "
            "measurement into existence. Reported as a finding."
        ),
        "not_measured_recently": (
            "the measurement is stale. A proposal decided on stale evidence is the thing "
            "B5 item 10 ranks worst, so the source is refreshed before this is decided "
            "rather than after. Reported as a finding."
        ),
    }.get(reason or "", "no proposal rule covers this finding; reported as a finding.")


# -- G4, EC-12, the bar that stopped being a constraint -----------------------------


def _from_easy_bars(
    session: Session, product: Product, run: GeneratorRun
) -> list[_Candidate]:
    """EC-12: "a metric has comfortably exceeded its threshold for months".

    The required behaviour is to report it as a candidate for raising the bar, because a
    permanently easy threshold measures nothing — and EC-12 notes this is the one teams
    never build, which is how a specification quietly stops being a constraint.

    There is no drift finding here to read: nothing is breaching. So this generator works
    from the observation series directly, with both of its numbers named and conservative
    (`EASY_BAR_MIN_RUNS`, `EASY_BAR_MARGIN`). A bar cleared by a hair is not an easy bar,
    and a bar cleared for three runs is not a trend.
    """
    out: list[_Candidate] = []
    bound = binding_gate.confirmed_metrics(session, product=product)
    for metric, clause_ref in bound.items():
        clause = _active(session, product, clause_ref)
        if clause is None or clause.value is None or clause.kind != "threshold":
            continue
        # Only a one-sided bar can be "comfortably exceeded". A band has two edges and
        # clearing one of them by a margin means approaching the other.
        if clause.value_high is not None or clause.direction == "within_band":
            continue
        rows = queries.series_for(session, product.id, metric)
        if len(rows) < EASY_BAR_MIN_RUNS:
            continue

        margin = _margin(clause, [r.value for r in rows])
        if margin is None or margin < EASY_BAR_MARGIN:
            continue

        worst = (min if clause.direction != "lower_is_better" else max)(
            r.value for r in rows
        )
        out.append(_Candidate(
            kind="clause_change",
            capability=FROM_EASY_BAR,
            target=f"clause:{clause.ref}",
            field="value",
            old_value=str(clause.value),
            new_value=_plain(worst),
            reason=(
                f"{clause.ref} states {queries.bar_text(clause)} and every one of "
                f"{len(rows)} runs clears it by at least "
                f"{margin * 100:.0f}% of the bar, the closest at {_plain(worst)}. A bar "
                f"nothing has approached is not measuring anything."
            ),
            evidence=[f"clause:{clause.ref}", f"obs:{rows[-1].id}"],
            if_rejected=(
                "the bar stays where it is and keeps passing without constraining "
                "anything, which is how a specification stops being one."
            ),
            # The lowest of the four. Nothing is broken; this is an improvement to the
            # specification, and it should not outrank a promise being missed today.
            score=20 + min(len(rows), 20),
            signals={
                "runs_observed": len(rows),
                "closest_approach": worst,
                "margin_over_bar": round(margin, 3),
                "min_runs_required": EASY_BAR_MIN_RUNS,
                "min_margin_required": EASY_BAR_MARGIN,
                "rule": "bar_never_approached",
            },
            target_version=clause.version,
        ))
    return out


def _margin(clause: Clause, values: list[float]) -> float | None:
    """How far the closest run sits from the bar, as a share of the bar.

    A share rather than an absolute difference, because the bar may be a ratio, a count,
    a duration in milliseconds or a currency amount, and 0.1 means something different in
    each. A bar of zero has no meaningful share and returns None rather than dividing.
    """
    if not values or not clause.value:
        return None
    if clause.direction == "lower_is_better":
        closest = max(values)
        return (clause.value - closest) / abs(clause.value)
    closest = min(values)
    return (closest - clause.value) / abs(clause.value)


# -- helpers -----------------------------------------------------------------------


def _active(session: Session, product: Product, ref: str | None) -> Clause | None:
    if not ref:
        return None
    return session.execute(
        select(Clause).where(
            Clause.product_id == product.id, Clause.ref == ref,
            Clause.status == "active",
        )
    ).scalars().first()


def _ref_for(product: Product, metric: str) -> str:
    """A ref for a clause that does not exist yet.

    Built from the product's own `ref_prefix` and the metric's name, so no ref format is
    assumed and no product's naming convention appears in this file (R1).
    """
    prefix = product.ref_prefix or product.key[:4].upper()
    slug = "".join(ch if ch.isalnum() else "-" for ch in metric).strip("-").upper()
    return f"{prefix}-NEW-{slug}"[:64]


def _plain(value: float | None) -> str:
    """A number as a human would write it, so a proposal's `new_value` round-trips."""
    if value is None:
        return ""
    text = f"{value:.4f}".rstrip("0").rstrip(".")
    return text or "0"
