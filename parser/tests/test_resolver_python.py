"""
Contract tests for Python import resolution in the Resolver.

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
    """Build synthetic parsed_results.

    files_with_imports = {
        "pkg/__init__.py": [],
        "pkg/main.py": [{"source": ".utils", "names": ["foo"], "line": 1}],
        "pkg/utils.py": [],
    }
    """
    return [
        {
            "file_path": path,
            "language": "python",
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
    """from .X, from ..X, from ...X resolution."""

    def test_single_dot_module(self):
        """`from .utils import foo` → pkg/utils.py"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/main.py": [{"source": ".utils", "names": ["foo"], "line": 1}],
            "pkg/utils.py": [],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils.py"

    def test_double_dot_module(self):
        """`from ..models import User` → pkg/models.py (one level up from pkg/sub/)"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/models.py": [],
            "pkg/sub/__init__.py": [],
            "pkg/sub/views.py": [{"source": "..models", "names": ["User"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[3]["imports"][0]
        assert imp["resolved_file"] == "pkg/models.py"

    def test_double_dot_module_to_file_in_parent(self):
        """`from ..models import User` where models.py is in parent dir"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/models.py": [],
            "pkg/sub/__init__.py": [],
            "pkg/sub/views.py": [{"source": "..models", "names": ["User"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[3]["imports"][0]
        assert imp["resolved_file"] == "pkg/models.py"

    def test_triple_dot_module(self):
        """`from ...config import X` → a/config.py (two levels up from a/b/c/)"""
        results = make_parsed_results({
            "a/__init__.py": [],
            "a/config.py": [],
            "a/b/__init__.py": [],
            "a/b/c/__init__.py": [],
            "a/b/c/deep.py": [{"source": "...config", "names": ["X"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[4]["imports"][0]
        assert imp["resolved_file"] == "a/config.py"

    def test_relative_to_package_init(self):
        """`from .sub import thing` → pkg/sub/__init__.py when sub is a package"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/main.py": [{"source": ".sub", "names": ["thing"], "line": 1}],
            "pkg/sub/__init__.py": [],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "pkg/sub/__init__.py"

    def test_relative_prefers_file_over_package(self):
        """`from .utils import foo` → pkg/utils.py even if pkg/utils/__init__.py exists"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/main.py": [{"source": ".utils", "names": ["foo"], "line": 1}],
            "pkg/utils.py": [],
            "pkg/utils/__init__.py": [],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils.py"


# =========================================================================
# B. Package (directory) imports via __init__.py
# =========================================================================


class TestPackageImports:
    """`from .` and package directory resolution."""

    def test_bare_dot_resolves_to_submodule_file(self):
        """`from . import utils` → pkg/utils.py when utils.py exists"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/main.py": [{"source": ".", "names": ["utils"], "line": 1}],
            "pkg/utils.py": [],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils.py"

    def test_bare_dot_resolves_to_init_when_no_submodule(self):
        """`from . import something` → pkg/__init__.py when no submodule match"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/main.py": [{"source": ".", "names": ["something"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "pkg/__init__.py"

    def test_bare_dot_resolves_to_subpackage(self):
        """`from . import sub` → pkg/sub/__init__.py when sub is a package"""
        results = make_parsed_results({
            "pkg/__init__.py": [],
            "pkg/main.py": [{"source": ".", "names": ["sub"], "line": 1}],
            "pkg/sub/__init__.py": [],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "pkg/sub/__init__.py"

    def test_dotted_relative_to_package(self):
        """`from .pkg import X` where pkg/ has __init__.py"""
        results = make_parsed_results({
            "src/__init__.py": [],
            "src/main.py": [{"source": ".pkg", "names": ["X"], "line": 1}],
            "src/pkg/__init__.py": [],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "src/pkg/__init__.py"

    def test_bare_double_dot_resolves_to_submodule(self):
        """`from .. import helpers` → parent/helpers.py"""
        results = make_parsed_results({
            "parent/__init__.py": [],
            "parent/helpers.py": [],
            "parent/child/__init__.py": [],
            "parent/child/mod.py": [{"source": "..", "names": ["helpers"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[3]["imports"][0]
        assert imp["resolved_file"] == "parent/helpers.py"


# =========================================================================
# C. Absolute project-internal imports
# =========================================================================


class TestAbsoluteInternalImports:
    """from mypackage.utils import foo — absolute but in-project."""

    def test_absolute_to_module_file(self):
        """`from mypackage.utils import foo` → mypackage/utils.py"""
        results = make_parsed_results({
            "mypackage/__init__.py": [],
            "mypackage/utils.py": [],
            "mypackage/main.py": [
                {"source": "mypackage.utils", "names": ["foo"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[2]["imports"][0]
        assert imp["resolved_file"] == "mypackage/utils.py"

    def test_absolute_to_package_init(self):
        """`from mypackage import utils` → mypackage/utils.py or mypackage/__init__.py"""
        results = make_parsed_results({
            "mypackage/__init__.py": [],
            "mypackage/utils.py": [],
            "other.py": [
                {"source": "mypackage", "names": ["utils"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[2]["imports"][0]
        # Should resolve to mypackage/__init__.py (the package itself)
        assert imp["resolved_file"] == "mypackage/__init__.py"

    def test_absolute_deep_path(self):
        """`from mypackage.sub.deep import X` → mypackage/sub/deep.py"""
        results = make_parsed_results({
            "mypackage/__init__.py": [],
            "mypackage/sub/__init__.py": [],
            "mypackage/sub/deep.py": [],
            "caller.py": [
                {"source": "mypackage.sub.deep", "names": ["X"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[3]["imports"][0]
        assert imp["resolved_file"] == "mypackage/sub/deep.py"


# =========================================================================
# D. External / stdlib → None
# =========================================================================


class TestExternalImports:
    """Stdlib and third-party imports resolve to None (no resolved_file)."""

    def test_stdlib_import(self):
        """`import os` → no resolved_file"""
        results = make_parsed_results({
            "main.py": [{"source": "os", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_stdlib_from_import(self):
        """`from collections import OrderedDict` → no resolved_file"""
        results = make_parsed_results({
            "main.py": [{"source": "collections", "names": ["OrderedDict"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_third_party_import(self):
        """`import requests` → no resolved_file"""
        results = make_parsed_results({
            "main.py": [{"source": "requests", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_third_party_dotted(self):
        """`from flask.views import MethodView` → no resolved_file when not in file set"""
        results = make_parsed_results({
            "main.py": [{"source": "flask.views", "names": ["MethodView"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# E. Determinism
# =========================================================================


class TestDeterminism:
    """Running resolver twice on the same input yields identical output."""

    def test_deterministic_output(self):
        files = {
            "pkg/__init__.py": [],
            "pkg/main.py": [
                {"source": ".utils", "names": ["foo"], "line": 1},
                {"source": ".models", "names": ["Bar"], "line": 2},
                {"source": "os", "names": ["path"], "line": 3},
            ],
            "pkg/utils.py": [],
            "pkg/models.py": [],
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
            "pkg/__init__.py": [],
            "pkg/main.py": [
                {"source": ".utils", "names": ["foo"], "line": 1},
                {"source": ".sub", "names": ["thing"], "line": 2},
                {"source": "os", "names": ["path"], "line": 3},
                {"source": "pkg.models", "names": ["X"], "line": 4},
            ],
            "pkg/utils.py": [],
            "pkg/sub/__init__.py": [],
            "pkg/models.py": [],
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
