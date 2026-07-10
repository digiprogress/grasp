"""
Contract tests for Ruby import resolution in the Resolver.

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
    """Build synthetic parsed_results for Ruby files.

    files_with_imports = {
        "app/main.rb": [{"source": "app/models/user", "names": [], "line": 1}],
        "app/models/user.rb": [],
    }
    """
    return [
        {
            "file_path": path,
            "language": "ruby",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. require (project path → suffix match .rb)
# =========================================================================


class TestRequireImports:
    """Project-internal require imports resolved via suffix matching."""

    def test_simple_require(self):
        """`require 'app/models/user'` → app/models/user.rb"""
        results = make_parsed_results({
            "app/main.rb": [{"source": "app/models/user", "names": [], "line": 1}],
            "app/models/user.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "app/models/user.rb"

    def test_require_with_extension(self):
        """`require 'lib/utils.rb'` → lib/utils.rb (already has .rb)"""
        results = make_parsed_results({
            "app/main.rb": [{"source": "lib/utils.rb", "names": [], "line": 1}],
            "lib/utils.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/utils.rb"

    def test_require_deep_path(self):
        """`require 'app/services/payment/stripe'` → suffix match"""
        results = make_parsed_results({
            "app/main.rb": [
                {"source": "app/services/payment/stripe", "names": [], "line": 1},
            ],
            "app/services/payment/stripe.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "app/services/payment/stripe.rb"

    def test_require_suffix_matching(self):
        """`require 'models/user'` matches even with extra prefix dirs"""
        results = make_parsed_results({
            "app/main.rb": [{"source": "models/user", "names": [], "line": 1}],
            "project/app/models/user.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "project/app/models/user.rb"


# =========================================================================
# B. require_relative (relative resolution + .rb)
# =========================================================================


class TestRequireRelativeImports:
    """require_relative imports resolved relative to importing file."""

    def test_sibling_file(self):
        """`require_relative 'utils'` → app/utils.rb (same dir)"""
        results = make_parsed_results({
            "app/main.rb": [
                {"source": "utils", "names": [], "line": 1, "kind": "require_relative"},
            ],
            "app/utils.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "app/utils.rb"

    def test_parent_relative(self):
        """`require_relative '../lib/helpers'` → lib/helpers.rb"""
        results = make_parsed_results({
            "app/controllers/main.rb": [
                {"source": "../lib/helpers", "names": [], "line": 1, "kind": "require_relative"},
            ],
            "app/lib/helpers.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "app/lib/helpers.rb"

    def test_with_extension(self):
        """`require_relative './config.rb'` → app/config.rb"""
        results = make_parsed_results({
            "app/main.rb": [
                {"source": "./config.rb", "names": [], "line": 1, "kind": "require_relative"},
            ],
            "app/config.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "app/config.rb"

    def test_deep_relative(self):
        """`require_relative '../../core/base'` → core/base.rb"""
        results = make_parsed_results({
            "app/features/auth/login.rb": [
                {"source": "../../core/base", "names": [], "line": 1, "kind": "require_relative"},
            ],
            "app/core/base.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "app/core/base.rb"


# =========================================================================
# C. External/stdlib → None
# =========================================================================


class TestExternalImports:
    """Stdlib and gem imports resolve to None (no resolved_file)."""

    def test_json(self):
        """`require 'json'` → no resolved_file"""
        results = make_parsed_results({
            "app/main.rb": [{"source": "json", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_net_http(self):
        """`require 'net/http'` → no resolved_file"""
        results = make_parsed_results({
            "app/main.rb": [{"source": "net/http", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_rails_gem(self):
        """`require 'active_record'` → no resolved_file"""
        results = make_parsed_results({
            "app/main.rb": [{"source": "active_record", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# D. load (like require)
# =========================================================================


class TestLoadImports:
    """load works like require — suffix match with .rb."""

    def test_load_with_extension(self):
        """`load 'lib/config.rb'` → lib/config.rb"""
        results = make_parsed_results({
            "app/main.rb": [
                {"source": "lib/config.rb", "names": [], "line": 1, "kind": "load"},
            ],
            "lib/config.rb": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "lib/config.rb"


# =========================================================================
# E. Determinism
# =========================================================================


class TestDeterminism:
    """Running resolver twice on the same input yields identical output."""

    def test_deterministic_output(self):
        files = {
            "app/main.rb": [
                {"source": "app/models/user", "names": [], "line": 1},
                {"source": "utils", "names": [], "line": 2, "kind": "require_relative"},
                {"source": "json", "names": [], "line": 3},
            ],
            "app/models/user.rb": [],
            "app/utils.rb": [],
        }
        results_a = make_parsed_results(copy.deepcopy(files))
        results_b = make_parsed_results(copy.deepcopy(files))

        Resolver().resolve(results_a)
        Resolver().resolve(results_b)

        assert results_a == results_b


# =========================================================================
# F. Closed-world validation
# =========================================================================


class TestClosedWorld:
    """Every resolved_file must exist in the input file set."""

    def test_all_resolved_files_in_file_set(self):
        files = {
            "app/main.rb": [
                {"source": "app/models/user", "names": [], "line": 1},
                {"source": "utils", "names": [], "line": 2, "kind": "require_relative"},
                {"source": "json", "names": [], "line": 3},
                {"source": "net/http", "names": [], "line": 4},
            ],
            "app/models/user.rb": [],
            "app/utils.rb": [],
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
