"""A git repository as a source, pinned to one immutable revision.

Three properties this exists to guarantee, all of them things that go wrong quietly
if left to convention:

**A source pins a commit, never a branch.** `pin()` refuses anything that is not a
commit sha. PRD B2: a finding citing `blob/main/SPEC.md` becomes wrong the moment
someone edits the file and the reader cannot tell, while one citing
`blob/ef07ac9/SPEC.md` stays true forever.

**Reads come from the commit, not the working tree.** `read()` uses `git show
<rev>:<path>`, so a clause imported from a spec is the text at the revision its
citation names. Reading the working tree would make the citation a near-miss —
right file, possibly different content — which is the worst kind of wrong, because
it looks right.

**A dirty revision is provenance, not a citation target.** Run files in the wild
record shas like `ca6d836-dirty`, meaning the tree had uncommitted changes. That
cannot be fetched by anyone, so it is kept as metadata and never used to build a
URL; refs that would need it resolve to None and surface in `unresolved`.

The URL shape is configuration, not code. `blob_url_template` defaults to GitHub's
but is overridable per source, so a GitLab, Gitea or internal host needs no change
here (agnosticism rule R2).
"""

from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from layer.refs.ref import Ref
from layer.refs.registry import Resolver

#: A commit sha, abbreviated or full. Tags are excluded on purpose: a tag can be
#: moved, so it is not a guarantee, only a convention.
_SHA = re.compile(r"\A[0-9a-f]{7,40}\Z")

DEFAULT_BLOB_URL_TEMPLATE = "{repo_url}/blob/{rev}/{path}"
DEFAULT_COMMIT_URL_TEMPLATE = "{repo_url}/commit/{rev}"


class NotPinnable(ValueError):
    """The revision given is not an immutable commit, so nothing may cite it."""


def is_commit_sha(rev: str) -> bool:
    return bool(rev) and bool(_SHA.match(rev))


def is_dirty(rev: str) -> bool:
    """`ca6d836-dirty` — a real shape in committed eval metadata."""
    return rev.endswith("-dirty")


@dataclass(frozen=True)
class RepoHandle:
    """One repository at one revision.

    `local_path` is where the Layer can run git. `repo_url` is where a human reads
    it. They are separate because the machine and the reader need not share a view.
    """

    local_path: Path
    repo_url: str
    rev: str
    blob_url_template: str = DEFAULT_BLOB_URL_TEMPLATE
    commit_url_template: str = DEFAULT_COMMIT_URL_TEMPLATE

    def __post_init__(self) -> None:
        if not is_commit_sha(self.rev):
            raise NotPinnable(
                f"{self.rev!r} is not a commit sha. A source must pin an immutable "
                f"revision, because a branch moves and a citation built on one rots "
                f"without the reader noticing (AC-14)."
            )

    # -- construction ------------------------------------------------------------

    @classmethod
    def pin(cls, local_path: str | Path, repo_url: str, rev: str = "HEAD", **kw) -> RepoHandle:
        """Resolve `rev` to a concrete commit and hold the repository at it.

        `HEAD`, a branch or a tag may be *asked for*; what is stored is always the
        sha it resolved to at that moment. That is the whole point: the caller says
        "current", and the Layer records which commit "current" meant.
        """
        path = Path(local_path)
        resolved = _git(path, "rev-parse", "--verify", f"{rev}^{{commit}}")
        return cls(local_path=path, repo_url=repo_url.rstrip("/"), rev=resolved, **kw)

    @classmethod
    def from_config(cls, config: dict) -> RepoHandle:
        """Build from a `source.config` row. Shape in config, mechanism here."""
        return cls.pin(
            local_path=config["local_path"],
            repo_url=config["repo_url"],
            rev=config.get("rev", "HEAD"),
            blob_url_template=config.get("blob_url_template", DEFAULT_BLOB_URL_TEMPLATE),
            commit_url_template=config.get("commit_url_template", DEFAULT_COMMIT_URL_TEMPLATE),
        )

    # -- reading -----------------------------------------------------------------

    def read(self, path: str) -> bytes:
        """File contents at the pinned revision. Never the working tree."""
        return _git_bytes(self.local_path, "show", f"{self.rev}:{path}")

    def exists(self, path: str) -> bool:
        try:
            self.read(path)
            return True
        except FileNotFoundError:
            return False

    def list_files(self, pattern: str = "") -> list[str]:
        """Paths at the pinned revision, optionally filtered by a glob.

        `git ls-tree` rather than a filesystem walk, so an untracked or
        locally-deleted file cannot change what the Layer thinks the source holds.

        Filtering happens in Python rather than as a git pathspec. `ls-tree` rejects
        `:(glob)` magic outright and treats a bare `products/*/runs/*.json` as
        matching nothing, returning success with no output — so the eval backfill
        would quietly store zero observations and report no error, which is exactly
        what AC-2 ("no run omitted") exists to catch. `full_match` is anchored and
        does not let `*` cross a separator, so a path glob means what it looks like.
        """
        out = _git(self.local_path, "ls-tree", "-r", "--name-only", self.rev)
        paths = [line for line in out.splitlines() if line]
        if not pattern:
            return paths
        return [p for p in paths if PurePosixPath(p).full_match(pattern)]

    # -- urls --------------------------------------------------------------------

    def blob_url(self, path: str, line: int | None = None) -> str:
        url = self.blob_url_template.format(
            repo_url=self.repo_url, rev=self.rev, path=path.lstrip("/")
        )
        return f"{url}#L{line}" if line else url

    def commit_url(self, rev: str | None = None) -> str:
        return self.commit_url_template.format(repo_url=self.repo_url, rev=rev or self.rev)


