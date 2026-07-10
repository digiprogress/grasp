"""
Language Configuration Registry

Central registry for all supported languages. Each language declares its
extensions, keywords, config files, and import extractor method name.
Toggle `enabled` to control which languages are active.
"""

from dataclasses import dataclass
from typing import Dict, Optional, Tuple


@dataclass(frozen=True)
class LanguageConfig:
    name: str
    enabled: bool
    extensions: Tuple[str, ...]
    constructor_names: Tuple[str, ...] = ()
    destructor_names: Tuple[str, ...] = ()
    self_keywords: Tuple[str, ...] = ()
    super_keywords: Tuple[str, ...] = ()
    index_files: Tuple[str, ...] = ()
    has_path_aliases: bool = False
    path_alias_config_files: Tuple[str, ...] = ()
    import_extractor: str = "extract_imports_generic"
    import_resolver: str = ""  # function name in resolver_langs
    script_preprocessor: str = ""  # "vue", "svelte", "astro" — script extraction + parse as tsx


# =============================================================================
# Language Configs
# =============================================================================

TYPESCRIPT = LanguageConfig(
    name="typescript",
    enabled=True,
    extensions=(".ts",),
    constructor_names=("constructor",),
    destructor_names=("destroy", "dispose", "close", "cleanup", "componentWillUnmount"),
    self_keywords=("this",),
    super_keywords=("super",),
    index_files=("index.ts",),
    has_path_aliases=True,
    path_alias_config_files=("tsconfig.json", "tsconfig.base.json"),
    import_extractor="extract_imports_js_ts",
    import_resolver="resolve_js_ts",
)

TSX = LanguageConfig(
    name="tsx",
    enabled=True,
    extensions=(".tsx",),
    constructor_names=("constructor",),
    destructor_names=("destroy", "dispose", "close", "cleanup", "componentWillUnmount"),
    self_keywords=("this",),
    super_keywords=("super",),
    index_files=("index.tsx",),
    has_path_aliases=True,
    path_alias_config_files=("tsconfig.json", "tsconfig.base.json"),
    import_extractor="extract_imports_js_ts",
    import_resolver="resolve_js_ts",
)

JAVASCRIPT = LanguageConfig(
    name="javascript",
    enabled=True,
    extensions=(".js", ".jsx"),
    constructor_names=("constructor",),
    destructor_names=("destroy", "dispose", "close", "cleanup", "componentWillUnmount"),
    self_keywords=("this",),
    super_keywords=("super",),
    index_files=("index.js", "index.jsx"),
    has_path_aliases=True,
    path_alias_config_files=("jsconfig.json", "tsconfig.json"),
    import_extractor="extract_imports_js_ts",
    import_resolver="resolve_js_ts",
)

PYTHON = LanguageConfig(
    name="python",
    enabled=True,
    extensions=(".py",),
    constructor_names=("__init__",),
    destructor_names=("__del__",),
    self_keywords=("self",),
    super_keywords=("super",),
    index_files=("__init__.py",),
    import_extractor="extract_imports_python",
    import_resolver="resolve_python",
)

GO = LanguageConfig(
    name="go",
    enabled=True,
    extensions=(".go",),
    import_extractor="extract_imports_go",
    import_resolver="resolve_go",
)

RUST = LanguageConfig(
    name="rust",
    enabled=True,
    extensions=(".rs",),
    destructor_names=("drop",),
    self_keywords=("self",),
    super_keywords=("super",),
    import_extractor="extract_imports_rust",
    import_resolver="resolve_rust",
)

JAVA = LanguageConfig(
    name="java",
    enabled=True,
    extensions=(".java",),
    constructor_names=("constructor",),
    destructor_names=("finalize", "close"),
    self_keywords=("this",),
    super_keywords=("super",),
    import_extractor="extract_imports_java",
    import_resolver="resolve_java",
)

CSHARP = LanguageConfig(
    name="c_sharp",
    enabled=True,
    extensions=(".cs",),
    self_keywords=("this",),
    super_keywords=("base",),
    import_extractor="extract_imports_csharp",
    import_resolver="resolve_csharp",
)

KOTLIN = LanguageConfig(
    name="kotlin",
    enabled=True,
    extensions=(".kt", ".kts"),
    self_keywords=("this",),
    super_keywords=("super",),
    import_extractor="extract_imports_kotlin",
    import_resolver="resolve_kotlin",
)

SCALA = LanguageConfig(
    name="scala",
    enabled=True,
    extensions=(".scala",),
    self_keywords=("this",),
    super_keywords=("super",),
    import_extractor="extract_imports_scala",
    import_resolver="resolve_scala",
)

