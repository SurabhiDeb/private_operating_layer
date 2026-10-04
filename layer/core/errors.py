"""The Layer's refusal vocabulary.

PRD B1: every response is an Answer, a Proposal, a Finding or a Refusal, and
"any response that cannot be parsed into one of these four defaults to a refusal
and records the problem". So a refusal is a first-class return value, not an
exception that leaks a stack trace to a caller.
"""

from dataclasses import dataclass, field


class LayerError(Exception):
    """Base for every error the Layer raises deliberately."""


class TenantContextMissing(LayerError):
    """No org was established before touching tenant data.

    Raised rather than silently reading nothing, because a query that quietly
    returns zero rows is indistinguishable from a tenant with no data.
    """


class UnknownProduct(LayerError):
    """B3 rule 9: a record whose product does not resolve is rejected at write
    time, not cleaned up later."""


class NotLive(LayerError):
    """B3 rule 8: nothing is proposed for a product without a confirmed binding."""


class Unreadable(LayerError):
    """An operator wrote something the Layer will not guess at.

    Separate from a Python `ValueError` on purpose: this is a refusal the operator is
    meant to read and correct, so it reaches them as `refused: ...` on stderr rather
    than as a traceback, while a `ValueError` escaping remains what it should be — a
    defect.
    """


@dataclass(frozen=True)
class Refusal:
    """A stated refusal, naming what is missing. Never an empty result."""

    shape: str = field(default="refusal", init=False)
    reason: str = ""
    missing: list[str] = field(default_factory=list)

    def as_dict(self) -> dict:
        return {"shape": self.shape, "reason": self.reason, "missing": list(self.missing)}
