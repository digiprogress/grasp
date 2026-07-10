"""
Language-specific import resolvers and shared path utilities.

Extracted from resolver.py to keep the core orchestration thin.
All public resolver functions use a unified signature:

    def resolve_<lang>(from_file: str, source: str, imp: dict, all_files: set, **ctx) -> Optional[str]

- from_file: the importing file's path
- source: the import source string
- imp: the full import dict (names, kind, etc.)
- all_files: set of all known file paths
- **ctx: dynamic context (e.g. dart_package_name, resolve_import_path)
"""

import os
import logging
from typing import Dict, List, Optional, Set

logger = logging.getLogger(__name__)


# ── Shared resolver utilities ──────────────────────────────────────

def build_dir_files_map(all_files, ext):
    """Build a map of directory → [files] filtered by extension."""
    dir_to_files = {}
    for f in all_files:
        if f.endswith(ext):
            d = f.rsplit("/", 1)[0] if "/" in f else ""
            dir_to_files.setdefault(d, []).append(f)
    return dir_to_files


def find_dir_by_suffix(suffix_path, dir_to_files):
    """
    Progressive suffix matching against directory keys.
    Returns the first file (sorted) in the first matched directory.
    """
    segments = suffix_path.split("/")
    for start in range(len(segments)):
        suffix = "/".join(segments[start:])
        for d in sorted(dir_to_files):
            if d == suffix or d.endswith("/" + suffix):
                return sorted(dir_to_files[d])[0]
    return None


def find_file_by_suffix(base_suffix, all_files, extensions=None):
    """
    Progressive suffix matching against sorted file paths.

    If extensions is given, appends each to the suffix at each progressive
    level (preserving interleaved priority).  Otherwise matches base_suffix
    directly as a complete file-path suffix.
    """
    sorted_files = sorted(all_files)
    segments = base_suffix.split("/")

    if extensions is None:
        for start in range(len(segments)):
            suffix = "/".join(segments[start:])
            for f in sorted_files:
                if f == suffix or f.endswith("/" + suffix):
                    return f
        return None

    for start in range(len(segments)):
        s = "/".join(segments[start:])
        for ext in extensions:
            target = s + ext
            for f in sorted_files:
                if f == target or f.endswith("/" + target):
                    return f
    return None


def try_candidates(base, extensions, index_files, all_files):
    """
    Try base path, then base+ext for each extension, then base/index_file.
    Returns the first match found in all_files.
    """
    if base in all_files:
        return base
    for ext in extensions:
        candidate = base + ext
        if candidate in all_files:
            return candidate
    for idx_file in index_files:
        candidate = base + "/" + idx_file
        if candidate in all_files:
            return candidate
    return None


# ── Language-specific resolvers (unified signature) ──────────────


