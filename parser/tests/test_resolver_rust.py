"""
Contract tests for Rust import resolution in the Resolver.

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
    """Build synthetic parsed_results for Rust files.

    files_with_imports = {
        "src/utils/helper.rs": [],
        "src/main.rs": [{"source": "crate::utils::helper", "names": ["helper"], "line": 1}],
    }
    """
    return [
        {
            "file_path": path,
            "language": "rust",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. crate:: imports — suffix matching
# =========================================================================


class TestCrateImports:
    """crate:: imports resolved via suffix matching."""

    def test_simple_crate_import(self):
        """`crate::utils::helper` with src/utils/helper.rs → resolves"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "crate::utils::helper", "names": ["helper"], "line": 1}],
            "src/utils/helper.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils/helper.rs"

    def test_deep_crate_path(self):
        """`crate::a::b::c` with src/a/b/c.rs → resolves"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "crate::a::b::c", "names": ["c"], "line": 1}],
            "src/a/b/c.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/a/b/c.rs"

    def test_crate_mod_rs_fallback(self):
        """`crate::utils` with src/utils/mod.rs → resolves to mod.rs"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "crate::utils", "names": ["utils"], "line": 1}],
            "src/utils/mod.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils/mod.rs"

    def test_crate_prefers_rs_over_mod(self):
        """`crate::config` with both config.rs and config/mod.rs → prefers .rs"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "crate::config", "names": ["config"], "line": 1}],
            "src/config.rs": [],
            "src/config/mod.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/config.rs"


# =========================================================================
# B. super:: imports — relative
# =========================================================================


class TestSuperImports:
    """super:: imports resolved relative to the importing file."""

    def test_super_from_regular_file(self):
        """From src/utils/helper.rs, `super::parser` → src/utils/parser.rs"""
        results = make_parsed_results({
            "src/utils/helper.rs": [{"source": "super::parser", "names": ["parser"], "line": 1}],
            "src/utils/parser.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils/parser.rs"

    def test_super_from_mod_rs(self):
        """From src/utils/mod.rs, `super::config` → src/config.rs (mod.rs base = parent of parent)"""
        results = make_parsed_results({
            "src/utils/mod.rs": [{"source": "super::config", "names": ["config"], "line": 1}],
            "src/config.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/config.rs"

    def test_super_deeply_nested(self):
        """From src/a/b/c.rs, `super::d` → src/a/b/d.rs"""
        results = make_parsed_results({
            "src/a/b/c.rs": [{"source": "super::d", "names": ["d"], "line": 1}],
            "src/a/b/d.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/a/b/d.rs"

    def test_double_super(self):
        """From src/a/b/c.rs, `super::super::x` → src/a/x.rs"""
        results = make_parsed_results({
            "src/a/b/c.rs": [{"source": "super::super::x", "names": ["x"], "line": 1}],
            "src/a/x.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/a/x.rs"


# =========================================================================
# C. self:: imports — current module
# =========================================================================


class TestSelfImports:
    """self:: imports resolved within the current module."""

    def test_self_from_mod_rs(self):
        """From src/utils/mod.rs, `self::helper` → src/utils/helper.rs"""
        results = make_parsed_results({
            "src/utils/mod.rs": [{"source": "self::helper", "names": ["helper"], "line": 1}],
            "src/utils/helper.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils/helper.rs"

    def test_self_from_lib_rs(self):
        """From src/lib.rs, `self::config` → src/config.rs"""
        results = make_parsed_results({
            "src/lib.rs": [{"source": "self::config", "names": ["config"], "line": 1}],
            "src/config.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/config.rs"

    def test_self_mod_rs_fallback(self):
        """From src/lib.rs, `self::handlers` → src/handlers/mod.rs (mod.rs fallback)"""
        results = make_parsed_results({
            "src/lib.rs": [{"source": "self::handlers", "names": ["handlers"], "line": 1}],
            "src/handlers/mod.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/handlers/mod.rs"


# =========================================================================
# D. Wildcard imports
# =========================================================================


class TestWildcardImports:
    """Wildcard imports resolve to first .rs file alphabetically."""

    def test_crate_wildcard(self):
        """`crate::utils::*` with multiple files → first .rs alphabetically"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "crate::utils", "names": ["*crate::utils"], "line": 1}],
            "src/utils/parser.rs": [],
            "src/utils/helper.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils/helper.rs"

    def test_self_wildcard(self):
        """`self::models::*` from src/lib.rs → first .rs alphabetically"""
        results = make_parsed_results({
            "src/lib.rs": [{"source": "self::models", "names": ["*self::models"], "line": 1}],
            "src/models/user.rs": [],
            "src/models/order.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/models/order.rs"

    def test_super_wildcard(self):
        """`super::types::*` from src/utils/helper.rs → first .rs alphabetically"""
        results = make_parsed_results({
            "src/utils/helper.rs": [{"source": "super::types", "names": ["*super::types"], "line": 1}],
            "src/utils/types/string_type.rs": [],
            "src/utils/types/int_type.rs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils/types/int_type.rs"


# =========================================================================
# E. Stdlib → None
# =========================================================================


class TestStdlibImports:
    """Rust stdlib imports resolve to None (no resolved_file)."""

    def test_std_io(self):
        """`use std::io;` → no resolved_file"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "std::io", "names": ["io"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_std_collections(self):
        """`use std::collections::HashMap;` → no resolved_file"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "std::collections::HashMap", "names": ["HashMap"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_std_fmt(self):
        """`use std::fmt;` → no resolved_file"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "std::fmt", "names": ["fmt"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# F. Third-party → None
# =========================================================================


class TestThirdPartyImports:
    """Third-party imports not in file set resolve to None."""

    def test_tokio(self):
        """`use tokio::runtime;` → no resolved_file"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "tokio::runtime", "names": ["runtime"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_serde(self):
        """`use serde::Deserialize;` → no resolved_file"""
        results = make_parsed_results({
            "src/main.rs": [{"source": "serde::Deserialize", "names": ["Deserialize"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# G. Determinism
# =========================================================================


class TestDeterminism:
    """Running resolver twice on the same input yields identical output."""

    def test_deterministic_output(self):
        files = {
            "src/main.rs": [
                {"source": "crate::utils::helper", "names": ["helper"], "line": 1},
                {"source": "super::config", "names": ["config"], "line": 2},
                {"source": "std::io", "names": ["io"], "line": 3},
            ],
            "src/utils/helper.rs": [],
            "src/config.rs": [],
        }
        results_a = make_parsed_results(copy.deepcopy(files))
        results_b = make_parsed_results(copy.deepcopy(files))

        Resolver().resolve(results_a)
        Resolver().resolve(results_b)

        assert results_a == results_b


# =========================================================================
# H. Closed-world validation
# =========================================================================


class TestClosedWorld:
    """Every resolved_file must exist in the input file set."""

    def test_all_resolved_files_in_file_set(self):
        files = {
            "src/main.rs": [
                {"source": "crate::utils::helper", "names": ["helper"], "line": 1},
                {"source": "crate::utils", "names": ["*crate::utils"], "line": 2},
                {"source": "std::io", "names": ["io"], "line": 3},
                {"source": "tokio::runtime", "names": ["runtime"], "line": 4},
            ],
            "src/utils/helper.rs": [],
            "src/utils/parser.rs": [],
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
