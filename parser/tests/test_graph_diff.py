"""Graph diff engine — pure unit tests, no database.

The golden hashes at the bottom are deliberate: _sig_hash is versioned
(sig1:) and its output MUST be stable across parser releases, or every
re-parse reports every element as CHANGED. If you change the hash basis,
bump the version prefix AND update the goldens in the same commit.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from modules.arcadedb_direct_writer import _sig_hash, anchor_key
from modules.graph_diff import compute_diff, parsed_to_elements


def _fn(name, params="()", rt=None, is_async=False, is_exported=True):
    return {
        "name": name, "params": params, "return_type": rt,
        "is_async": is_async, "is_exported": is_exported,
        "line": 1, "end_line": 5,
    }


def _file(path, functions=(), methods=(), classes=(), interfaces=(), enums=()):
    return {
        "file_path": path,
        "functions": list(functions), "methods": list(methods),
        "classes": list(classes), "interfaces": list(interfaces),
        "enums": list(enums),
    }


def _state(parsed):
    """Build the {anchor: [hash]} snapshot the writer would have stored."""
    state = {}
    for e in parsed_to_elements(parsed):
        state.setdefault(e["anchor_key"], []).append(e["signature_hash"])
    return state


# ── anchor_key ─────────────────────────────────────────────────────────────

def test_anchor_is_line_free_and_method_qualified():
    assert anchor_key("function", "a/b.py", "foo") == "function:a/b.py:foo"
    assert anchor_key("method", "a/b.py", "run", "Task") == "method:a/b.py:Task.run"


# ── _sig_hash sensitivity ──────────────────────────────────────────────────

def test_sig_hash_ignores_position_but_sees_params():
    base = _fn("foo", params="(a: int)")
    moved = {**base, "line": 99, "end_line": 120}          # body edit / moved
    resigned = {**base, "params": "(a: int, b: str)"}      # interface change
    assert _sig_hash("function", base) == _sig_hash("function", moved)
    assert _sig_hash("function", base) != _sig_hash("function", resigned)


def test_sig_hash_sees_return_type_async_export():
    base = _fn("foo")
    assert _sig_hash("function", base) != _sig_hash("function", {**base, "return_type": "int"})
    assert _sig_hash("function", base) != _sig_hash("function", {**base, "is_async": True})
    assert _sig_hash("function", base) != _sig_hash("function", {**base, "is_exported": False})


def test_class_hash_sees_parents_order_insensitively():
    c1 = {"name": "C", "resolved_parents": [{"name": "A"}, {"name": "B"}], "implements": []}
    c2 = {"name": "C", "resolved_parents": [{"name": "B"}, {"name": "A"}], "implements": []}
    c3 = {"name": "C", "resolved_parents": [{"name": "A"}], "implements": []}
    assert _sig_hash("class", c1) == _sig_hash("class", c2)
    assert _sig_hash("class", c1) != _sig_hash("class", c3)


# ── parsed_to_elements ─────────────────────────────────────────────────────

def test_flatten_matches_writer_filters():
    parsed = [_file(
        "src/x.ts",
        functions=[_fn("f")],
        interfaces=[{"name": "Pub", "is_exported": True}, {"name": "Priv", "is_exported": False}],
        enums=[{"name": "E", "is_exported": False}],
    )]
    anchors = {e["anchor_key"] for e in parsed_to_elements(parsed)}
    # exported-only interfaces/enums — same rule as the writer's INSERTs
    assert anchors == {"function:src/x.ts:f", "interface:src/x.ts:Pub"}


# ── compute_diff ───────────────────────────────────────────────────────────

def test_added_changed_deleted_unchanged():
    old = _state([_file("a.py", functions=[_fn("keep"), _fn("edit", params="(x)"), _fn("gone")])])
    new = [_file("a.py", functions=[_fn("keep"), _fn("edit", params="(x, y)"), _fn("fresh")])]
    d = compute_diff(old, parsed_to_elements(new))
    assert [e["qualified_name"] for e in d.added] == ["fresh"]
    assert [e["qualified_name"] for e in d.changed] == ["edit"]
    assert [e["anchor_key"] for e in d.deleted] == ["function:a.py:gone"]
    assert d.summary()["added"] == 1 and not d.first_parse


def test_unchanged_graph_diffs_empty():
    files = [_file("a.py", functions=[_fn("f"), _fn("g", params="(x)")])]
    d = compute_diff(_state(files), parsed_to_elements(files))
    assert d.is_empty()


def test_first_parse_flag():
    files = [_file("a.py", functions=[_fn("f")])]
    d = compute_diff({}, parsed_to_elements(files), first_parse=True)
    assert d.first_parse and len(d.added) == 1


def test_deleted_anchor_is_split_for_reporting():
    old = _state([_file("a/b.py", methods=[{**_fn("run"), "class_name": "Task"}])])
    d = compute_diff(old, [])
    assert d.deleted == [{
        "anchor_key": "method:a/b.py:Task.run",
        "kind": "method", "file": "a/b.py", "qualified_name": "Task.run",
    }]


# ── duplicate names: the multiset semantics ────────────────────────────────

def test_duplicate_names_equal_multisets_are_unchanged():
    files = [_file("a.py", functions=[_fn("helper", params="(a)"), _fn("helper", params="(a)")])]
    d = compute_diff(_state(files), parsed_to_elements(files))
    assert d.is_empty()


def test_duplicate_names_one_twin_changing_is_changed():
    old = _state([_file("a.py", functions=[_fn("helper", params="(a)"), _fn("helper", params="(b)")])])
    new = [_file("a.py", functions=[_fn("helper", params="(a)"), _fn("helper", params="(c)")])]
    d = compute_diff(old, parsed_to_elements(new))
    assert [e["anchor_key"] for e in d.changed] == ["function:a.py:helper"]
    assert not d.added and not d.deleted


def test_duplicate_count_change_is_changed_not_added():
    old = _state([_file("a.py", functions=[_fn("helper")])])
    new = [_file("a.py", functions=[_fn("helper"), _fn("helper")])]
    d = compute_diff(old, parsed_to_elements(new))
    assert len(d.changed) == 1 and not d.added and not d.deleted


# ── golden hashes: cross-release stability contract ────────────────────────

def test_golden_hashes_pin_the_sig1_algorithm():
    assert _sig_hash("function", _fn("foo", params="(a: int, b: str)", rt="bool")) == \
        "sig1:449a8184f3e86871"
    assert _sig_hash("method", {**_fn("run", params="(self)"), "class_name": "Task"}) == \
        "sig1:ca06784e32d0e031"
    assert _sig_hash("class", {"name": "C", "resolved_parents": [{"name": "B"}], "implements": ["I"]}) == \
        "sig1:2a7e281e3e8d12fb"
    assert _sig_hash("interface", {"name": "I"}) == "sig1:25055a5bbfd0881d"
    assert _sig_hash("enum", {"name": "E"}) == "sig1:b17e3a0e338b75be"
