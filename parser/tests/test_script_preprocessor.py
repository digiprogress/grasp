"""Tests for script extraction preprocessing (Vue, Svelte, Astro)."""

import importlib
import os
import sys
import types

import pytest

# ---------------------------------------------------------------------------
# Bootstrap: load modules without triggering modules/__init__.py chain.
# ---------------------------------------------------------------------------
_modules_dir = os.path.join(os.path.dirname(__file__), os.pardir, "modules")
_parser_dir = os.path.join(_modules_dir, "parser")

# 1) Register bare `modules` package so `from modules.lang import ...` works
if "modules" not in sys.modules:
    _pkg = types.ModuleType("modules")
    _pkg.__path__ = [os.path.abspath(_modules_dir)]
    sys.modules["modules"] = _pkg

# 2) Load modules.lang
if "modules.lang" not in sys.modules:
    _lang_spec = importlib.util.spec_from_file_location(
        "modules.lang", os.path.join(_modules_dir, "lang.py"),
    )
    _lang_mod = importlib.util.module_from_spec(_lang_spec)
    sys.modules["modules.lang"] = _lang_mod
    _lang_spec.loader.exec_module(_lang_mod)

# 3) Register modules.parser package
if "modules.parser" not in sys.modules:
    _parser_pkg = types.ModuleType("modules.parser")
    _parser_pkg.__path__ = [os.path.abspath(_parser_dir)]
    sys.modules["modules.parser"] = _parser_pkg

# 4) Load submodules of modules.parser that base.py imports
for _name, _file in [
    ("modules.parser.languages", "languages.py"),
    ("modules.parser.result", "result.py"),
    ("modules.parser.queries", "queries.py"),
    ("modules.parser.import_extractors", "import_extractors.py"),
    ("modules.parser.query_extractor", "query_extractor.py"),
]:
    if _name not in sys.modules:
        _spec = importlib.util.spec_from_file_location(
            _name, os.path.join(_parser_dir, _file),
        )
        _submod = importlib.util.module_from_spec(_spec)
        sys.modules[_name] = _submod
        _spec.loader.exec_module(_submod)

# 5) Now load base.py
if "modules.parser.base" not in sys.modules:
    _base_spec = importlib.util.spec_from_file_location(
        "modules.parser.base", os.path.join(_parser_dir, "base.py"),
    )
    _base_mod = importlib.util.module_from_spec(_base_spec)
    sys.modules["modules.parser.base"] = _base_mod
    _base_spec.loader.exec_module(_base_mod)

_blank_non_script = sys.modules["modules.parser.base"]._blank_non_script
_lang_mod = sys.modules["modules.lang"]


# ===========================================================================
# Vue tests
# ===========================================================================

