"""The repo source: pinning, reading at a revision, and the URLs built from it.

The mechanism tests run against a throwaway repository built in the test, not
against the fixture repository. If proving that pinning works needed a particular
product's files, pinning would already be fitted to that product.

One test at the end does use the fixture repository, because "the revision we pin
is the revision a reader opens" is only worth believing against a real history.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from layer.adapters.repo import (
    NotPinnable,
    RepoHandle,
    is_commit_sha,
    is_dirty,
    repo_resolvers,
)
from layer.refs.registry import Registry

FIXTURE_REPO = Path("/Users/surabhideb/Desktop/chatbot-lab")


def _git(path: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=True
    )
    return done.stdout.decode().strip()


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A two-commit repository with no product in it."""
    _git(tmp_path, "init", "-b", "main")
    _git(tmp_path, "config", "user.email", "test@example.invalid")
    _git(tmp_path, "config", "user.name", "Test")

    (tmp_path / "doc.md").write_text("first version\n")
    (tmp_path / "nested").mkdir()
    (tmp_path / "nested" / "thing.py").write_text("x = 1\n")
    _git(tmp_path, "add", "-A")
    _git(tmp_path, "commit", "-m", "one")
    return tmp_path


def handle_for(repo: Path, rev: str = "HEAD") -> RepoHandle:
    return RepoHandle.pin(repo, "https://host.example/org/repo", rev)


class TestPinning:
    def test_asking_for_head_stores_the_commit_it_resolved_to(self, repo):
        """The caller says 'current'; the Layer records which commit that meant."""
        handle = handle_for(repo)
        assert handle.rev == _git(repo, "rev-parse", "HEAD")
        assert is_commit_sha(handle.rev)

    def test_a_branch_name_may_be_asked_for_but_is_never_stored(self, repo):
        assert handle_for(repo, "main").rev != "main"

    @pytest.mark.ac("AC-14")
    @pytest.mark.parametrize("rev", ["main", "master", "HEAD", "v1.0", "", "ca6d836-dirty"])
    def test_constructing_a_handle_on_a_moving_revision_is_refused(self, repo, rev):
        """Guarded at construction, so no later code path can cite a branch."""
        with pytest.raises(NotPinnable):
            RepoHandle(local_path=repo, repo_url="https://host.example/r", rev=rev)

    def test_a_dirty_revision_is_recognised(self):
        """Committed eval metadata really does carry shas like this."""
        assert is_dirty("ca6d836-dirty")
        assert not is_dirty("ca6d836")


class TestReadingAtTheRevision:
    def test_reads_come_from_the_commit_not_the_working_tree(self, repo):
        """The property the whole design rests on.

        A clause imported from a spec must be the text at the revision its citation
        names. Reading the working tree would make every citation a near-miss — right
        file, different content — which looks right and is not.
        """
        handle = handle_for(repo)
        (repo / "doc.md").write_text("edited after pinning\n")

        assert handle.read("doc.md") == b"first version\n"

    def test_a_file_added_after_pinning_is_not_in_the_revision(self, repo):
        handle = handle_for(repo)
        (repo / "late.md").write_text("later\n")
        _git(repo, "add", "-A")
        _git(repo, "commit", "-m", "two")

        assert not handle.exists("late.md")
        assert handle.exists("doc.md")

    def test_a_missing_path_is_a_fact_about_the_revision_not_a_crash(self, repo):
        assert handle_for(repo).exists("nope.md") is False

    def test_listing_sees_tracked_files_at_the_revision_only(self, repo):
        handle = handle_for(repo)
        (repo / "untracked.md").write_text("ignore me\n")

        files = handle.list_files()

        assert set(files) == {"doc.md", "nested/thing.py"}
        assert handle.list_files("nested/*") == ["nested/thing.py"]


