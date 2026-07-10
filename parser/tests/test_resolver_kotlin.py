"""
Contract tests for Kotlin import resolution in the Resolver.

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
    """Build synthetic parsed_results for Kotlin files."""
    return [
        {
            "file_path": path,
            "language": "kotlin",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. Specific class imports (suffix matching)
# =========================================================================


class TestSpecificClassImports:
    """Fully qualified class imports resolved via suffix matching."""

    def test_simple_class_import(self):
        """`com.myproject.utils.StringHelper` → com/myproject/utils/StringHelper.kt"""
        results = make_parsed_results({
            "com/myproject/Main.kt": [
                {"source": "com.myproject.utils.StringHelper", "names": ["StringHelper"], "line": 1},
            ],
            "com/myproject/utils/StringHelper.kt": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "com/myproject/utils/StringHelper.kt"

    def test_class_import_with_src_prefix(self):
        """`com.myproject.models.User` with src/main/kotlin/ prefix → resolves via suffix"""
        results = make_parsed_results({
            "src/main/kotlin/com/myproject/Main.kt": [
                {"source": "com.myproject.models.User", "names": ["User"], "line": 1},
            ],
            "src/main/kotlin/com/myproject/models/User.kt": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/main/kotlin/com/myproject/models/User.kt"

    def test_deep_path(self):
        """`org.example.a.b.c.Deep` with a/b/c/Deep.kt → resolves"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "org.example.a.b.c.Deep", "names": ["Deep"], "line": 1},
            ],
            "a/b/c/Deep.kt": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "a/b/c/Deep.kt"


# =========================================================================
# B. Wildcard imports (directory matching)
# =========================================================================


class TestWildcardImports:
    """Wildcard imports resolve to first .kt file alphabetically in matched directory."""

    def test_wildcard_picks_first_alphabetically(self):
        """`com.myproject.utils.*` with Helper.kt and Parser.kt → Helper.kt"""
        results = make_parsed_results({
            "com/myproject/Main.kt": [
                {"source": "com.myproject.utils", "names": ["*"], "line": 1},
            ],
            "utils/Helper.kt": [],
            "utils/Parser.kt": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "utils/Helper.kt"

    def test_wildcard_single_file(self):
        """`com.myproject.models.*` with models/User.kt → resolves"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "com.myproject.models", "names": ["*"], "line": 1},
            ],
            "models/User.kt": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "models/User.kt"

    def test_wildcard_with_src_prefix(self):
        """`com.myproject.utils.*` with src/main/kotlin prefix → resolves via suffix"""
        results = make_parsed_results({
            "src/main/kotlin/com/myproject/Main.kt": [
                {"source": "com.myproject.utils", "names": ["*"], "line": 1},
            ],
            "src/main/kotlin/com/myproject/utils/Helper.kt": [],
            "src/main/kotlin/com/myproject/utils/Parser.kt": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/main/kotlin/com/myproject/utils/Helper.kt"


# =========================================================================
# C. Stdlib → None
# =========================================================================


class TestStdlibImports:
    """Kotlin stdlib imports resolve to None (no resolved_file)."""

    def test_kotlin_collections(self):
        """`import kotlin.collections.*` → no resolved_file"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "kotlin.collections", "names": ["*"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_kotlin_io(self):
        """`import kotlin.io.*` → no resolved_file"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "kotlin.io", "names": ["*"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_java_util_list(self):
        """`import java.util.List` → no resolved_file"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "java.util.List", "names": ["List"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# D. Third-party → None
# =========================================================================


class TestThirdPartyImports:
    """Third-party imports not in file set resolve to None."""

    def test_ktor_server(self):
        """`import io.ktor.server.*` → no resolved_file"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "io.ktor.server", "names": ["*"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_kotlinx_coroutines(self):
        """`import kotlinx.coroutines.*` → no resolved_file"""
        results = make_parsed_results({
            "Main.kt": [
                {"source": "kotlinx.coroutines", "names": ["*"], "line": 1},
            ],
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
            "com/myproject/Main.kt": [
                {"source": "com.myproject.utils.StringHelper", "names": ["StringHelper"], "line": 1},
                {"source": "com.myproject.models", "names": ["*"], "line": 2},
                {"source": "kotlin.collections", "names": ["*"], "line": 3},
            ],
            "com/myproject/utils/StringHelper.kt": [],
            "com/myproject/models/User.kt": [],
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
            "com/myproject/Main.kt": [
                {"source": "com.myproject.utils.StringHelper", "names": ["StringHelper"], "line": 1},
                {"source": "com.myproject.models", "names": ["*"], "line": 2},
                {"source": "kotlin.collections", "names": ["*"], "line": 3},
                {"source": "io.ktor.server", "names": ["*"], "line": 4},
            ],
            "com/myproject/utils/StringHelper.kt": [],
            "com/myproject/models/User.kt": [],
            "com/myproject/models/Order.kt": [],
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