DART = LanguageConfig(
    name="dart",
    enabled=True,
    extensions=(".dart",),
    constructor_names=("this",),
    self_keywords=("this",),
    super_keywords=("super",),
    import_extractor="extract_imports_dart",
    import_resolver="resolve_dart",
)

SWIFT = LanguageConfig(
    name="swift",
    enabled=True,
    extensions=(".swift",),
    self_keywords=("self",),
    super_keywords=("super",),
    import_extractor="extract_imports_swift",
    import_resolver="resolve_swift",
)

PHP = LanguageConfig(
    name="php",
    enabled=True,
    extensions=(".php",),
    self_keywords=("$this",),
    super_keywords=("parent",),
    import_extractor="extract_imports_php",
    import_resolver="resolve_php",
)

RUBY = LanguageConfig(
    name="ruby",
    enabled=True,
    extensions=(".rb",),
    constructor_names=("initialize",),
    self_keywords=("self",),
    super_keywords=("super",),
    import_extractor="extract_imports_ruby",
    import_resolver="resolve_ruby",
)

C = LanguageConfig(
    name="c",
    enabled=True,
    extensions=(".c", ".h"),
    import_extractor="extract_imports_c",
    import_resolver="resolve_c",
)

CPP = LanguageConfig(
    name="cpp",
    enabled=True,
    extensions=(".cpp", ".hpp", ".cc", ".cxx"),
    constructor_names=("constructor",),
    destructor_names=("~destructor",),
    self_keywords=("this",),
    import_extractor="extract_imports_cpp",
    import_resolver="resolve_cpp",
)

VUE = LanguageConfig(
    name="vue",
    enabled=True,
    extensions=(".vue",),
    constructor_names=("constructor",),
    self_keywords=("this",),
    super_keywords=("super",),
    index_files=("index.vue",),
    has_path_aliases=True,
    path_alias_config_files=("tsconfig.json", "tsconfig.base.json"),
    import_extractor="extract_imports_js_ts",
    import_resolver="resolve_js_ts",
    script_preprocessor="vue",
)

SVELTE = LanguageConfig(
    name="svelte",
    enabled=True,
    extensions=(".svelte",),
    constructor_names=("constructor",),
    self_keywords=("this",),
    super_keywords=("super",),
    has_path_aliases=True,
    path_alias_config_files=("tsconfig.json", "tsconfig.base.json"),
    import_extractor="extract_imports_js_ts",
    import_resolver="resolve_js_ts",
    script_preprocessor="svelte",
)

ASTRO = LanguageConfig(
    name="astro",
    enabled=True,
    extensions=(".astro",),
    self_keywords=("this",),
    has_path_aliases=True,
    path_alias_config_files=("tsconfig.json", "tsconfig.base.json"),
    import_extractor="extract_imports_js_ts",
    import_resolver="resolve_js_ts",
    script_preprocessor="astro",
)


# =============================================================================
# Registry
# =============================================================================

ALL_LANGUAGES: Dict[str, LanguageConfig] = {
    cfg.name: cfg
    for cfg in [TYPESCRIPT, TSX, JAVASCRIPT, PYTHON, GO, RUST, JAVA,
                CSHARP, KOTLIN, SCALA, DART, SWIFT, PHP, RUBY, C, CPP,
                VUE, SVELTE, ASTRO]
}


def get_enabled_languages() -> Dict[str, LanguageConfig]:
    """Get only enabled language configs."""
    return {name: cfg for name, cfg in ALL_LANGUAGES.items() if cfg.enabled}


def get_config(language: str) -> Optional[LanguageConfig]:
    """Get config for a language name."""
    return ALL_LANGUAGES.get(language)


# =============================================================================
# Derived Constants (computed once at import time from enabled configs)
# =============================================================================

_all_configs = list(ALL_LANGUAGES.values())

SELF_KEYWORDS = frozenset(n for cfg in _all_configs for n in cfg.self_keywords)
SUPER_KEYWORDS = frozenset(n for cfg in _all_configs for n in cfg.super_keywords)
ENABLED_EXTENSIONS = tuple(ext for cfg in _all_configs for ext in cfg.extensions)
ENABLED_INDEX_FILES = tuple(idx for cfg in _all_configs for idx in cfg.index_files)

_alias_files: set = set()
for _cfg in _all_configs:
    if _cfg.has_path_aliases:
        _alias_files.update(_cfg.path_alias_config_files)
PATH_ALIAS_CONFIG_FILES = tuple(sorted(_alias_files))
HAS_PATH_ALIASES = bool(_alias_files)
