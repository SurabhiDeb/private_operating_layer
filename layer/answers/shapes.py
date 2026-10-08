"""The Answer, exactly as PRD B1 defines it.

The fourth shape. `Finding` and `FindingSet` live in `layer/findings/shapes.py` and
`Refusal` in `layer/core/errors.py`; this is the one a question gets back when the Layer
has something to state rather than a condition to report.

```
statement        prose, no more than five sentences
citations        one or more record ids, each resolvable to a URL
confidence       high | medium | cannot_determine
caveats          zero or more, always present when confidence is not high
stale_sources    [{...}] — empty is the required state. A non-empty list caps
                 confidence at medium and SHALL appear in `caveats` in words
```

**Two of those rules are enforced in the constructor rather than left to each caller.**
A non-empty `stale_sources` caps confidence and adds its own caveat, and lowering
confidence without saying why is refused outright. There are eighteen tools in B11 and
the rule that matters most here is the one about silence: a caller that forgot the caveat
would produce an answer which looks confident and is not, which is PRD B5 item 10 wearing
a different hat. A rule enforced in one place cannot be forgotten in seventeen others.

**The five-sentence limit is checked in the tests, not here.** A runtime guard on prose
length would turn a cosmetically long answer into a failed tool call, which is a worse
outcome than a six-sentence paragraph. `sentences()` is shared so the sweep over the
whole surface counts them the same way the writer would.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

HIGH = "high"
MEDIUM = "medium"
CANNOT_DETERMINE = "cannot_determine"
CONFIDENCES = (HIGH, MEDIUM, CANNOT_DETERMINE)

#: Sentence terminators, ignoring the decimal points in `0.85` and `90.9%` — which is
#: why this is not a split on ".".
_SENTENCE = re.compile(r"[.!?](?:\s|$)")


def sentences(text: str) -> int:
    return len(_SENTENCE.findall(text.strip()))


@dataclass
class Answer:
    """A statement, plus the records it was derived from."""

    statement: str
    citations: list[str] = field(default_factory=list)
    confidence: str = HIGH
    caveats: list[str] = field(default_factory=list)
    stale_sources: list[dict] = field(default_factory=list)
    #: Resolved from `citations` the same way a finding's evidence is, so an unresolvable
    #: id is displayed rather than dropped (B1, AC-7).
    citation_links: list[dict] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)
    #: Tool-specific payload. Deliberately beside the prose rather than inside it: a
    #: human reads `statement`, an agent reads this, and neither has to parse the other.
    detail: dict = field(default_factory=dict)
    shape: str = "answer"

    def __post_init__(self) -> None:
        if self.confidence not in CONFIDENCES:
            raise ValueError(f"not a B1 confidence: {self.confidence!r}")
        if self.stale_sources:
            # B1: "A non-empty list caps confidence at medium and SHALL appear in
            # `caveats` in words, not only as a field."
            if self.confidence == HIGH:
                self.confidence = MEDIUM
            for entry in self.stale_sources:
                note = entry.get("note")
                if note and note not in self.caveats:
                    self.caveats.append(note)
        if self.confidence != HIGH and not self.caveats:
            # A defect rather than a refusal: the answer would read as qualified while
            # saying nothing about why, and no caller is entitled to that.
            raise ValueError(
                f"confidence {self.confidence!r} with no caveat. B1 requires a caveat "
                f"whenever confidence is not high, because an answer that hedges "
                f"without saying why cannot be acted on"
            )

    def resolve(self, registry) -> Answer:
        self.citation_links, self.unresolved = registry.links(self.citations)
        return self

    def as_dict(self) -> dict:
        return {
            "shape": self.shape,
            "statement": self.statement,
            "citations": list(self.citations),
            "citation_links": list(self.citation_links),
            "unresolved": list(self.unresolved),
            "confidence": self.confidence,
            "caveats": list(self.caveats),
            "stale_sources": list(self.stale_sources),
            "detail": dict(self.detail),
        }


@dataclass
class ProposedChange:
    """B1's Proposal shape: one change to one field, awaiting a human.

    The response shape, not the row — `layer.db.models.Proposal` is the row. They are
    deliberately separate: the row carries the critic's score and the decision record,
    and a caller of a write tool needs neither to know what it just created.

    `state` is always `open` here. Nothing in this shape can express a decision, because
    nothing reachable over MCP may make one (B11's third tier, AC-31).
    """

    target: str
    field_name: str
    reason: str
    new_value: str | None = None
    old_value: str | None = None
    evidence: list[str] = field(default_factory=list)
    if_rejected: str | None = None
    confidence: float | None = None
    state: str = "open"
    proposal_id: str | None = None
    kind: str | None = None
    shape: str = "proposal"

    def as_dict(self) -> dict:
        return {
            "shape": self.shape,
            "id": self.proposal_id,
            "kind": self.kind,
            "target": self.target,
            "field": self.field_name,
            "old": self.old_value,
            "new": self.new_value,
            "reason": self.reason,
            "evidence": list(self.evidence),
            "if_rejected": self.if_rejected,
            "confidence": self.confidence,
            "state": self.state,
        }
