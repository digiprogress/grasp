"""
Contract tests for Go import resolution in the Resolver.

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
    """Build synthetic parsed_results for Go files.

    files_with_imports = {
        "pkg/utils/utils.go": [],
        "cmd/main.go": [{"source": "mymodule/pkg/utils", "names": ["utils"], "line": 3}],
    }
    """
    return [
        {
            "file_path": path,
            "language": "go",
            "imports": imports,
            "functions": [],
            "classes": [],
            "methods": [],
        }
        for path, imports in files_with_imports.items()
    ]


# =========================================================================
# A. Project-internal imports (suffix matching)
# =========================================================================


class TestProjectInternalImports:
    """Suffix-matched project-internal Go imports."""

    def test_simple_suffix_match(self):
        """`"mymodule/pkg/utils"` with pkg/utils/utils.go → resolves"""
        results = make_parsed_results({
            "cmd/main.go": [{"source": "mymodule/pkg/utils", "names": ["utils"], "line": 3}],
            "pkg/utils/utils.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils/utils.go"

    def test_internal_package(self):
        """`"mymodule/internal/config"` with internal/config/config.go → resolves"""
        results = make_parsed_results({
            "cmd/main.go": [{"source": "mymodule/internal/config", "names": ["config"], "line": 3}],
            "internal/config/config.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "internal/config/config.go"

    def test_deep_path(self):
        """`"mymodule/a/b/c"` → matches a/b/c/main.go"""
        results = make_parsed_results({
            "cmd/main.go": [{"source": "mymodule/a/b/c", "names": ["c"], "line": 3}],
            "a/b/c/main.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "a/b/c/main.go"

    def test_github_style_prefix(self):
        """`"github.com/myorg/proj/pkg/utils"` → matches pkg/utils/utils.go"""
        results = make_parsed_results({
            "cmd/main.go": [
                {"source": "github.com/myorg/proj/pkg/utils", "names": ["utils"], "line": 3},
            ],
            "pkg/utils/utils.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils/utils.go"

    def test_shortest_suffix_wins(self):
        """Longest matching suffix is preferred (first match from full path)."""
        results = make_parsed_results({
            "cmd/main.go": [
                {"source": "github.com/myorg/proj/pkg/utils", "names": ["utils"], "line": 3},
            ],
            "proj/pkg/utils/utils.go": [],
            "pkg/utils/utils.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        # "myorg/proj/pkg/utils" matches proj/pkg/utils/ first (longer suffix)
        assert imp["resolved_file"] == "proj/pkg/utils/utils.go"


# =========================================================================
# B. Directory with multiple .go files
# =========================================================================


class TestMultipleGoFiles:
    """Go package = directory; pick first .go file alphabetically."""

    def test_picks_first_alphabetically(self):
        """`"mymodule/pkg/utils"` with helpers.go and types.go → helpers.go"""
        results = make_parsed_results({
            "cmd/main.go": [{"source": "mymodule/pkg/utils", "names": ["utils"], "line": 3}],
            "pkg/utils/types.go": [],
            "pkg/utils/helpers.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils/helpers.go"

    def test_picks_first_with_many_files(self):
        """`"mymodule/pkg/utils"` with a.go, b.go, z.go → a.go"""
        results = make_parsed_results({
            "cmd/main.go": [{"source": "mymodule/pkg/utils", "names": ["utils"], "line": 3}],
            "pkg/utils/z.go": [],
            "pkg/utils/b.go": [],
            "pkg/utils/a.go": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "pkg/utils/a.go"


# =========================================================================
# C. Stdlib → None
# =========================================================================


class TestStdlibImports:
    """Go stdlib imports resolve to None (no resolved_file)."""

    def test_fmt(self):
        """`import "fmt"` → no resolved_file"""
        results = make_parsed_results({
            "main.go": [{"source": "fmt", "names": ["fmt"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_net_http(self):
        """`import "net/http"` → no resolved_file"""
        results = make_parsed_results({
            "main.go": [{"source": "net/http", "names": ["http"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_encoding_json(self):
        """`import "encoding/json"` → no resolved_file"""
        results = make_parsed_results({
            "main.go": [{"source": "encoding/json", "names": ["json"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_os(self):
        """`import "os"` → no resolved_file"""
        results = make_parsed_results({
            "main.go": [{"source": "os", "names": ["os"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# D. Third-party → None
# =========================================================================


class TestThirdPartyImports:
    """Third-party imports not in file set resolve to None."""

    def test_gin(self):
        """`import "github.com/gin-gonic/gin"` → no resolved_file"""
        results = make_parsed_results({
            "main.go": [{"source": "github.com/gin-gonic/gin", "names": ["gin"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_golang_x(self):
        """`import "golang.org/x/text"` → no resolved_file"""
        results = make_parsed_results({
            "main.go": [{"source": "golang.org/x/text", "names": ["text"], "line": 1}],
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
            "cmd/main.go": [
                {"source": "mymodule/pkg/utils", "names": ["utils"], "line": 3},
                {"source": "mymodule/internal/config", "names": ["config"], "line": 4},
                {"source": "fmt", "names": ["fmt"], "line": 1},
            ],
            "pkg/utils/utils.go": [],
            "internal/config/config.go": [],
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
            "cmd/main.go": [
                {"source": "mymodule/pkg/utils", "names": ["utils"], "line": 3},
                {"source": "fmt", "names": ["fmt"], "line": 1},
                {"source": "github.com/gin-gonic/gin", "names": ["gin"], "line": 5},
                {"source": "mymodule/internal/config", "names": ["config"], "line": 4},
            ],
            "pkg/utils/helpers.go": [],
            "pkg/utils/types.go": [],
            "internal/config/config.go": [],
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