class TestUrls:
    def test_a_blob_url_carries_the_pinned_revision(self, repo):
        handle = handle_for(repo)
        assert handle.blob_url("doc.md") == (
            f"https://host.example/org/repo/blob/{handle.rev}/doc.md"
        )

    def test_a_line_becomes_a_fragment(self, repo):
        assert handle_for(repo).blob_url("doc.md", 25).endswith("/doc.md#L25")

    def test_the_url_shape_is_configuration(self, repo):
        """A different host needs a config value, not a code change (rule R2)."""
        handle = RepoHandle.pin(
            repo,
            "https://gitea.internal/team/repo",
            blob_url_template="{repo_url}/src/commit/{rev}/{path}",
        )
        assert handle.blob_url("doc.md").startswith(
            "https://gitea.internal/team/repo/src/commit/"
        )

    def test_from_config_builds_the_same_handle(self, repo):
        handle = RepoHandle.from_config(
            {"local_path": str(repo), "repo_url": "https://host.example/org/repo"}
        )
        assert handle.rev == _git(repo, "rev-parse", "HEAD")


class TestResolvers:
    def test_a_file_ref_resolves_to_a_pinned_blob_url(self, repo):
        registry = Registry()
        registry.register_all(repo_resolvers(handle_for(repo)))

        url = registry.resolve("file:nested/thing.py#L3")

        assert url is not None
        assert url.endswith("/nested/thing.py#L3")

    def test_a_file_absent_at_the_revision_declines(self, repo):
        """A URL would 404 for whoever opened it, so None is the honest answer."""
        registry = Registry()
        registry.register_all(repo_resolvers(handle_for(repo)))
        assert registry.resolve("file:nope.md") is None

    def test_a_commit_ref_resolves(self, repo):
        registry = Registry()
        handle = handle_for(repo)
        registry.register_all(repo_resolvers(handle))
        assert registry.resolve(f"code_change:{handle.rev[:7]}") == handle.commit_url(
            handle.rev[:7]
        )

    def test_a_dirty_commit_ref_declines(self, repo):
        """It describes a tree nobody else can obtain, so it is provenance only."""
        registry = Registry()
        registry.register_all(repo_resolvers(handle_for(repo)))
        assert registry.resolve("code_change:ca6d836-dirty") is None


@pytest.mark.skipif(not FIXTURE_REPO.exists(), reason="fixture repository not present")
@pytest.mark.ac("AC-14")
def test_against_the_fixture_repository_a_citation_names_a_real_commit():
    handle = RepoHandle.pin(FIXTURE_REPO, "https://github.com/SurabhiDeb/chatbot-lab")
    registry = Registry()
    registry.register_all(repo_resolvers(handle))

    url = registry.resolve("file:products/triage/gate.py#L25")

    assert url is not None
    assert handle.rev in url
    assert "/blob/main/" not in url
    assert handle.read("products/triage/gate.py").startswith(b'"""Fail the build')


@pytest.mark.skipif(not FIXTURE_REPO.exists(), reason="fixture repository not present")
@pytest.mark.ac("AC-2")
def test_a_deep_glob_finds_every_candidate_file_in_the_fixture_repository():
    """AC-2 requires no run to be omitted, so enumeration is asserted against a
    hand-countable number rather than trusted.

    The counts here are files, not runs, and the difference is the point. Fixture A's
    runs directory holds 48 `.json` files of which 47 are run records; `v1.json` is a
    bare JSON list from an earlier format and has no `meta` or `results`. The
    prototype reports "7 of 47 runs" and is right about runs, while 48 is right about
    files.

    That gap is a trap for the eval adapter in step 6. Globbing `*.json`, failing to
    parse one, and skipping it quietly would leave the reconciliation that proves AC-2
    comparing 47 against 47 and looking correct. The adapter has to tell "not a run
    record" apart from "a run that failed to import" and account for both out loud
    (EC-2, EC-4). Enumeration's job is to find all 48.
    """
    handle = RepoHandle.pin(FIXTURE_REPO, "https://github.com/SurabhiDeb/chatbot-lab")

    triage = handle.list_files("products/triage/runs/*.json")
    policydesk = handle.list_files("products/policydesk/runs/*.json")
    both = handle.list_files("products/*/runs/*.json")

    assert len(triage) == 48
    assert len(policydesk) == 9
    assert len(both) == len(triage) + len(policydesk)
    assert all(p.endswith(".json") for p in both)

    # Nothing adjacent is swept in: the same directories hold notes and console logs.
    assert not any(p.endswith((".md", ".txt")) for p in both)