class TestVuePreprocessor:
    """Vue <script> extraction."""

    def test_basic_script_block(self):
        content = "\n".join([
            "<template>",
            "  <div>Hello</div>",
            "</template>",
            "<script>",
            "import { ref } from 'vue'",
            "const count = ref(0)",
            "</script>",
            "<style>",
            ".red { color: red; }",
            "</style>",
        ])
        result = _blank_non_script(content, "vue")
        lines = result.split("\n")
        assert lines[0] == ""
        assert lines[1] == ""
        assert lines[2] == ""
        assert lines[3] == ""  # <script> tag blanked
        assert lines[4] == "import { ref } from 'vue'"
        assert lines[5] == "const count = ref(0)"
        assert lines[6] == ""  # </script> tag blanked
        assert lines[7] == ""
        assert lines[8] == ""
        assert lines[9] == ""

    def test_script_setup_lang_ts(self):
        content = "\n".join([
            "<template>",
            "  <div>{{ msg }}</div>",
            "</template>",
            '<script setup lang="ts">',
            "import { ref } from 'vue'",
            "const msg = ref('hello')",
            "</script>",
        ])
        result = _blank_non_script(content, "vue")
        lines = result.split("\n")
        assert lines[3] == ""  # <script setup lang="ts"> tag blanked
        assert lines[4] == "import { ref } from 'vue'"
        assert lines[5] == "const msg = ref('hello')"

    def test_multiple_script_blocks(self):
        """Vue can have <script> + <script setup> in same file."""
        content = "\n".join([
            "<script>",
            "export default { name: 'Foo' }",
            "</script>",
            "<script setup>",
            "import { ref } from 'vue'",
            "</script>",
            "<template>",
            "  <div />",
            "</template>",
        ])
        result = _blank_non_script(content, "vue")
        lines = result.split("\n")
        assert lines[0] == ""
        assert lines[1] == "export default { name: 'Foo' }"
        assert lines[2] == ""
        assert lines[3] == ""
        assert lines[4] == "import { ref } from 'vue'"
        assert lines[5] == ""
        assert lines[6] == ""
        assert lines[7] == ""
        assert lines[8] == ""

    def test_line_count_preserved(self):
        content = "\n".join([
            "<template>",
            "  <div>line2</div>",
            "  <div>line3</div>",
            "</template>",
            "<script>",
            "const x = 1",
            "</script>",
        ])
        result = _blank_non_script(content, "vue")
        assert len(result.split("\n")) == len(content.split("\n"))

    def test_no_script_block(self):
        content = "\n".join([
            "<template>",
            "  <div>Hello</div>",
            "</template>",
            "<style>",
            ".red { color: red; }",
            "</style>",
        ])
        result = _blank_non_script(content, "vue")
        lines = result.split("\n")
        assert all(line == "" for line in lines)

    def test_empty_file(self):
        result = _blank_non_script("", "vue")
        assert result == ""

    def test_indented_content_preserved(self):
        content = "\n".join([
            "<script>",
            "  import { ref } from 'vue'",
            "  const x = 1",
            "</script>",
        ])
        result = _blank_non_script(content, "vue")
        lines = result.split("\n")
        assert lines[1] == "  import { ref } from 'vue'"
        assert lines[2] == "  const x = 1"


# ===========================================================================
# Svelte tests
# ===========================================================================

class TestSveltePreprocessor:
    """Svelte <script> extraction."""

    def test_basic_script_block(self):
        content = "\n".join([
            '<script lang="ts">',
            "import { onMount } from 'svelte'",
            "let count = 0",
            "</script>",
            "",
            "<div>{count}</div>",
            "",
            "<style>",
            "  div { color: red; }",
            "</style>",
        ])
        result = _blank_non_script(content, "svelte")
        lines = result.split("\n")
        assert lines[0] == ""  # <script> tag
        assert lines[1] == "import { onMount } from 'svelte'"
        assert lines[2] == "let count = 0"
        assert lines[3] == ""  # </script>
        assert lines[4] == ""
        assert lines[5] == ""
        assert lines[6] == ""
        assert lines[7] == ""

    def test_module_context_script(self):
        """Svelte <script context="module"> block."""
        content = "\n".join([
            '<script context="module">',
            "export const x = 1",
            "</script>",
            "<script>",
            "let y = 2",
            "</script>",
            "<div>{y}</div>",
        ])
        result = _blank_non_script(content, "svelte")
        lines = result.split("\n")
        assert lines[1] == "export const x = 1"
        assert lines[4] == "let y = 2"
        assert lines[6] == ""

    def test_line_count_preserved(self):
        content = "\n".join([
            "<script>",
            "const a = 1",
            "const b = 2",
            "</script>",
            "<div>",
            "  <span>text</span>",
            "</div>",
        ])
        result = _blank_non_script(content, "svelte")
        assert len(result.split("\n")) == len(content.split("\n"))


# ===========================================================================
# Astro tests
# ===========================================================================

