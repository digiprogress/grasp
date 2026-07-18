"""
Cross-File Reference Resolver

After all files are parsed individually, this module resolves:
1. Import paths → actual file paths
2. Class inheritance → parent class definitions

This enables building accurate IMPORTS and INHERITS edges in the graph.
"""

import os
import re
import json
import logging
from typing import Dict, List, Optional, Set, Tuple
from dataclasses import dataclass

from modules.lang import (
    ENABLED_EXTENSIONS, ENABLED_INDEX_FILES,
    HAS_PATH_ALIASES, PATH_ALIAS_CONFIG_FILES,
    get_config,
)
from modules.resolver_langs import try_candidates
import sys as _sys
resolver_langs = _sys.modules["modules.resolver_langs"]

logger = logging.getLogger(__name__)


@dataclass
class Symbol:
    """A class symbol that can be referenced for inheritance resolution."""
    name: str
    file_path: str
    line: int
    symbol_type: str = "class"
    is_exported: bool = True


@dataclass
class ResolvedInheritance:
    """A resolved inheritance relationship."""
    parent_name: str
    parent_file: str
    parent_line: int
    confidence: str  # "high", "medium", "low"


class Resolver:
    """
    Resolves cross-file import paths and inheritance after parsing.

    Usage:
        resolver = Resolver()
        resolved_results = resolver.resolve(parsed_results)
    """

    def __init__(self):
        # Symbol table: name -> list of Symbol (multiple files can export same name)
        self.symbols: Dict[str, List[Symbol]] = {}

        # File exports: file_path -> set of exported names
        self.file_exports: Dict[str, Set[str]] = {}

        # Import map: (file_path, module_path) -> resolved_file_path
        self.import_cache: Dict[Tuple[str, str], Optional[str]] = {}

        # Path aliases from tsconfig.json: alias_prefix -> resolved_prefix
        # e.g., {"@/": "src/", "~/": "src/", "@components/": "src/components/"}
        self.path_aliases: Dict[str, str] = {}

        # Base URL from tsconfig.json (for non-relative imports)
        self.base_url: Optional[str] = None

        # Project root (detected from tsconfig location)
        self.project_root: Optional[str] = None

    def _load_tsconfig_paths(self, parsed_results: List[Dict]):
        """
        Find and parse tsconfig.json to extract path aliases.

        Looks for tsconfig.json in the project root (detected from file paths).
        Extracts compilerOptions.paths and compilerOptions.baseUrl.

        Example tsconfig.json:
        {
            "compilerOptions": {
                "baseUrl": ".",
                "paths": {
                    "@/*": ["src/*"],
                    "@components/*": ["src/components/*"]
                }
            }
        }
        """
        if not parsed_results:
            return

        # Detect project root from file paths
        file_paths = [r.get("file_path", "") for r in parsed_results if r.get("file_path")]
        if not file_paths:
            return

        # Find common prefix to detect project root
        # Start from first file and walk up to find tsconfig.json
        sample_file = file_paths[0]
        current_dir = os.path.dirname(sample_file)

        tsconfig_path = None
        checked_dirs = []

        # Walk up directory tree looking for tsconfig.json
        while current_dir and current_dir != "/":
            checked_dirs.append(current_dir)

            # Check for config files in priority order
            for tsconfig_name in PATH_ALIAS_CONFIG_FILES:
                candidate = os.path.join(current_dir, tsconfig_name)
                if os.path.exists(candidate):
                    tsconfig_path = candidate
                    self.project_root = current_dir
                    break

            if tsconfig_path:
                break

            current_dir = os.path.dirname(current_dir)

        if not tsconfig_path:
            logger.debug(f"No tsconfig.json found in ancestors of {sample_file}")
            # Fall back to default aliases
            self.path_aliases = {"@/": "src/", "~/": "src/"}
            return

        # Parse tsconfig.json
        try:
            with open(tsconfig_path, "r", encoding="utf-8") as f:
                # Handle JSON with comments (common in tsconfig)
                content = f.read()
                # Strip single-line comments (simple approach)
                lines = []
                for line in content.split("\n"):
                    stripped = line.strip()
                    if stripped.startswith("//"):
                        continue
                    # Remove trailing comments
                    if "//" in line and not line.strip().startswith('"'):
                        comment_pos = line.find("//")
                        # Make sure it's not inside a string
                        if line[:comment_pos].count('"') % 2 == 0:
                            line = line[:comment_pos]
                    lines.append(line)
                content = "\n".join(lines)

                # Remove trailing commas (invalid JSON but common in tsconfig)
                content = re.sub(r',(\s*[}\]])', r'\1', content)

                tsconfig = json.loads(content)

        except json.JSONDecodeError as e:
            logger.warning(f"Failed to parse {tsconfig_path}: {e}")
            self.path_aliases = {"@/": "src/", "~/": "src/"}
            return
        except Exception as e:
            logger.warning(f"Error reading {tsconfig_path}: {e}")
            self.path_aliases = {"@/": "src/", "~/": "src/"}
            return

        # Handle "extends" - load base config first
        if "extends" in tsconfig:
            extends_path = tsconfig["extends"]
            if not extends_path.startswith("."):
                # Node module extends (e.g., "@tsconfig/node18")
                pass  # Skip for now
            else:
                base_path = os.path.normpath(
                    os.path.join(os.path.dirname(tsconfig_path), extends_path)
                )
                if not base_path.endswith(".json"):
                    base_path += ".json"

                if os.path.exists(base_path):
                    try:
                        with open(base_path, "r", encoding="utf-8") as f:
                            base_content = f.read()
                            base_content = re.sub(r',(\s*[}\]])', r'\1', base_content)
                            base_config = json.loads(base_content)
                            # Merge base into tsconfig (shallow)
                            if "compilerOptions" in base_config:
                                base_opts = base_config["compilerOptions"]
                                tsconfig_opts = tsconfig.get("compilerOptions", {})
                                merged = {**base_opts, **tsconfig_opts}
                                tsconfig["compilerOptions"] = merged
                    except Exception as e:
                        logger.debug(f"Could not load base config {base_path}: {e}")

        # Extract compilerOptions
        compiler_options = tsconfig.get("compilerOptions", {})

        # Extract baseUrl
        base_url = compiler_options.get("baseUrl", ".")
        if base_url == ".":
            self.base_url = os.path.dirname(tsconfig_path)
        else:
            self.base_url = os.path.normpath(
                os.path.join(os.path.dirname(tsconfig_path), base_url)
            )

        # Extract paths
        paths = compiler_options.get("paths", {})

        if not paths:
            logger.debug(f"No paths in {tsconfig_path}, using defaults")
            self.path_aliases = {"@/": "src/", "~/": "src/"}
            return

        # Convert tsconfig paths to our alias format
        # tsconfig: "@/*": ["src/*"]
        # our format: "@/": "src/"
        for alias_pattern, target_patterns in paths.items():
            if not target_patterns:
                continue

            # Take first target (tsconfig allows multiple, we use first)
            target = target_patterns[0] if isinstance(target_patterns, list) else target_patterns

            # Remove glob suffix /*
            alias_prefix = alias_pattern.rstrip("*")
            target_prefix = target.rstrip("*")

            # Normalize target relative to baseUrl
            if not target_prefix.startswith("/"):
                target_prefix = os.path.join(self.base_url or "", target_prefix)
                # Make relative to project root
                if self.project_root and target_prefix.startswith(self.project_root):
                    target_prefix = target_prefix[len(self.project_root):].lstrip("/")

            self.path_aliases[alias_prefix] = target_prefix

        logger.info(f"Loaded {len(self.path_aliases)} path aliases from {tsconfig_path}")
        logger.debug(f"Path aliases: {self.path_aliases}")

    def _load_workspace_packages(self, parsed_results: List[Dict], all_files: Optional[Set[str]] = None):
        """
        Detect local workspace packages (monorepo) and add as path aliases.

        Maps package names like '@vendure/core' to their local source directories,
        enabling cross-package import resolution in monorepos.
        """
        all_file_paths = set(all_files) if all_files else {r.get("file_path", "") for r in parsed_results if r.get("file_path")}
        if not all_file_paths:
            return

        # Detect project root if not already set by tsconfig loading
        if not self.project_root:
            sample = sorted(all_file_paths)[0]
            current = os.path.dirname(os.path.abspath(sample))
            while current and current != os.path.dirname(current):
                if os.path.exists(os.path.join(current, "package.json")):
                    self.project_root = current
                    break
                current = os.path.dirname(current)
            if not self.project_root:
                return

        # Find all package.json files, skipping irrelevant directories
        skip_dirs = {'node_modules', '.git', 'dist', 'build', '.next', '.cache', '.turbo', 'coverage'}
        pkg_json_files = []
        for root, dirs, files in os.walk(self.project_root):
            dirs[:] = sorted(d for d in dirs if d not in skip_dirs)
            if 'package.json' in files:
                pkg_json_files.append(os.path.join(root, 'package.json'))

        if not pkg_json_files:
            return

        count = 0
        for pkg_path in pkg_json_files:
            try:
                with open(pkg_path, 'r', encoding='utf-8') as f:
                    pkg = json.load(f)
            except (json.JSONDecodeError, IOError):
                continue

            name = pkg.get('name')
            if not name:
                continue

            pkg_dir = os.path.dirname(pkg_path)

            # Skip root package (monorepo root is not importable)
            if pkg_dir == self.project_root:
                continue

            rel_dir = os.path.relpath(pkg_dir, self.project_root)

            # Find the source directory by checking which subdirectory
            # contains actual parsed source files
            source_dir = rel_dir
            for src_name in ('src', 'lib', 'source'):
                candidate = f"{rel_dir}/{src_name}"
                if any(fp.startswith(candidate + '/') for fp in all_file_paths):
                    source_dir = candidate
                    break

            self.path_aliases[name] = source_dir
            count += 1

        if count:
            logger.info(f"Loaded {count} workspace package aliases")
            logger.debug(f"Workspace aliases: {dict(list({k: v for k, v in self.path_aliases.items() if '/' in k and not k.startswith('@/') and not k.startswith('~/')}.items())[:10])}")

    def resolve(
        self,
        parsed_results: List[Dict],
        all_files: Optional[Set[str]] = None,
        extra_symbols: Optional[List[Dict]] = None,
    ) -> List[Dict]:
        """
        Main entry point. Resolves cross-file import paths and inheritance.

        Args:
            parsed_results: List of parsed file results from tree-sitter
            all_files: optional full file-path universe for import resolution.
                On an incremental parse only the changed files are parsed, but
                their imports still point at the rest of the repo — this set
                (from file discovery, no parsing needed) stands in for them.
            extra_symbols: optional class symbols ({name, file_path, line})
                sourced from the existing graph, standing in for the classes
                of files that were not re-parsed this run.

        Returns:
            Same list with resolved import paths and inheritance added
        """
        logger.info(f"Resolving references across {len(parsed_results)} files")

        # Step 0: Load path aliases from config files (e.g. tsconfig.json)
        if HAS_PATH_ALIASES:
            self._load_tsconfig_paths(parsed_results)

        # Step 0b: Detect workspace packages for cross-package import resolution
        self._load_workspace_packages(parsed_results, all_files)

        # Step 0c: Detect Dart package name from pubspec.yaml
        self._load_dart_package_name(parsed_results)

        # Step 1: Build symbol table from all files
        self._build_symbol_table(parsed_results)
        if extra_symbols:
            self._merge_extra_symbols(extra_symbols, parsed_results)
        logger.info(f"Built symbol table with {len(self.symbols)} unique names")

        # Step 2: Build import map for each file
        self._build_import_maps(parsed_results, all_files)

        # Step 3: Resolve inheritance
        resolved_inheritance = 0

        for idx, file_result in enumerate(parsed_results):
            if (idx + 1) % 500 == 0:
                logger.info(f"Resolve progress: {idx+1}/{len(parsed_results)} files")

            file_path = file_result.get("file_path", "")

            # Resolve class inheritance (parents)
            for cls in file_result.get("classes", []):
                parents = cls.get("parents", [])
                if not parents:
                    continue

                resolved_parents = []
                for parent_name in parents:
                    resolved = self._resolve_inheritance(parent_name, file_path, file_result)
                    if resolved:
                        resolved_parents.append({
                            "name": resolved.parent_name,
                            "file": resolved.parent_file,
                            "line": resolved.parent_line,
                            "confidence": resolved.confidence,
                        })
                        resolved_inheritance += 1
                    else:
                        # Keep unresolved parent name for reference
                        resolved_parents.append({
                            "name": parent_name,
                            "file": None,
                            "line": None,
                            "confidence": "unresolved",
                        })

                cls["resolved_parents"] = resolved_parents

        logger.info(f"Resolved {resolved_inheritance} inheritance relationships")

        return parsed_results

    def _build_symbol_table(self, parsed_results: List[Dict]):
        """Build a global symbol table for class inheritance resolution."""

        for file_result in parsed_results:
            file_path = file_result.get("file_path", "")
            exports = set()

            # Index classes (used for inheritance resolution)
            for cls in file_result.get("classes", []):
                name = cls.get("name")
                if not name:
                    continue

                symbol = Symbol(
                    name=name,
                    file_path=file_path,
                    line=cls.get("line", 0),
                    symbol_type="class",
                    is_exported=True,
                )

                if name not in self.symbols:
                    self.symbols[name] = []
                self.symbols[name].append(symbol)
                exports.add(name)

            self.file_exports[file_path] = exports

    def _merge_extra_symbols(self, extra_symbols: List[Dict], parsed_results: List[Dict]):
        """Merge graph-sourced class symbols into the table, for files that
        were not parsed this run. A parsed file's fresh symbols always win:
        extras belonging to a parsed file are stale by definition and skipped."""
        parsed_files = {r.get("file_path", "") for r in parsed_results}
        merged = 0
        for s in extra_symbols:
            name, fp = s.get("name"), s.get("file_path")
            if not name or not fp or fp in parsed_files:
                continue
            self.symbols.setdefault(name, []).append(Symbol(
                name=name, file_path=fp, line=int(s.get("line") or 0),
                symbol_type="class", is_exported=True,
            ))
            merged += 1
        if merged:
            logger.info(f"Merged {merged} class symbols from the existing graph")

    def _build_import_maps(self, parsed_results: List[Dict], all_files: Optional[Set[str]] = None):
        """Build import resolution map for each file."""

        # Collect all file paths for resolution — the full discovered universe
        # when provided (incremental), else the parsed set (full parse).
        all_files = set(all_files) if all_files else {r.get("file_path", "") for r in parsed_results}

        for file_result in parsed_results:
            file_path = file_result.get("file_path", "")
            lang = file_result.get("language", "")
            cfg = get_config(lang)

            # Resolve handler from config
            handler = None
            if cfg and cfg.import_resolver:
                handler = getattr(resolver_langs, cfg.import_resolver, None)

            # Build shared context for handler
            ctx = {
                "dart_package_name": getattr(self, "_dart_package_name", None),
                "resolve_import_path": self._resolve_import_path,
            }

            for imp in file_result.get("imports", []):
                source = imp.get("source", "")
                if not source:
                    continue

                if handler:
                    resolved_path = handler(file_path, source, imp, all_files, **ctx)
                else:
                    resolved_path = self._resolve_import_path(file_path, source, all_files)

                if resolved_path:
                    imp["resolved_file"] = resolved_path
                    self.import_cache[(file_path, source)] = resolved_path

    def _resolve_import_path(
        self,
        from_file: str,
        module_path: str,
        all_files: Set[str]
    ) -> Optional[str]:
        """
        Resolve an import path to an actual file path.

        Handles:
        - Relative imports: ./foo, ../bar
        - Alias imports: @/foo, ~/bar (from tsconfig.json paths)
        - Index files: ./components -> ./components/index.ts
        - Extension inference: ./foo -> ./foo.ts, ./foo.js
        """

        # Check if this is an aliased import using tsconfig paths
        is_aliased = False
        for alias_prefix in self.path_aliases:
            if module_path.startswith(alias_prefix):
                is_aliased = True
                break

        # Skip external packages (no dot prefix, not aliased)
        if not module_path.startswith(".") and not is_aliased:
            # Could be node_modules or stdlib - skip for now
            return None

        # Handle alias paths using loaded tsconfig.json mappings
        # Sorted by length (descending) to match longest prefix first
        for alias_prefix in sorted(self.path_aliases.keys(), key=len, reverse=True):
            if module_path.startswith(alias_prefix):
                target_prefix = self.path_aliases[alias_prefix]
                module_path = target_prefix + module_path[len(alias_prefix):]
                break

        # Get directory of importing file
        from_dir = os.path.dirname(from_file)

        # Resolve relative path
        if module_path.startswith("."):
            resolved = os.path.normpath(os.path.join(from_dir, module_path))
        else:
            resolved = module_path

        # Clean up path
        resolved = resolved.replace("\\", "/")
        if resolved.startswith("./"):
            resolved = resolved[2:]

        return try_candidates(resolved, ENABLED_EXTENSIONS, ENABLED_INDEX_FILES, all_files)

    def _load_dart_package_name(self, parsed_results: List[Dict]):
        """
        Detect the Dart package name from pubspec.yaml.

        Walks up the directory tree from the first .dart file looking for
        pubspec.yaml, then extracts the `name:` field with regex.
        """
        if getattr(self, "_dart_package_name", None) is not None:
            return

        sample = None
        for r in parsed_results:
            fp = r.get("file_path", "")
            if fp.endswith(".dart"):
                sample = fp
                break
        if not sample:
            self._dart_package_name = None
            return

        current_dir = os.path.dirname(sample)
        while current_dir and current_dir != "/":
            candidate = os.path.join(current_dir, "pubspec.yaml")
            if os.path.exists(candidate):
                try:
                    with open(candidate, "r", encoding="utf-8") as f:
                        content = f.read()
                    m = re.search(r'^name:\s*(\S+)', content, re.MULTILINE)
                    if m:
                        self._dart_package_name = m.group(1)
                        return
                except Exception:
                    pass
            current_dir = os.path.dirname(current_dir)

        self._dart_package_name = None

    def _resolve_inheritance(
        self,
        parent_name: str,
        file_path: str,
        file_result: Dict
    ) -> Optional[ResolvedInheritance]:
        """Resolve a parent class name to its class definition."""

        if not parent_name:
            return None

        # Handle qualified names (e.g., "module.ClassName")
        simple_name = parent_name.split(".")[-1] if "." in parent_name else parent_name

        # 1. Check local classes (same file)
        for cls in file_result.get("classes", []):
            if cls.get("name") == simple_name:
                return ResolvedInheritance(
                    parent_name=simple_name,
                    parent_file=file_path,
                    parent_line=cls.get("line", 0),
                    confidence="high",
                )

        # 2. Check imports
        for imp in file_result.get("imports", []):
            resolved_file = imp.get("resolved_file")
            if not resolved_file:
                continue

            import_names = imp.get("names", [])
            if simple_name in import_names or parent_name in import_names:
                if simple_name in self.symbols:
                    for symbol in self.symbols[simple_name]:
                        if symbol.file_path == resolved_file and symbol.symbol_type == "class":
                            return ResolvedInheritance(
                                parent_name=symbol.name,
                                parent_file=symbol.file_path,
                                parent_line=symbol.line,
                                confidence="high",
                            )

        # 3. Global lookup for classes
        if simple_name in self.symbols:
            for symbol in self.symbols[simple_name]:
                if symbol.symbol_type == "class":
                    confidence = "medium" if len([
                        s for s in self.symbols[simple_name]
                        if s.symbol_type == "class"
                    ]) == 1 else "low"

                    return ResolvedInheritance(
                        parent_name=symbol.name,
                        parent_file=symbol.file_path,
                        parent_line=symbol.line,
                        confidence=confidence,
                    )

        # 4. Could be external (e.g., React.Component, BaseModel from library)
        # Return None - will be marked as "unresolved" by caller
        return None


def resolve_references(
    parsed_results: List[Dict],
    repo_root: str = None,
    all_files: Optional[Set[str]] = None,
    extra_symbols: Optional[List[Dict]] = None,
) -> List[Dict]:
    """
    Convenience function to resolve all cross-file references.

    Usage:
        parsed_results = resolve_references(parsed_results, repo_root="/tmp/repo")

    all_files / extra_symbols supply the unparsed rest of the repo on an
    incremental parse (file universe from discovery, class symbols from the
    graph) — see Resolver.resolve.
    """
    resolver = Resolver()
    if repo_root:
        resolver.project_root = repo_root
    return resolver.resolve(parsed_results, all_files=all_files, extra_symbols=extra_symbols)