def resolve_python(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a Python import source to a file path.

    Handles:
    - Relative imports: .utils, ..models, ...config
    - Bare relative imports: from . import foo (source is only dots)
    - Absolute project-internal: mypackage.utils (matched against file set)
    - External/stdlib: returns None
    """
    if not source:
        return None

    # Count leading dots
    dots = 0
    for ch in source:
        if ch == ".":
            dots += 1
        else:
            break

    remainder = source[dots:]  # e.g. "utils", "models.core", ""

    if dots > 0:
        # --- Relative import ---
        # Start from the importing file's directory, go up (dots - 1) levels
        base = os.path.dirname(from_file)
        for _ in range(dots - 1):
            base = os.path.dirname(base)

        # Convert dotted remainder to path segments
        if remainder:
            tail = remainder.replace(".", "/")
            candidate_base = os.path.join(base, tail) if base else tail
        else:
            # source is only dots (e.g. "."), no remainder
            # Try each imported name as a submodule
            names = imp.get("names", [])
            if names:
                for name in names:
                    resolved = resolve_python(from_file, source + name, imp, all_files, **ctx)
                    if resolved:
                        return resolved
            # Fallback: resolve to __init__.py of current package
            from_dir = os.path.dirname(from_file)
            for _ in range(dots - 1):
                from_dir = os.path.dirname(from_dir)
            init_candidate = os.path.join(from_dir, "__init__.py") if from_dir else "__init__.py"
            init_candidate = init_candidate.replace("\\", "/")
            if init_candidate in all_files:
                return init_candidate
            return None

        candidate_base = candidate_base.replace("\\", "/")

        return try_candidates(candidate_base, [".py"], ["__init__.py"], all_files)

    # --- Absolute import (no leading dot) ---
    tail = source.replace(".", "/")

    return try_candidates(tail, [".py"], ["__init__.py"], all_files)


def resolve_go(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a Go import path to a file path via suffix matching.

    Go imports are full module paths (e.g. "github.com/myorg/proj/pkg/utils").
    The module prefix maps to the repo root; the suffix maps to a directory.
    We try progressively shorter suffixes against directories containing .go files.
    Returns the first .go file alphabetically in the matched directory.
    """
    if not source:
        return None

    dir_to_files = build_dir_files_map(all_files, ".go")
    return find_dir_by_suffix(source, dir_to_files)


def resolve_dotted(
    source: str,
    names: List[str],
    all_files: Set[str],
    ext: str,
    separator: str = ".",
) -> Optional[str]:
    """
    Resolve a dotted-namespace import to a file path.

    Generic utility for languages where imports use dotted package paths
    (Java, C#, Kotlin, Scala). Parameterized by file extension and separator.

    Handles:
    - Specific imports: com.example.utils.Helper → com/example/utils/Helper{ext}
    - Wildcard imports: com.example.utils (names=["*"]) → first file in matched dir

    Uses progressive suffix matching to handle src prefixes.
    Returns None for stdlib/external imports not in the file set.
    """
    if not source:
        return None

    is_wildcard = names == ["*"]

    if is_wildcard:
        dir_suffix = source.replace(separator, "/")

        dir_to_files = build_dir_files_map(all_files, ext)
        return find_dir_by_suffix(dir_suffix, dir_to_files)

    # Specific import: fully qualified name
    file_path = source.replace(separator, "/") + ext
    return find_file_by_suffix(file_path, all_files)


def resolve_java(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    return resolve_dotted(source, imp.get("names", []), all_files, ext=".java")


def resolve_rust(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a Rust import source to a file path.

    Handles:
    - crate:: imports: suffix-matched against all .rs files
    - super:: imports: relative to importing file's module
    - self:: imports: within the current module directory
    - Stdlib/external (std::, tokio::, etc.): returns None
    """
    if not source:
        return None

    names = imp.get("names", [])
    segments = source.split("::")
    is_wildcard = bool(names) and names[0].startswith("*")
    first = segments[0]

    if first not in ("crate", "super", "self"):
        return None

    if first == "crate":
        remaining = segments[1:]
        return _resolve_rust_by_suffix(remaining, is_wildcard, all_files)

    if first == "super":
        # Determine base directory based on file type
        basename = os.path.basename(from_file)
        if basename in ("mod.rs", "lib.rs", "main.rs"):
            # Module file: the module IS the directory, so super = grandparent
            base_dir = os.path.dirname(os.path.dirname(from_file))
        else:
            # Regular file: super = parent directory
            base_dir = os.path.dirname(from_file)

        # Consume additional super:: segments
        remaining = segments[1:]
        while remaining and remaining[0] == "super":
            base_dir = os.path.dirname(base_dir)
            remaining = remaining[1:]

        return _resolve_rust_relative(base_dir, remaining, is_wildcard, all_files)

    if first == "self":
        base_dir = os.path.dirname(from_file)
        remaining = segments[1:]
        return _resolve_rust_relative(base_dir, remaining, is_wildcard, all_files)

    return None


def _resolve_rust_relative(
    base_dir: str,
    remaining: List[str],
    is_wildcard: bool,
    all_files: Set[str],
) -> Optional[str]:
    """Resolve a Rust path relative to a base directory."""
    if not remaining:
        return None

    tail = "/".join(remaining)
    path = (base_dir + "/" + tail) if base_dir else tail

    if is_wildcard:
        # Find first .rs file alphabetically in the matched directory
        prefix = path + "/"
        matches = sorted(f for f in all_files if f.startswith(prefix) and f.endswith(".rs"))
        return matches[0] if matches else None

    return try_candidates(path, [".rs"], ["mod.rs"], all_files)


def _resolve_rust_by_suffix(
    remaining: List[str],
    is_wildcard: bool,
    all_files: Set[str],
) -> Optional[str]:
    """Resolve a crate:: import via progressive suffix matching."""
    if not remaining:
        return None

    suffix = "/".join(remaining)

    if is_wildcard:
        dir_to_files = build_dir_files_map(all_files, ".rs")
        return find_dir_by_suffix(suffix, dir_to_files)

    return find_file_by_suffix(suffix, all_files, extensions=[".rs", "/mod.rs"])


def resolve_dart(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a Dart import URI to a file path.

    Handles:
    - dart: prefix → stdlib, return None
    - package: prefix → self-package resolves via lib/, external returns None
    - Relative paths (with or without dot prefix) → resolve from importing file's dir
    """
    if not source:
        return None

    dart_package_name = ctx.get("dart_package_name")

    # Stdlib
    if source.startswith("dart:"):
        return None

    # Package imports
    if source.startswith("package:"):
        remainder = source[len("package:"):]
        slash_idx = remainder.find("/")
        if slash_idx < 0:
            return None
        pkg_name = remainder[:slash_idx]
        pkg_path = remainder[slash_idx + 1:]

        if pkg_name == dart_package_name:
            target = "lib/" + pkg_path
            return find_file_by_suffix(target, all_files)
        return None

    # Relative import (dot-prefixed or plain path)
    from_dir = os.path.dirname(from_file)
    resolved = os.path.normpath(os.path.join(from_dir, source))
    resolved = resolved.replace("\\", "/")
    if resolved.startswith("./"):
        resolved = resolved[2:]

    if resolved in all_files:
        return resolved
    return try_candidates(resolved, [".dart"], [], all_files)


def resolve_swift(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a Swift import to a file path via suffix matching.

    Swift imports are module-level (e.g. `import MyModule`).
    We match the module name against directories containing .swift files,
    identical to Go's approach.
    Returns the first .swift file alphabetically in the matched directory.
    """
    if not source:
        return None

    dir_to_files = build_dir_files_map(all_files, ".swift")
    return find_dir_by_suffix(source, dir_to_files)


def resolve_php(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a PHP import source to a file path.

    Handles:
    - Namespace use: `App\\Models\\User` → convert \\ to /, suffix-match App/Models/User.php
    - Relative require/include: starts with `.` → resolve relative to importing file
    - Plain require: suffix-match as file path with .php
    """
    if not source:
        return None

    # Namespace use: contains backslash
    if "\\" in source:
        file_path = source.replace("\\", "/") + ".php"
        return find_file_by_suffix(file_path, all_files)

    # Relative require/include (starts with . or /)
    if source.startswith(".") or source.startswith("/"):
        from_dir = os.path.dirname(from_file)
        resolved = os.path.normpath(os.path.join(from_dir, source))
        resolved = resolved.replace("\\", "/")
        if resolved.startswith("./"):
            resolved = resolved[2:]
        if resolved in all_files:
            return resolved
        # Try appending .php
        if not resolved.endswith(".php"):
            candidate = resolved + ".php"
            if candidate in all_files:
                return candidate
        return None

    # Plain require: suffix-match as file path
    if source.endswith(".php"):
        return find_file_by_suffix(source, all_files)
    return find_file_by_suffix(source, all_files, extensions=[".php"])


def resolve_ruby(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """
    Resolve a Ruby import source to a file path.

    Handles:
    - require_relative: resolve from importing file's dir, add .rb if missing
    - require/load: suffix-match source with .rb extension against file set
    """
    if not source:
        return None

    is_relative = imp.get("kind") == "require_relative"

    if is_relative:
        # require_relative: resolve from importing file's directory
        from_dir = os.path.dirname(from_file)
        resolved = os.path.normpath(os.path.join(from_dir, source))
        resolved = resolved.replace("\\", "/")
        if resolved.startswith("./"):
            resolved = resolved[2:]
        # Add .rb if missing
        if not resolved.endswith(".rb"):
            resolved += ".rb"
        if resolved in all_files:
            return resolved
        return None

    # require/load: suffix-match with .rb extension
    if source.endswith(".rb"):
        return find_file_by_suffix(source, all_files)
    return find_file_by_suffix(source, all_files, extensions=[".rb"])


def resolve_csharp(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    return resolve_dotted(source, imp.get("names", []), all_files, ext=".cs")


def resolve_kotlin(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    return resolve_dotted(source, imp.get("names", []), all_files, ext=".kt")


def resolve_scala(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    return resolve_dotted(source, imp.get("names", []), all_files, ext=".scala")


def resolve_js_ts(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """Delegate JS/TS resolution to the Resolver's _resolve_import_path via ctx."""
    resolve_import_path = ctx.get("resolve_import_path")
    if resolve_import_path:
        return resolve_import_path(from_file, source, all_files)
    return None


def _resolve_c_include(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
) -> Optional[str]:
    """
    Resolve a C/C++ #include to a file path.

    - System includes (kind="system", e.g. <stdio.h>) → None
    - Local includes (kind="local", e.g. "myfile.h") → resolve relative
      to the importing file first, then fall back to suffix matching
    """
    if not source:
        return None

    kind = imp.get("kind", "local")
    if kind == "system":
        return None

    # Try relative resolution first
    from_dir = os.path.dirname(from_file)
    resolved = os.path.normpath(os.path.join(from_dir, source))
    resolved = resolved.replace("\\", "/")
    if resolved.startswith("./"):
        resolved = resolved[2:]
    if resolved in all_files:
        return resolved

    # Fall back to suffix matching
    return find_file_by_suffix(source, all_files)


def resolve_c(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """Resolve a C #include directive."""
    return _resolve_c_include(from_file, source, imp, all_files)


def resolve_cpp(
    from_file: str,
    source: str,
    imp: dict,
    all_files: Set[str],
    **ctx,
) -> Optional[str]:
    """Resolve a C++ #include directive."""
    return _resolve_c_include(from_file, source, imp, all_files)