class TestAstroPreprocessor:
    """Astro frontmatter + <script> extraction."""

    def test_frontmatter_only(self):
        content = "\n".join([
            "---",
            "import Layout from '../layouts/Main.astro'",
            "const title = 'Hello'",
            "---",
            "<Layout title={title}>",
            "  <h1>{title}</h1>",
            "</Layout>",
        ])
        result = _blank_non_script(content, "astro")
        lines = result.split("\n")
        assert lines[0] == ""  # ---
        assert lines[1] == "import Layout from '../layouts/Main.astro'"
        assert lines[2] == "const title = 'Hello'"
        assert lines[3] == ""  # ---
        assert lines[4] == ""
        assert lines[5] == ""
        assert lines[6] == ""

    def test_frontmatter_and_script(self):
        """Astro can have both frontmatter and <script> blocks."""
        content = "\n".join([
            "---",
            "import Layout from '../layouts/Main.astro'",
            "---",
            "<Layout>",
            "  <h1>Hello</h1>",
            "</Layout>",
            "<script>",
            "document.addEventListener('click', () => {})",
            "</script>",
        ])
        result = _blank_non_script(content, "astro")
        lines = result.split("\n")
        assert lines[0] == ""
        assert lines[1] == "import Layout from '../layouts/Main.astro'"
        assert lines[2] == ""
        assert lines[3] == ""
        assert lines[4] == ""
        assert lines[5] == ""
        assert lines[6] == ""  # <script> tag
        assert lines[7] == "document.addEventListener('click', () => {})"
        assert lines[8] == ""  # </script>

    def test_no_frontmatter(self):
        content = "\n".join([
            "<html>",
            "  <body>Hello</body>",
            "</html>",
        ])
        result = _blank_non_script(content, "astro")
        lines = result.split("\n")
        assert all(line == "" for line in lines)

    def test_line_count_preserved(self):
        content = "\n".join([
            "---",
            "const x = 1",
            "---",
            "<div>",
            "  <p>hello</p>",
            "</div>",
            "<script>",
            "console.log('hi')",
            "</script>",
        ])
        result = _blank_non_script(content, "astro")
        assert len(result.split("\n")) == len(content.split("\n"))

    def test_empty_frontmatter(self):
        content = "\n".join([
            "---",
            "---",
            "<div>Hello</div>",
        ])
        result = _blank_non_script(content, "astro")
        lines = result.split("\n")
        assert lines[0] == ""
        assert lines[1] == ""
        assert lines[2] == ""


# ===========================================================================
# Config tests
# ===========================================================================

class TestLangConfig:
    """Verify lang.py configs are correctly registered."""

    def test_vue_config(self):
        cfg = _lang_mod.get_config("vue")
        assert cfg is not None
        assert cfg.enabled is True
        assert cfg.extensions == (".vue",)
        assert cfg.script_preprocessor == "vue"
        assert cfg.import_extractor == "extract_imports_js_ts"
        assert cfg.import_resolver == "resolve_js_ts"

    def test_svelte_config(self):
        cfg = _lang_mod.get_config("svelte")
        assert cfg is not None
        assert cfg.enabled is True
        assert cfg.extensions == (".svelte",)
        assert cfg.script_preprocessor == "svelte"
        assert cfg.import_extractor == "extract_imports_js_ts"

    def test_astro_config(self):
        cfg = _lang_mod.get_config("astro")
        assert cfg is not None
        assert cfg.enabled is True
        assert cfg.extensions == (".astro",)
        assert cfg.script_preprocessor == "astro"
        assert cfg.import_extractor == "extract_imports_js_ts"

    def test_existing_langs_no_preprocessor(self):
        for lang in ["typescript", "tsx", "javascript", "python", "go"]:
            cfg = _lang_mod.get_config(lang)
            assert cfg.script_preprocessor == ""

    def test_new_extensions_registered(self):
        assert ".vue" in _lang_mod.ENABLED_EXTENSIONS
        assert ".svelte" in _lang_mod.ENABLED_EXTENSIONS
        assert ".astro" in _lang_mod.ENABLED_EXTENSIONS


class TestLanguageMap:
    """Verify languages.py extension mapping."""

    def test_astro_extension(self):
        from modules.parser.languages import LANGUAGE_MAP
        assert LANGUAGE_MAP[".astro"] == "astro"

    def test_vue_extension(self):
        from modules.parser.languages import LANGUAGE_MAP
        assert LANGUAGE_MAP[".vue"] == "vue"

    def test_svelte_extension(self):
        from modules.parser.languages import LANGUAGE_MAP
        assert LANGUAGE_MAP[".svelte"] == "svelte"
