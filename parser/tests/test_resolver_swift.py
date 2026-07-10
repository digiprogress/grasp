"""
Contract tests for Swift import resolution in the Resolver.

Tests operate on the Resolver class directly with synthetic parsed_results.
No filesystem, no DB, no graph — each test builds a fake file set and
verifies resolved_file values.
"""

import copy
import importlib
import sys
import os

import pytest

# Import resolver directly to avoid modules/__init__.py pulling in heavy deps
_modules_dir = os.path.join(os.path.dirname(__file__), os.pardir, "modules")
_spec = importlib.util.spec_from_file_location(
    "resolver", os.path.join(_modules_dir, "resolver.py"),
    submodule_search_locations=[],
)
_mod = importlib.util.module_from_spec(_spec)

# resolver.py imports from modules.lang — ensure that resolves correctly
if "modules.lang" not in sys.modules:
    _lang_spec = importlib.util.spec_from_file_location(
        "modules.lang", os.path.join(_modules_dir, "lang.py"),
    )
    _lang_mod = importlib.util.module_from_spec(_lang_spec)
    sys.modules["modules.lang"] = _lang_mod
    _lang_spec.loader.exec_module(_lang_mod)

if "modules.resolver_langs" not in sys.modules:
    _rl_spec = importlib.util.spec_from_file_location(
        "modules.resolver_langs", os.path.join(_modules_dir, "resolver_langs.py"),
    )
    _rl_mod = importlib.util.module_from_spec(_rl_spec)
    sys.modules["modules.resolver_langs"] = _rl_mod
    _rl_spec.loader.exec_module(_rl_mod)

_spec.loader.exec_module(_mod)
Resolver = _mod.Resolver


def make_parsed_results(files_with_imports):
    """Build synthetic parsed_results for Swift files.

    files_with_imports = {
        "Sources/App/main.swift": [{"source": "MyModule", "names": [], "line": 1}],
        "Sources/MyModule/file.swift": [],
    }
    """
    return [
        {
            "file_path": path,
            "language": "swift",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. Project-internal module imports (suffix dir matching)
# =========================================================================


class TestProjectInternalImports:
    """Suffix-matched project-internal Swift module imports."""

    def test_simple_module_import(self):
        """`import MyModule` → matches Sources/MyModule/file.swift"""
        results = make_parsed_results({
            "Sources/App/main.swift": [{"source": "MyModule", "names": [], "line": 1}],
            "Sources/MyModule/file.swift": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Sources/MyModule/file.swift"

    def test_nested_module(self):
        """`import Networking` → matches Sources/Networking/client.swift"""
        results = make_parsed_results({
            "Sources/App/main.swift": [{"source": "Networking", "names": [], "line": 1}],
            "Sources/Networking/client.swift": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Sources/Networking/client.swift"

    def test_deep_path(self):
        """`import Utils` → matches pkg/Sources/Utils/helpers.swift"""
        results = make_parsed_results({
            "Sources/App/main.swift": [{"source": "Utils", "names": [], "line": 1}],
            "pkg/Sources/Utils/helpers.swift": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "pkg/Sources/Utils/helpers.swift"

    def test_selective_import_uses_module_name(self):
        """`import struct MyModule.MyStruct` → resolver sees source="MyModule", matches dir"""
        results = make_parsed_results({
            "Sources/App/main.swift": [{"source": "MyModule", "names": ["MyStruct"], "line": 1}],
            "Sources/MyModule/types.swift": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Sources/MyModule/types.swift"


# =========================================================================
# B. Multiple files picks first alphabetically
# =========================================================================


class TestMultipleSwiftFiles:
    """Swift module = directory; pick first .swift file alphabetically."""

    def test_picks_first_alphabetically(self):
        """`import MyModule` with types.swift and helpers.swift → helpers.swift"""
        results = make_parsed_results({
            "Sources/App/main.swift": [{"source": "MyModule", "names": [], "line": 1}],
            "Sources/MyModule/types.swift": [],
            "Sources/MyModule/helpers.swift": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Sources/MyModule/helpers.swift"

    def test_picks_first_with_many_files(self):
        """`import MyModule` with a.swift, b.swift, z.swift → a.swift"""
        results = make_parsed_results({
            "Sources/App/main.swift": [{"source": "MyModule", "names": [], "line": 1}],
            "Sources/MyModule/z.swift": [],
            "Sources/MyModule/b.swift": [],
            "Sources/MyModule/a.swift": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Sources/MyModule/a.swift"


# =========================================================================
# C. External/stdlib → None
# =========================================================================


class TestExternalImports:
    """Stdlib and framework imports resolve to None (no resolved_file)."""

    def test_foundation(self):
        """`import Foundation` → no resolved_file"""
        results = make_parsed_results({
            "Sources/main.swift": [{"source": "Foundation", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_uikit(self):
        """`import UIKit` → no resolved_file"""
        results = make_parsed_results({
            "Sources/main.swift": [{"source": "UIKit", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_swiftui(self):
        """`import SwiftUI` → no resolved_file"""
        results = make_parsed_results({
            "Sources/main.swift": [{"source": "SwiftUI", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# D. Determinism
# =========================================================================


class TestDeterminism:
    """Running resolver twice on the same input yields identical output."""

    def test_deterministic_output(self):
        files = {
            "Sources/App/main.swift": [
                {"source": "MyModule", "names": [], "line": 1},
                {"source": "Foundation", "names": [], "line": 2},
            ],
            "Sources/MyModule/file.swift": [],
        }
        results_a = make_parsed_results(copy.deepcopy(files))
        results_b = make_parsed_results(copy.deepcopy(files))

        Resolver().resolve(results_a)
        Resolver().resolve(results_b)

        assert results_a == results_b


# =========================================================================
# E. Closed-world validation
# =========================================================================


class TestClosedWorld:
    """Every resolved_file must exist in the input file set."""

    def test_all_resolved_files_in_file_set(self):
        files = {
            "Sources/App/main.swift": [
                {"source": "MyModule", "names": [], "line": 1},
                {"source": "Foundation", "names": [], "line": 2},
                {"source": "UIKit", "names": [], "line": 3},
            ],
            "Sources/MyModule/types.swift": [],
            "Sources/MyModule/helpers.swift": [],
        }
        results = make_parsed_results(files)
        Resolver().resolve(results)

        all_files = set(files.keys())
        for file_result in results:
            for imp in file_result["imports"]:
                resolved = imp.get("resolved_file")
                if resolved is not None:
                    assert resolved in all_files, (
                        f"resolved_file '{resolved}' not in file set"
                    )
