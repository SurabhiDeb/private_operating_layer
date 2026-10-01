"""Refs and resolution. PRD B1 evidence shapes, AC-14, AC-7, B5 item 3.

Nothing here touches the database or any product: a ref is a naming scheme, and if
these tests needed a product to run, the scheme would already be fitted to one.
"""

from __future__ import annotations

import pytest

from layer.refs.ref import MalformedRef, Ref, parse, parse_all
from layer.refs.registry import Registry, is_pinned


class TestParsing:
    def test_a_ref_round_trips(self):
        assert str(parse("clause:TRI-11.2")) == "clause:TRI-11.2"

    def test_an_id_may_contain_slashes_and_fragments(self):
        ref = parse("file:products/triage/gate.py#L25")
        assert (ref.kind, ref.id) == ("file", "products/triage/gate.py#L25")

    def test_splitting_happens_on_the_first_colon_only(self):
        """Ids legitimately contain colons — a permalink, a timestamped run id.
        Splitting on the last would quietly rewrite them."""
        ref = parse("decision:slack:C0POLICY:p1726918200")
        assert ref.kind == "decision"
        assert ref.id == "slack:C0POLICY:p1726918200"

    @pytest.mark.parametrize(
        "raw", ["", "nocolon", ":missingkind", "clause:", "Clause:X", "1kind:x", "a:b c"]
    )
    def test_a_malformed_ref_raises_rather_than_being_coerced(self, raw):
        """B5 ranks a fabricated ref third among unacceptable failures, so a
        half-readable one must not be quietly repaired into something plausible."""
        with pytest.raises((MalformedRef, ValueError)):
            parse(raw)

    def test_refs_are_values(self):
        assert Ref("obs", "102") == Ref("obs", "102")
        assert len({Ref("obs", "102"), Ref("obs", "102")}) == 1


class TestRegistry:
    def test_an_unregistered_kind_resolves_to_none_rather_than_raising(self):
        """B1 requires `unresolved` to be a list on the finding, so an unknown kind
        is an outcome to report, not an exception to propagate."""
        assert Registry().resolve("prod_metric:x/y") is None

    def test_links_separates_resolved_from_unresolved_and_drops_neither(self):
        registry = Registry()
        registry.register("obs", lambda ref: f"https://runs.example/{ref.id}")

        links, unresolved = registry.links(["obs:1", "ticket:NIM-112", "obs:2"])

        assert links == [
            {"id": "obs:1", "url": "https://runs.example/1"},
            {"id": "obs:2", "url": "https://runs.example/2"},
        ]
        assert unresolved == ["ticket:NIM-112"]

    def test_order_and_duplicates_are_preserved(self):
        """Evidence is evidence. Tidying the list would misreport what was cited."""
        registry = Registry()
        registry.register("obs", lambda ref: f"https://runs.example/{ref.id}")
        links, _ = registry.links(["obs:2", "obs:1", "obs:2"])
        assert [link["id"] for link in links] == ["obs:2", "obs:1", "obs:2"]

    def test_a_malformed_ref_is_reported_unresolved_not_raised(self):
        _, unresolved = Registry().links(["not-a-ref"])
        assert unresolved == ["not-a-ref"]

    @pytest.mark.ac("AC-14")
    def test_a_branch_url_is_refused_even_when_a_resolver_returns_one(self):
        """The registry is the second line. A resolver added later cannot
        reintroduce a moving citation without this failing."""
        registry = Registry()
        registry.register("file", lambda ref: f"https://host/repo/blob/main/{ref.id}")

        assert registry.resolve("file:SPEC.md") is None
        _, unresolved = registry.links(["file:SPEC.md"])
        assert unresolved == ["file:SPEC.md"]

    @pytest.mark.ac("AC-14")
    @pytest.mark.parametrize(
        "url,pinned",
        [
            ("https://h/r/blob/ef07ac9/SPEC.md", True),
            ("https://h/r/blob/ef07ac9b4daf706d1d59bdae6c8d1e8cbc6ab83f/SPEC.md", True),
            ("https://h/r/blob/main/SPEC.md", False),
            ("https://h/r/blob/master/SPEC.md", False),
            ("https://h/r/raw/main/SPEC.md", False),
            ("https://h/r/tree/master/products", False),
        ],
    )
    def test_moving_revisions_are_recognised(self, url, pinned):
        assert is_pinned(url) is pinned


def test_parse_all_is_all_or_nothing():
    with pytest.raises(MalformedRef):
        parse_all(["clause:A", "broken"])
