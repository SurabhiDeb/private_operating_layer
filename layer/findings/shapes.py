"""The Finding, exactly as PRD B1 defines it.

A finding is "a detected condition needing no change, only attention". It is not a
proposal and not an answer, and keeping those apart matters: a finding can be produced for
a product that has not passed the binding gate, where a proposal cannot.

Three fields carry most of the discipline.

`evidence` holds refs, `evidence_links` holds the URLs they resolved to, and `unresolved`
holds the ones that did not. B1: "Empty is the required state; a non-empty list is
displayed, never hidden." So a finding whose citation broke says so on its own face rather
than in an aggregate nobody reads.

`summary` states facts and no cause. B3 rule 6 permits "X first failed at v3 and the prompt
sha changed at v3" and forbids "the prompt change caused it". Every summary below is built
from numbers and timestamps, and a test greps the generated text for causal verbs.

`current` separates "this is happening" from "this happened". A breach that no longer holds
is still a finding — it is how condition 1 works, where the newest run is clean — but a
reader must be able to tell the two apart at a glance.

`as_of` and `stale` are the fourth. A finding resting on a measurement outside its source's
window is shown with its age, never silently as current (B1, B3 rule 12), and AC-29 requires
that age in the summary's own words rather than only in a field — so `stale` is a flag for a
caller to branch on and never the only place the staleness appears.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

from layer.core.errors import Refusal

DRIFT = "drift"
UNENFORCED = "unenforced"
UNCOVERED = "uncovered"
STALLED_DECISION = "stalled_decision"
UNDERSPECIFIED = "underspecified"

#: PRD B1's `uncovered` reason vocabulary.
NO_ASSERTION = "no_assertion"
NO_METRIC = "no_metric"
METRIC_WITHOUT_CLAUSE = "metric_without_clause"
NOT_MEASURED_RECENTLY = "not_measured_recently"


@dataclass
class Finding:
    kind: str
    product: str
    summary: str
    clause_ref: str | None = None
    first_seen: datetime | None = None
    current: bool = True
    #: The `measured_at` of the newest observation this rests on. None where the finding
    #: rests on no measurement at all — an unenforced clause is about CI, not about a run.
    as_of: datetime | None = None
    #: True where `as_of` falls outside the source's freshness window.
    stale: bool = False
    evidence: list[str] = field(default_factory=list)
    evidence_links: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    detail: dict = field(default_factory=dict)
    shape: str = "finding"

    def resolve(self, registry) -> Finding:
        """Attach URLs for the evidence, and name what could not be resolved."""
        self.evidence_links, self.unresolved = registry.links(self.evidence)
        return self

    def as_dict(self) -> dict:
        return {
            "shape": self.shape,
            "kind": self.kind,
            "clause_ref": self.clause_ref,
            "product": self.product,
            "first_seen": self.first_seen.isoformat() if self.first_seen else None,
            "current": self.current,
            "as_of": self.as_of.isoformat() if self.as_of else None,
            "stale": self.stale,
            "summary": self.summary,
            "evidence": list(self.evidence),
            "evidence_links": list(self.evidence_links),
            "unresolved": list(self.unresolved),
            "detail": dict(self.detail),
        }


@dataclass
class FindingSet:
    """What one query returns: findings, or a refusal naming what is missing.

    A refusal rather than an empty list, because PRD B1 requires that the Layer "never
    silently returns nothing" and AC-20 requires a bare install to say what it lacks. An
    empty list from a query that cannot run reads as "all clear", which is the most
    expensive wrong answer available here.
    """

    kind: str
    findings: list[Finding] = field(default_factory=list)
    refusal: Refusal | None = None
    #: PRD B1's `stale_sources`: "empty is the required state. A non-empty list caps
    #: confidence at medium and SHALL appear in `caveats` in words, not only as a field".
    #: Attached to every set a query returns, including an empty one — a product whose
    #: sources have all gone quiet has nothing to report and that is the thing to report.
    stale_sources: list[dict] = field(default_factory=list)

    def __len__(self) -> int:
        return len(self.findings)

    def __iter__(self):
        return iter(self.findings)

    @property
    def stale(self) -> bool:
        return bool(self.stale_sources)

    def as_dict(self) -> dict:
        if self.refusal is not None:
            return self.refusal.as_dict() | {
                "kind": self.kind, "stale_sources": list(self.stale_sources)
            }
        return {
            "shape": "findings",
            "kind": self.kind,
            "count": len(self.findings),
            "findings": [f.as_dict() for f in self.findings],
            "stale_sources": list(self.stale_sources),
        }
