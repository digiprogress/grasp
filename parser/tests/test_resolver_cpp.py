"""
Contract tests for C++ import resolution in the Resolver.

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
    """Build synthetic parsed_results for C++ files.

    files_with_imports = {
        "src/utils.hpp": [],
        "src/main.cpp": [{"source": "utils.hpp", "names": [], "kind": "local", "line": 1}],
    }
    """
    return [
        {
            "file_path": path,
            "language": "cpp",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. Local includes (relative resolution)
# =========================================================================


class TestLocalIncludes:
    """Local #include "file.hpp" resolves relative to importing file."""

    def test_sibling_header(self):
        """#include "utils.hpp" from src/main.cpp → src/utils.hpp"""
        results = make_parsed_results({
            "src/main.cpp": [{"source": "utils.hpp", "names": [], "kind": "local", "line": 1}],
            "src/utils.hpp": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils.hpp"

    def test_hpp_include(self):
        """C++ specific: .hpp headers resolve correctly."""
        results = make_parsed_results({
            "src/engine.cpp": [{"source": "engine.hpp", "names": [], "kind": "local", "line": 1}],
            "src/engine.hpp": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/engine.hpp"

    def test_relative_path(self):
        """#include "../include/config.hpp" from src/main.cpp → include/config.hpp"""
        results = make_parsed_results({
            "src/main.cpp": [{"source": "../include/config.hpp", "names": [], "kind": "local", "line": 1}],
            "include/config.hpp": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "include/config.hpp"

    def test_c_header_from_cpp(self):
        """C++ including a C header: #include "legacy.h" → resolves"""
        results = make_parsed_results({
            "src/main.cpp": [{"source": "legacy.h", "names": [], "kind": "local", "line": 1}],
            "src/legacy.h": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/legacy.h"

    def test_nested_dirs(self):
        """Deep relative path resolution."""
        results = make_parsed_results({
            "src/core/engine.cpp": [{"source": "../../include/types.hpp", "names": [], "kind": "local", "line": 1}],
            "include/types.hpp": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "include/types.hpp"

    def test_suffix_fallback(self):
        """When relative doesn't match, suffix matching kicks in."""
        results = make_parsed_results({
            "src/main.cpp": [{"source": "config.hpp", "names": [], "kind": "local", "line": 1}],
            "include/config.hpp": [],  # not in src/
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "include/config.hpp"


# =========================================================================
# B. System includes → None
# =========================================================================


class TestSystemIncludes:
    """System #include <header> resolves to None (stdlib)."""

    def test_iostream(self):
        """#include <iostream> → no resolved_file"""
        results = make_parsed_results({
            "main.cpp": [{"source": "iostream", "names": [], "kind": "system", "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_vector(self):
        """#include <vector> → no resolved_file"""
        results = make_parsed_results({
            "main.cpp": [{"source": "vector", "names": [], "kind": "system", "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_memory(self):
        """#include <memory> → no resolved_file"""
        results = make_parsed_results({
            "main.cpp": [{"source": "memory", "names": [], "kind": "system", "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_c_stdlib_from_cpp(self):
        """#include <cstdio> → no resolved_file"""
        results = make_parsed_results({
            "main.cpp": [{"source": "cstdio", "names": [], "kind": "system", "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# C. Determinism
# =========================================================================


class TestDeterminism:
    """Running resolver twice on the same input yields identical output."""

    def test_deterministic_output(self):
        files = {
            "src/main.cpp": [
                {"source": "utils.hpp", "names": [], "kind": "local", "line": 1},
                {"source": "iostream", "names": [], "kind": "system", "line": 2},
            ],
            "src/utils.hpp": [],
        }
        results_a = make_parsed_results(copy.deepcopy(files))
        results_b = make_parsed_results(copy.deepcopy(files))

        Resolver().resolve(results_a)
        Resolver().resolve(results_b)

        assert results_a == results_b


# =========================================================================
# D. Closed-world validation
# =========================================================================


class TestClosedWorld:
    """Every resolved_file must exist in the input file set."""

    def test_all_resolved_files_in_file_set(self):
        files = {
            "src/main.cpp": [
                {"source": "utils.hpp", "names": [], "kind": "local", "line": 1},
                {"source": "iostream", "names": [], "kind": "system", "line": 2},
                {"source": "../include/config.hpp", "names": [], "kind": "local", "line": 3},
            ],
            "src/utils.hpp": [],
            "include/config.hpp": [],
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
