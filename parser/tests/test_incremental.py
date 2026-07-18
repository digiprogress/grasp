"""Incremental re-parse — DB-free unit tests.

Covers the two pure pieces of the incremental path:
  - derive_git_changes: changed/removed lists from a real (temp) git repo
  - the resolver's borrowed context: graph-sourced class symbols and the
    discovery-sourced file universe standing in for unparsed files
"""

import os
import subprocess
import sys
import tempfile

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from handler import derive_git_changes
from modules.resolver import Resolver, resolve_references


# ── derive_git_changes ─────────────────────────────────────────────────────

def _git(repo, *args):
    subprocess.run(["git", "-C", repo, *args], check=True, capture_output=True,
                   env={**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@t",
                        "GIT_COMMITTER_NAME": "t", "GIT_COMMITTER_EMAIL": "t@t"})


def _sha(repo):
    return subprocess.run(["git", "-C", repo, "rev-parse", "HEAD"],
                          capture_output=True, text=True).stdout.strip()


def _repo_with_two_commits():
    repo = tempfile.mkdtemp(prefix="grasp-test-repo_")
    _git(repo, "init", "-q")
    for name, body in [("keep.py", "def keep(): pass\n"),
                       ("edit.py", "def old(): pass\n"),
                       ("gone.py", "def gone(): pass\n")]:
        with open(os.path.join(repo, name), "w") as f:
            f.write(body)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "base")
    base = _sha(repo)

    with open(os.path.join(repo, "edit.py"), "w") as f:
        f.write("def new(x): pass\n")
    with open(os.path.join(repo, "fresh.py"), "w") as f:
        f.write("def fresh(): pass\n")
    os.remove(os.path.join(repo, "gone.py"))
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "change")
    return repo, base


def test_derive_changed_and_removed():
    repo, base = _repo_with_two_commits()
    changed, removed = derive_git_changes(repo, base)
    assert sorted(changed) == ["edit.py", "fresh.py"]
    assert removed == ["gone.py"]


def test_derive_same_sha_is_empty():
    repo, _ = _repo_with_two_commits()
    changed, removed = derive_git_changes(repo, _sha(repo))
    assert changed == [] and removed == []


def test_derive_unknown_sha_falls_back():
    repo, _ = _repo_with_two_commits()
    # unknown SHA (rebased away / shallow clone) → None → caller full-parses
    assert derive_git_changes(repo, "0" * 40) is None


def test_derive_rename_is_delete_plus_add():
    repo, base = _repo_with_two_commits()
    _git(repo, "mv", "keep.py", "kept.py")
    _git(repo, "commit", "-qm", "rename")
    changed, removed = derive_git_changes(repo, base)
    assert "kept.py" in changed and "keep.py" in removed


# ── resolver: borrowed context ─────────────────────────────────────────────

def _parsed_changed_file():
    """Parse result for ONE changed file whose class extends a class that
    lives in an UNPARSED file — only the graph knows where Base is."""
    return [{
        "file_path": "src/b.ts",
        "language": "typescript",
        "classes": [{"name": "Child", "line": 3, "parents": ["Base"]}],
        "functions": [], "methods": [], "interfaces": [], "enums": [],
        "imports": [{"source": "./a", "names": ["Base"]}],
    }]


def test_inheritance_resolves_via_graph_symbols():
    parsed = resolve_references(
        _parsed_changed_file(),
        all_files={"src/a.ts", "src/b.ts"},
        extra_symbols=[{"name": "Base", "file_path": "src/a.ts", "line": 7}],
    )
    rp = parsed[0]["classes"][0]["resolved_parents"]
    assert rp == [{"name": "Base", "file": "src/a.ts", "line": 7, "confidence": "high"}]


def test_import_resolves_against_discovered_universe():
    # src/a.ts was never parsed this run — only discovery knows it exists
    parsed = resolve_references(
        _parsed_changed_file(),
        all_files={"src/a.ts", "src/b.ts"},
        extra_symbols=[{"name": "Base", "file_path": "src/a.ts", "line": 7}],
    )
    assert parsed[0]["imports"][0]["resolved_file"] == "src/a.ts"


def test_stale_graph_symbol_for_parsed_file_is_skipped():
    r = Resolver()
    parsed = _parsed_changed_file()
    r._build_symbol_table(parsed)
    # graph still holds b.ts's PREVIOUS class — must not shadow the fresh parse
    r._merge_extra_symbols(
        [{"name": "Renamed", "file_path": "src/b.ts", "line": 1},
         {"name": "Base", "file_path": "src/a.ts", "line": 7}],
        parsed,
    )
    assert "Renamed" not in r.symbols
    assert [s.file_path for s in r.symbols["Base"]] == ["src/a.ts"]


def test_without_context_behaves_like_before():
    # full-parse path: no all_files / extra_symbols — resolution limited to
    # the parsed set, exactly as on main
    parsed = resolve_references(_parsed_changed_file())
    rp = parsed[0]["classes"][0]["resolved_parents"]
    assert rp[0]["confidence"] == "unresolved"
    assert "resolved_file" not in parsed[0]["imports"][0]
