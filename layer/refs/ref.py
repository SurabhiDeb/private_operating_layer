"""A ref: the Layer's single way of naming a record.

Every citation anywhere in the system is a `kind:id` string — `clause:ABC-1.2`,
`obs:102`, `file:path/to/gate.py#L25`. PRD B1 fixes this shape for a
finding's `evidence`, and the prototype's output uses it throughout.

The point of a URN rather than a URL is that a ref survives the system it came
from. A URL embeds a host, a revision and a path, so storing one means storing a
guess about how that system will look later. A ref stores identity, and the URL
is derived at read time by whichever resolver is registered for that kind. That
is why adding a source system touches one resolver and nothing else.

Refs are **not globally unique**. They are scoped to an org (hard case H11), so
two tenants may both hold `clause:ABC-1.2` meaning different things. Resolution
therefore always happens inside a tenant context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: A kind is a lowercase identifier. Deliberately not an enum: the handoff's build
#: order adds a source "with a new `kind` and no new concepts", and that must not
#: require a schema change (agnosticism rule R3).
_KIND = re.compile(r"\A[a-z][a-z0-9_]*\Z")

#: Anything but whitespace. Ids carry paths, fragments and slashes — `products/x.py#L25`
#: — so they are left unparsed here and interpreted by the resolver for that kind.
_FORBIDDEN_IN_ID = re.compile(r"[\s]")


class MalformedRef(ValueError):
    """A ref that cannot be parsed. Raised rather than silently coerced, because a
    fabricated or corrupted ref is PRD B5's third unacceptable failure."""


@dataclass(frozen=True, slots=True)
class Ref:
    kind: str
    id: str

    def __post_init__(self) -> None:
        if not _KIND.match(self.kind):
            raise MalformedRef(f"not a usable ref kind: {self.kind!r}")
        if not self.id or _FORBIDDEN_IN_ID.search(self.id):
            raise MalformedRef(f"not a usable ref id: {self.id!r}")

    def __str__(self) -> str:
        return f"{self.kind}:{self.id}"


def parse(raw: str) -> Ref:
    """`"file:products/x.py#L25"` -> `Ref("file", "products/x.py#L25")`.

    Split on the first colon only. Ids legitimately contain colons (a Slack
    permalink, a timestamped run id), and splitting on the last one would quietly
    rewrite them.
    """
    if not isinstance(raw, str) or ":" not in raw:
        raise MalformedRef(f"not a ref: {raw!r}")
    kind, _, id_ = raw.partition(":")
    return Ref(kind, id_)


def parse_all(raws: list[str]) -> list[Ref]:
    return [parse(r) for r in raws]
