"""Resolving refs to URLs a human can open.

A resolver turns a `Ref` into an immutable URL, or returns None when it cannot.
Returning None is a first-class outcome, not an error: PRD B1 requires every
finding to carry `unresolved[]`, "empty is the required state; a non-empty list is
displayed, never hidden". So resolution never raises on a ref it cannot place and
never silently drops one.

AC-14 is enforced here rather than in each resolver: every URL a resolver returns
is checked for an immutable revision before it is handed back. A resolver that
builds a branch URL fails the check and its ref lands in `unresolved`, which is
visible, rather than producing a citation that silently rots.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from layer.refs.ref import MalformedRef, Ref, parse

#: Given a ref, a URL or None. Resolvers are registered per kind and are free to
#: close over whatever they need (a repo handle, a session, a source row).
Resolver = Callable[[Ref], str | None]


class Registry:
    """Kind -> resolver. One instance per unit of work, not a global.

    It is deliberately not a module-level singleton: resolvers close over a tenant's
    sources, and a process-wide registry would be a way for one tenant's repository
    URL to answer another tenant's ref.
    """

    def __init__(self) -> None:
        self._resolvers: dict[str, Resolver] = {}

    def register(self, kind: str, resolver: Resolver) -> None:
        self._resolvers[kind] = resolver

    def register_all(self, resolvers: dict[str, Resolver]) -> None:
        self._resolvers.update(resolvers)

    @property
    def kinds(self) -> frozenset[str]:
        return frozenset(self._resolvers)

    def resolve(self, ref: Ref | str) -> str | None:
        """The URL for one ref, or None when no resolver is registered for its kind,
        the resolver declines, or the result is not pinned to an immutable revision."""
        if isinstance(ref, str):
            try:
                ref = parse(ref)
            except MalformedRef:
                return None
        resolver = self._resolvers.get(ref.kind)
        if resolver is None:
            return None
        url = resolver(ref)
        if url is None:
            return None
        return url if is_pinned(url) else None

    def links(self, refs: Iterable[Ref | str]) -> tuple[list[dict], list[str]]:
        """`evidence` -> `(evidence_links, unresolved)`, in the finding's own shape.

        Order is preserved and duplicates are kept, because a finding's evidence list
        is evidence: rewriting it to look tidier would misreport what was cited.
        """
        links: list[dict] = []
        unresolved: list[str] = []
        for ref in refs:
            as_text = str(ref)
            url = self.resolve(ref)
            if url is None:
                unresolved.append(as_text)
            else:
                links.append({"id": as_text, "url": url})
        return links, unresolved


#: Revision-shaped fragments that are not immutable. A branch or `HEAD` moves, so a
#: citation built on one becomes wrong the moment someone pushes, and the reader
#: cannot tell (PRD B2, "citations pin a commit").
_MOVING = ("/blob/main/", "/blob/master/", "/blob/head/", "/blob/develop/",
           "/raw/main/", "/raw/master/", "/tree/main/", "/tree/master/")


def is_pinned(url: str) -> bool:
    """True when the URL does not obviously name a moving target.

    A deliberately conservative check on the URL rather than a clever one: the
    authoritative guarantee comes from `layer.adapters.repo`, which refuses to pin a
    source to anything but a commit sha in the first place. This is the second line,
    so that a resolver added later cannot reintroduce a branch citation unnoticed.
    """
    lowered = url.lower()
    return not any(moving in lowered for moving in _MOVING)
