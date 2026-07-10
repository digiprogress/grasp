"""
Contract tests for TypeScript/JS import resolution in the Resolver.

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


def make_parsed_results(files_with_imports, language="typescript"):
    """Build synthetic parsed_results for TS/JS files.

    files_with_imports = {
        "src/index.ts": [],
        "src/main.ts": [{"source": "./utils", "names": ["foo"], "line": 1}],
        "src/utils.ts": [],
    }
    """
    return [
        {
            "file_path": path,
            "language": language,
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
    """./foo, ../bar resolution."""

    def test_relative_sibling(self):
        """`import { foo } from './utils'` → src/utils.ts"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "./utils", "names": ["foo"], "line": 1}],
            "src/utils.ts": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils.ts"

    def test_relative_parent(self):
        """`import { Bar } from '../models'` → models.ts one level up"""
        results = make_parsed_results({
            "src/models.ts": [],
            "src/sub/views.ts": [{"source": "../models", "names": ["Bar"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "src/models.ts"

    def test_relative_grandparent(self):
        """`import { X } from '../../config'` → config.ts two levels up"""
        results = make_parsed_results({
            "src/config.ts": [],
            "src/a/b/deep.ts": [{"source": "../../config", "names": ["X"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[1]["imports"][0]
        assert imp["resolved_file"] == "src/config.ts"

    def test_relative_with_extension_already(self):
        """`import './styles.css'` — non-ts extension, no match"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "./styles.css", "names": [], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# B. Index file resolution
# =========================================================================


class TestIndexFiles:
    """./components → ./components/index.ts"""

    def test_directory_resolves_to_index_ts(self):
        """`import { Button } from './components'` → components/index.ts"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "./components", "names": ["Button"], "line": 1}],
            "src/components/index.ts": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/components/index.ts"

    def test_directory_resolves_to_index_tsx(self):
        """`import { App } from './app'` → app/index.tsx"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "./app", "names": ["App"], "line": 1}],
            "src/app/index.tsx": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/app/index.tsx"

    def test_file_preferred_over_index(self):
        """`import './utils'` → utils.ts preferred over utils/index.ts"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "./utils", "names": ["foo"], "line": 1}],
            "src/utils.ts": [],
            "src/utils/index.ts": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils.ts"


# =========================================================================
# C. Extension inference
# =========================================================================


class TestExtensionInference:
    """./foo → ./foo.ts, ./foo.tsx, ./foo.js, etc."""

    def test_infers_ts_extension(self):
        results = make_parsed_results({
            "src/main.ts": [{"source": "./helper", "names": ["help"], "line": 1}],
            "src/helper.ts": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/helper.ts"

    def test_infers_tsx_extension(self):
        results = make_parsed_results({
            "src/main.ts": [{"source": "./Button", "names": ["Button"], "line": 1}],
            "src/Button.tsx": [],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/Button.tsx"


# =========================================================================
# D. External / bare imports → None
# =========================================================================


class TestExternalImports:
    """Bare specifiers (node_modules) resolve to None."""

    def test_bare_import(self):
        """`import React from 'react'` → no resolved_file"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "react", "names": ["React"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_scoped_package(self):
        """`import { Injectable } from '@nestjs/common'` → no resolved_file"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "@nestjs/common", "names": ["Injectable"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp

    def test_node_builtin(self):
        """`import fs from 'fs'` → no resolved_file"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "fs", "names": ["default"], "line": 1}],
        })
        Resolver().resolve(results)
        imp = results[0]["imports"][0]
        assert "resolved_file" not in imp


# =========================================================================
# E. Path alias resolution
# =========================================================================


class TestPathAliases:
    """@/foo, ~/bar aliases (simulating tsconfig paths)."""

    def test_at_alias(self):
        """`import { foo } from '@/utils'` → src/utils.ts"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "@/utils", "names": ["foo"], "line": 1}],
            "src/utils.ts": [],
        })
        resolver = Resolver()
        resolver.path_aliases = {"@/": "src/"}
        resolver.resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/utils.ts"

    def test_tilde_alias(self):
        """`import { bar } from '~/components/Button'` → src/components/Button.tsx"""
        results = make_parsed_results({
            "src/app.ts": [{"source": "~/components/Button", "names": ["bar"], "line": 1}],
            "src/components/Button.tsx": [],
        })
        resolver = Resolver()
        resolver.path_aliases = {"~/": "src/"}
        resolver.resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/components/Button.tsx"

    def test_alias_to_index_file(self):
        """`import { Layout } from '@/components'` → src/components/index.ts"""
        results = make_parsed_results({
            "src/main.ts": [{"source": "@/components", "names": ["Layout"], "line": 1}],
            "src/components/index.ts": [],
        })
        resolver = Resolver()
        resolver.path_aliases = {"@/": "src/"}
        resolver.resolve(results)
        imp = results[0]["imports"][0]
        assert imp["resolved_file"] == "src/components/index.ts"


# =========================================================================
# F. Determinism
# =========================================================================


class TestDeterminism:
    """Running resolver twice on the same input yields identical output."""

    def test_deterministic_output(self):
        files = {
            "src/main.ts": [
                {"source": "./utils", "names": ["foo"], "line": 1},
                {"source": "./models", "names": ["Bar"], "line": 2},
                {"source": "react", "names": ["React"], "line": 3},
            ],
            "src/utils.ts": [],
            "src/models.ts": [],
        }
        results_a = make_parsed_results(copy.deepcopy(files))
        results_b = make_parsed_results(copy.deepcopy(files))

        Resolver().resolve(results_a)
        Resolver().resolve(results_b)

        assert results_a == results_b


# =========================================================================
# G. Closed-world validation
# =========================================================================


class TestClosedWorld:
    """Every resolved_file must exist in the input file set."""

    def test_all_resolved_files_in_file_set(self):
        files = {
            "src/main.ts": [
                {"source": "./utils", "names": ["foo"], "line": 1},
                {"source": "./components", "names": ["Button"], "line": 2},
                {"source": "react", "names": ["React"], "line": 3},
            ],
            "src/utils.ts": [],
            "src/components/index.ts": [],
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
