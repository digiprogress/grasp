"""
Contract tests for PHP import resolution in the Resolver.

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
    """Build synthetic parsed_results for PHP files.

    files_with_imports = {
        "src/Controller/HomeController.php": [
            {"source": "App\\Models\\User", "names": ["User"], "line": 3}
        ],
        "src/Models/User.php": [],
    }
    """
    return [
        {
            "file_path": path,
            "language": "php",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. Namespace use imports (suffix matching with \ → /)
# =========================================================================


class TestNamespaceUseImports:
    """Namespace use declarations resolved via suffix matching."""

    def test_simple_namespace(self):
        """`use App\\Models\\User` → src/App/Models/User.php"""
        results = make_parsed_results({
            "src/Controller/Home.php": [
                {"source": "App\\Models\\User", "names": ["User"], "line": 3},
            ],
            "src/App/Models/User.php": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/App/Models/User.php"

    def test_deep_namespace(self):
        """`use App\\Services\\Payment\\Stripe` → matches via suffix"""
        results = make_parsed_results({
            "src/Controller/Home.php": [
                {"source": "App\\Services\\Payment\\Stripe", "names": ["Stripe"], "line": 3},
            ],
            "src/App/Services/Payment/Stripe.php": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/App/Services/Payment/Stripe.php"

    def test_namespace_suffix_matching(self):
        """`use App\\Models\\User` matches even with extra prefix dirs"""
        results = make_parsed_results({
            "project/src/Controller/Home.php": [
                {"source": "App\\Models\\User", "names": ["User"], "line": 3},
            ],
            "project/src/App/Models/User.php": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "project/src/App/Models/User.php"

    def test_grouped_use(self):
        """`use App\\Models\\{User, Order}` — each resolves independently"""
        results = make_parsed_results({
            "src/Controller/Home.php": [
                {"source": "App\\Models\\User", "names": ["User"], "line": 3},
                {"source": "App\\Models\\Order", "names": ["Order"], "line": 3},
            ],
            "src/App/Models/User.php": [],
            "src/App/Models/Order.php": [],
        })
        Resolver().resolve(results)
        assert results[0]["imports"][0]["resolved_file"] == "src/App/Models/User.php"
        assert results[0]["imports"][1]["resolved_file"] == "src/App/Models/Order.php"


# =========================================================================
# B. Require/include file imports (relative resolution)
# =========================================================================


class TestRequireImports:
    """Require/include with relative path resolution."""

    def test_relative_require(self):
        """`require './config.php'` → resolves relative to importing file"""
        results = make_parsed_results({
            "src/index.php": [
                {"source": "./config.php", "names": [], "line": 2},
            ],
            "src/config.php": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/config.php"

    def test_parent_relative_require(self):
        """`require '../lib/helpers.php'` → resolves up one level"""
        results = make_parsed_results({
            "src/controllers/main.php": [
                {"source": "../lib/helpers.php", "names": [], "line": 2},
            ],
            "src/lib/helpers.php": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/lib/helpers.php"

    def test_plain_require_suffix_match(self):
        """`require 'vendor/autoload.php'` → suffix match"""
        results = make_parsed_results({
            "public/index.php": [
                {"source": "vendor/autoload.php", "names": [], "line": 1},
            ],
            "vendor/autoload.php": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "vendor/autoload.php"


# =========================================================================
# C. External/stdlib → None
# =========================================================================


class TestExternalImports:
    """Stdlib and vendor-external imports resolve to None."""

    def test_pdo(self):
        """`use PDO` → no resolved_file (no backslash = not in file set)"""
        results = make_parsed_results({
            "src/index.php": [
                {"source": "PDO", "names": ["PDO"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_datetime(self):
        """`use DateTime` → no resolved_file"""
        results = make_parsed_results({
            "src/index.php": [
                {"source": "DateTime", "names": ["DateTime"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_vendor_package(self):
        """`use Illuminate\\Support\\Facades\\DB` → no resolved_file (not in file set)"""
        results = make_parsed_results({
            "src/index.php": [
                {"source": "Illuminate\\Support\\Facades\\DB", "names": ["DB"], "line": 1},
            ],
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
            "src/Controller/Home.php": [
                {"source": "App\\Models\\User", "names": ["User"], "line": 3},
                {"source": "./config.php", "names": [], "line": 5},
                {"source": "PDO", "names": ["PDO"], "line": 1},
            ],
            "src/App/Models/User.php": [],
            "src/Controller/config.php": [],
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
            "src/Controller/Home.php": [
                {"source": "App\\Models\\User", "names": ["User"], "line": 3},
                {"source": "./config.php", "names": [], "line": 5},
                {"source": "PDO", "names": ["PDO"], "line": 1},
                {"source": "Illuminate\\Support\\Facades\\DB", "names": ["DB"], "line": 2},
            ],
            "src/App/Models/User.php": [],
            "src/Controller/config.php": [],
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
