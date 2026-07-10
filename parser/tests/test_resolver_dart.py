"""
Contract tests for Dart import resolution in the Resolver.

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
    """Build synthetic parsed_results for Dart files.

    files_with_imports = {
        "lib/main.dart": [{"source": "utils.dart", "names": [], "line": 1}],
        "lib/utils.dart": [],
    }
    """
    return [
        {
            "file_path": path,
            "language": "dart",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. Relative imports
# =========================================================================


class TestRelativeImports:
    """Dot-prefixed and plain relative path resolution."""

    def test_dot_slash_relative(self):
        """`import './utils.dart'` → lib/utils.dart"""
        results = make_parsed_results({
            "lib/main.dart": [{"source": "./utils.dart", "names": [], "line": 1}],
            "lib/utils.dart": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/utils.dart"

    def test_dot_dot_relative(self):
        """`import '../models/user.dart'` → lib/models/user.dart"""
        results = make_parsed_results({
            "lib/src/main.dart": [{"source": "../models/user.dart", "names": [], "line": 1}],
            "lib/models/user.dart": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/models/user.dart"

    def test_plain_relative(self):
        """`import 'utils/helper.dart'` (no dot prefix) → resolved relative to importing file"""
        results = make_parsed_results({
            "lib/main.dart": [{"source": "utils/helper.dart", "names": [], "line": 1}],
            "lib/utils/helper.dart": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/utils/helper.dart"

    def test_sibling_file(self):
        """`import 'constants.dart'` → lib/src/constants.dart (same dir)"""
        results = make_parsed_results({
            "lib/src/app.dart": [{"source": "constants.dart", "names": [], "line": 1}],
            "lib/src/constants.dart": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/src/constants.dart"

    def test_deep_relative(self):
        """`import '../../core/base.dart'` → two levels up"""
        results = make_parsed_results({
            "lib/features/auth/login.dart": [{"source": "../../core/base.dart", "names": [], "line": 1}],
            "lib/core/base.dart": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/core/base.dart"


# =========================================================================
# B. Self-package imports
# =========================================================================


class TestSelfPackageImports:
    """package:my_app/... when my_app matches pubspec.yaml name."""

    def test_self_package_import(self):
        """`import 'package:my_app/src/file.dart'` → lib/src/file.dart"""
        resolver = Resolver()
        resolver._dart_package_name = "my_app"
        results = make_parsed_results({
            "lib/main.dart": [{"source": "package:my_app/src/file.dart", "names": [], "line": 1}],
            "lib/src/file.dart": [],
        })
        resolver.resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/src/file.dart"

    def test_self_package_nested_path(self):
        """`import 'package:my_app/models/user.dart'` → lib/models/user.dart"""
        resolver = Resolver()
        resolver._dart_package_name = "my_app"
        results = make_parsed_results({
            "lib/main.dart": [{"source": "package:my_app/models/user.dart", "names": [], "line": 1}],
            "lib/models/user.dart": [],
        })
        resolver.resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/models/user.dart"

    def test_self_package_suffix_matching(self):
        """`package:my_app/src/utils.dart` resolves via suffix when prefixed."""
        resolver = Resolver()
        resolver._dart_package_name = "my_app"
        results = make_parsed_results({
            "project/lib/main.dart": [{"source": "package:my_app/src/utils.dart", "names": [], "line": 1}],
            "project/lib/src/utils.dart": [],
        })
        resolver.resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "project/lib/src/utils.dart"


# =========================================================================
# C. External imports → None
# =========================================================================


class TestExternalImports:
    """Stdlib and third-party imports resolve to None (no resolved_file)."""

    def test_dart_stdlib(self):
        """`import 'dart:math'` → no resolved_file"""
        results = make_parsed_results({
            "lib/main.dart": [{"source": "dart:math", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_dart_core(self):
        """`import 'dart:core'` → no resolved_file"""
        results = make_parsed_results({
            "lib/main.dart": [{"source": "dart:core", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_external_package(self):
        """`import 'package:flutter/material.dart'` → no resolved_file"""
        results = make_parsed_results({
            "lib/main.dart": [{"source": "package:flutter/material.dart", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_external_package_other(self):
        """`import 'package:http/http.dart'` → no resolved_file"""
        results = make_parsed_results({
            "lib/main.dart": [{"source": "package:http/http.dart", "names": [], "line": 1}],
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
            "lib/main.dart": [
                {"source": "./utils.dart", "names": [], "line": 1},
                {"source": "dart:math", "names": [], "line": 2},
                {"source": "package:flutter/material.dart", "names": [], "line": 3},
            ],
            "lib/utils.dart": [],
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
            "lib/main.dart": [
                {"source": "./utils.dart", "names": [], "line": 1},
                {"source": "../models/user.dart", "names": [], "line": 2},
                {"source": "dart:math", "names": [], "line": 3},
                {"source": "package:flutter/material.dart", "names": [], "line": 4},
            ],
            "lib/utils.dart": [],
            "models/user.dart": [],
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
