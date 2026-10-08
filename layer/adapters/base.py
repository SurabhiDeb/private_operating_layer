"""What an adapter produces, and how it reports what it could not do.

An adapter reads one source and emits candidates. It never writes: persistence,
identity resolution and the onboarding gates are the caller's job, so an adapter can be
tested against a document with no database at all.

**The report is as important as the candidates.** PRD EC-2 requires a spec that cannot be
parsed to "say which part failed and import the rest", and EC-4 requires a partial result
to be "clearly marked, naming what is missing. Never a silent partial". So every adapter
returns an `ImportReport` whose counts must add up: everything enumerated is either a
candidate, a deliberate skip, or a failure. Nothing is dropped.

That arithmetic is what AC-2 rests on. Reconciling stored observations against source
runs only proves anything if "this file is not a run" and "this run would not import"
are counted separately — otherwise a quiet skip makes 47 of 47 look like success.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol

from layer.metrics.engine import PASS, CaseOutcome


@dataclass(frozen=True)
class SourceDocument:
    """One thing read from a source, with where a human can open it.

    The URL is supplied by the caller rather than built here, because only the caller
    knows the source's pinned revision. That keeps an adapter free of any knowledge about
    repositories or hosts while still letting every observation carry a citation that
    names an immutable revision (AC-14).
    """

    identifier: str
    content: bytes | str
    url: str | None = None


@dataclass(frozen=True)
class Skipped:
    """Something enumerated and deliberately not turned into a candidate."""

    identifier: str
    reason: str
    note: str | None = None


@dataclass(frozen=True)
class Failed:
    """Something that should have produced a candidate and did not."""

    identifier: str
    reason: str
    note: str | None = None


@dataclass
class ImportReport:
    """The outcome of reading one source, with the arithmetic kept honest."""

    source_role: str
    candidates: list[Any] = field(default_factory=list)
    skipped: list[Skipped] = field(default_factory=list)
    failed: list[Failed] = field(default_factory=list)
    #: How many things were enumerated before any were interpreted. A unit of
    #: enumeration is whatever the adapter walks — a section, a run file — not a
    #: candidate, since one unit routinely yields several candidates or none.
    enumerated: int = 0
    #: Units that yielded at least one candidate.
    imported: int = 0
    #: Metrics that could not be computed, recorded per unit. Deliberately outside the
    #: arithmetic above: the unit of enumeration is a document, and a document whose
    #: third metric is uncomputable is still imported. Kept so onboarding can say "this
    #: metric was unmeasurable in 47 of 47 runs" rather than leaving a clause quietly
    #: unmeasured, which is the `uncovered / no_metric` finding's input.
    unmeasured: list[Skipped] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def accounted(self) -> int:
        return self.imported + len(self.skipped) + len(self.failed)

    @property
    def complete(self) -> bool:
        """True when nothing went missing between enumeration and interpretation."""
        return self.enumerated == self.accounted

    @property
    def partial(self) -> bool:
        return bool(self.failed)

    @property
    def cases(self) -> int:
        """Case rows carried by the candidates, which is what the evidence spine stores.

        Deliberately outside `accounted`, like `unmeasured` and for the same reason: the
        unit of enumeration is a document, and cases are a layer beneath a candidate
        rather than a second kind of enumerated thing. Counted so a backfill can say how
        much proof it loaded instead of only how many numbers.
        """
        return sum(len(c.cases) for c in self.candidates if hasattr(c, "cases"))

    @property
    def cases_disagreeing(self) -> list[str]:
        """Candidates whose cases do not reproduce their own `passed` and `total`.

        An empty list is the required state. A non-empty one is a defect in whichever
        adapter produced it and is reported in `summary()` rather than being left to
        surface later as a finding citing evidence that does not add up.
        """
        return [
            c.case_key
            for c in self.candidates
            if hasattr(c, "cases_reproduce_the_value") and not c.cases_reproduce_the_value
        ]

    def summary(self) -> str:
        parts = [
            f"{len(self.candidates)} candidate(s) from {self.imported} imported",
            f"{len(self.skipped)} skipped",
            f"{len(self.failed)} failed",
            f"of {self.enumerated} enumerated",
        ]
        line = ", ".join(parts)
        if self.cases:
            line += f", {self.cases} case row(s)"
        disagreeing = self.cases_disagreeing
        if disagreeing:
            # Loud for the same reason as an unbalanced report. Cases that do not
            # reproduce their aggregate are not evidence, so they are named here and
            # refused at the write rather than stored and cited.
            line += (
                f"  [CASES DISAGREE with their own passed/total: "
                f"{', '.join(disagreeing[:5])}. Not stored.]"
            )
        if not self.complete:
            # Loud on purpose. An unbalanced report means something was dropped on the
            # floor, which is the one outcome EC-4 forbids outright.
            line += (
                f"  [UNACCOUNTED: {self.enumerated - self.accounted}. "
                f"Every enumerated item must be a candidate, a skip or a failure.]"
            )
        return line


@dataclass(frozen=True)
class ClauseCandidate:
    """A promise read out of a source, before identity is resolved.

    `ref_hint` is a suggestion. The real ref comes from `clause_identity`, so that a
    reworded statement keeps the ref it already had (AC-1, H3). `identity_key` is what
    that lookup uses.
    """

    identity_key: str
    statement: str
    kind: str = "threshold"
    ref_hint: str | None = None
    section: str | None = None
    label: str | None = None
    rationale: str | None = None
    metric: str | None = None
    comparator: str | None = None
    value: float | None = None
    value_high: float | None = None
    unit: str | None = None
    direction: str | None = None
    k: int | None = None
    source_locator: str | None = None
    #: Readings inferred rather than stated, shown to whoever confirms the clause.
    assumptions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ObservationCandidate:
    """One measured number from one run, ready to be stored."""

    metric: str
    value: float
    measured_at: datetime
    source_kind: str = "eval"
    clause_ref: str | None = None
    passed: int | None = None
    total: int | None = None
    unit: str | None = None
    prompt_version: str | None = None
    corpus_sha: str | None = None
    code_rev: str | None = None
    run_id: str | None = None
    run_url: str | None = None
    detail: dict = field(default_factory=dict)
    #: The per-case layer beneath this number, where the aggregation has one. Empty is a
    #: correct and common state: a tier 1 metric is a number the source already counted,
    #: with no rows behind it that this Layer ever saw.
    cases: tuple[CaseOutcome, ...] = ()

    @property
    def counted_cases(self) -> tuple[CaseOutcome, ...]:
        """The cases inside the metric's denominator. See `CaseOutcome.counted`."""
        return tuple(case for case in self.cases if case.counted)

    @property
    def cases_reproduce_the_value(self) -> bool:
        """Whether the cases add up to the number they are the evidence for.

        The load-bearing property of the whole evidence spine. If `passed` and `total`
        could disagree with the stored outcomes, a finding would cite cases that do not
        add up to its own claim, which is worse than citing none — so this is checked
        before the rows are written and a disagreement is reported rather than stored.

        Vacuously true with no cases. A tier 1 metric has no per-case layer, and that is
        an absence of evidence, not a disagreement.
        """
        if not self.cases:
            return True
        if self.passed is None or self.total is None:
            return False
        passes = sum(1 for case in self.counted_cases if case.outcome == PASS)
        return passes == self.passed and len(self.counted_cases) == self.total

    @property
    def case_key(self) -> str:
        """How a candidate is named in a report, for a human reading the arithmetic."""
        return f"{self.metric}@{self.run_id or self.run_url or 'unknown run'}"


