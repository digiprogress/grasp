"""
Contract tests for C# import resolution in the Resolver.

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
    """Build synthetic parsed_results for C# files."""
    return [
        {
            "file_path": path,
            "language": "c_sharp",
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
        """`MyProject.Utils.StringHelper` → MyProject/Utils/StringHelper.cs"""
        results = make_parsed_results({
            "MyProject/Program.cs": [
                {"source": "MyProject.Utils.StringHelper", "names": ["StringHelper"], "line": 1},
            ],
            "MyProject/Utils/StringHelper.cs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "MyProject/Utils/StringHelper.cs"

    def test_class_import_with_src_prefix(self):
        """`MyProject.Models.User` with src/main/csharp/ prefix → resolves via suffix"""
        results = make_parsed_results({
            "src/main/csharp/MyProject/Program.cs": [
                {"source": "MyProject.Models.User", "names": ["User"], "line": 1},
            ],
            "src/main/csharp/MyProject/Models/User.cs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/main/csharp/MyProject/Models/User.cs"

    def test_deep_path(self):
        """`Org.Example.A.B.C.Deep` with A/B/C/Deep.cs → resolves"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "Org.Example.A.B.C.Deep", "names": ["Deep"], "line": 1},
            ],
            "A/B/C/Deep.cs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "A/B/C/Deep.cs"


# =========================================================================
# B. Wildcard imports (directory matching)
# =========================================================================


class TestWildcardImports:
    """Wildcard imports resolve to first .cs file alphabetically in matched directory."""

    def test_wildcard_picks_first_alphabetically(self):
        """`MyProject.Utils.*` with Helper.cs and Parser.cs → Helper.cs"""
        results = make_parsed_results({
            "MyProject/Program.cs": [
                {"source": "MyProject.Utils", "names": ["*"], "line": 1},
            ],
            "Utils/Helper.cs": [],
            "Utils/Parser.cs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Utils/Helper.cs"

    def test_wildcard_single_file(self):
        """`MyProject.Models.*` with Models/User.cs → resolves"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "MyProject.Models", "names": ["*"], "line": 1},
            ],
            "Models/User.cs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "Models/User.cs"

    def test_wildcard_with_src_prefix(self):
        """`MyProject.Utils.*` with src/ prefix → resolves via suffix"""
        results = make_parsed_results({
            "src/MyProject/Program.cs": [
                {"source": "MyProject.Utils", "names": ["*"], "line": 1},
            ],
            "src/MyProject/Utils/Helper.cs": [],
            "src/MyProject/Utils/Parser.cs": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/MyProject/Utils/Helper.cs"


# =========================================================================
# C. Stdlib → None
# =========================================================================


class TestStdlibImports:
    """C# stdlib imports resolve to None (no resolved_file)."""

    def test_system_io(self):
        """`using System.IO;` → no resolved_file"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "System.IO", "names": ["*"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_system_collections_generic(self):
        """`using System.Collections.Generic;` → no resolved_file"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "System.Collections.Generic", "names": ["*"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_system_linq(self):
        """`using System.Linq;` → no resolved_file"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "System.Linq", "names": ["*"], "line": 1},
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

    def test_newtonsoft_json(self):
        """`using Newtonsoft.Json;` → no resolved_file"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "Newtonsoft.Json", "names": ["*"], "line": 1},
            ],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_microsoft_extensions(self):
        """`using Microsoft.Extensions.DependencyInjection;` → no resolved_file"""
        results = make_parsed_results({
            "Program.cs": [
                {"source": "Microsoft.Extensions.DependencyInjection", "names": ["*"], "line": 1},
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
            "MyProject/Program.cs": [
                {"source": "MyProject.Utils.StringHelper", "names": ["StringHelper"], "line": 1},
                {"source": "MyProject.Models", "names": ["*"], "line": 2},
                {"source": "System.IO", "names": ["*"], "line": 3},
            ],
            "MyProject/Utils/StringHelper.cs": [],
            "MyProject/Models/User.cs": [],
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
            "MyProject/Program.cs": [
                {"source": "MyProject.Utils.StringHelper", "names": ["StringHelper"], "line": 1},
                {"source": "MyProject.Models", "names": ["*"], "line": 2},
                {"source": "System.IO", "names": ["*"], "line": 3},
                {"source": "Newtonsoft.Json", "names": ["*"], "line": 4},
            ],
            "MyProject/Utils/StringHelper.cs": [],
            "MyProject/Models/User.cs": [],
            "MyProject/Models/Order.cs": [],
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