# -- resolvers ------------------------------------------------------------------


def _file_resolver(handle: RepoHandle) -> Resolver:
    """`file:products/triage/gate.py#L25` -> a blob URL at the pinned revision."""

    def resolve(ref: Ref) -> str | None:
        path, _, fragment = ref.id.partition("#")
        line: int | None = None
        if fragment.startswith("L") and fragment[1:].isdigit():
            line = int(fragment[1:])
        if not handle.exists(path):
            # The ref names a file this revision does not contain. Declining is the
            # honest answer; a URL would 404 for whoever opened it.
            return None
        return handle.blob_url(path, line)

    return resolve


def _code_change_resolver(handle: RepoHandle) -> Resolver:
    """`code_change:98380d1` -> a commit URL, when the sha is one that exists."""

    def resolve(ref: Ref) -> str | None:
        if is_dirty(ref.id) or not is_commit_sha(ref.id):
            # A dirty sha describes a tree nobody else can obtain. Kept as provenance
            # on the record that carries it, never offered as something to open.
            return None
        return handle.commit_url(ref.id)

    return resolve


def repo_resolvers(handle: RepoHandle) -> dict[str, Resolver]:
    """The ref kinds a repository can answer for."""
    return {"file": _file_resolver(handle), "code_change": _code_change_resolver(handle)}


# -- git ------------------------------------------------------------------------


def _run(path: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(path), *args], capture_output=True, check=False
    )


def _git(path: Path, *args: str) -> str:
    done = _run(path, *args)
    if done.returncode != 0:
        raise _git_error(path, args, done)
    return done.stdout.decode().strip()


def _git_bytes(path: Path, *args: str) -> bytes:
    done = _run(path, *args)
    if done.returncode != 0:
        raise _git_error(path, args, done)
    return done.stdout


def _git_error(path: Path, args: tuple[str, ...], done) -> Exception:
    message = done.stderr.decode().strip()
    # `git show` says this for a path absent at that revision; callers treat a missing
    # file as a fact about the revision rather than as a failure.
    if "does not exist" in message or "exists on disk, but not in" in message:
        return FileNotFoundError(message)
    return RuntimeError(f"git {' '.join(args)} in {path}: {message}")