@dataclass(frozen=True)
class Expectation:
    """A stated bar the code adapter goes looking for.

    The scan is told what to look for rather than left to comprehend arbitrary code. That
    is the difference between a tool that works on one repository and one that works on
    any: understanding a gate script in general is a research problem, while answering "is
    0.85 compared against something called team accuracy anywhere in CI" is a search.
    """

    metric: str
    comparator: str | None = None
    value: float | None = None
    value_high: float | None = None
    unit: str | None = None
    label: str | None = None


@dataclass(frozen=True)
class EnforcementCandidate:
    """What a CI file actually checks, including the run set it checks it against."""

    metric: str
    file: str
    enforced: bool = True
    partial: bool = False
    partial_note: str | None = None
    threshold: float | None = None
    comparator: str | None = None
    scope: str = "all_runs"
    line: int | None = None
    unit: str | None = None
    workflow: str | None = None
    known_failing: bool = False


class SourceAdapter(Protocol):
    """Every adapter is `(config, bytes or documents) -> ImportReport`.

    Narrow on purpose. An adapter that could reach the database could also reach another
    tenant's rows, and one that took a `product` could branch on which product it was —
    the thing agnosticism rule R2 forbids.
    """

    role: str

    def read(self, *args: Any, **kwargs: Any) -> ImportReport: ...
