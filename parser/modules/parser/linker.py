"""
Cross-File Linker

Resolves imports to actual file definitions.
Creates edges between files based on import/export relationships.
"""

import os
from typing import Dict, List, Any, Optional
from dataclasses import dataclass, field

from modules.lang import ENABLED_EXTENSIONS, ENABLED_INDEX_FILES


@dataclass
class LinkedSymbol:
    """A symbol with its resolved location."""
    name: str
    kind: str  # function, class, interface, type, variable, etc.
    defined_in: str  # file path where defined
    line: int
    exported: bool = True


@dataclass
class ImportEdge:
    """An edge from importer to imported symbol."""
    from_file: str
    to_file: str
    symbol_name: str
    alias: Optional[str] = None
    line: int = 0


@dataclass
class LinkResult:
    """Result of cross-file linking."""
    # All symbols indexed by file
    symbols: Dict[str, List[LinkedSymbol]] = field(default_factory=dict)

    # Resolved import edges
    import_edges: List[ImportEdge] = field(default_factory=list)

    # Unresolved imports (couldn't find definition)
    unresolved: List[Dict[str, Any]] = field(default_factory=list)

    # Component render tree (JSX)
    component_edges: List[Dict[str, Any]] = field(default_factory=list)


class CrossFileLinker:
    """
    Links symbols across files by matching imports to exports.

    Usage:
        linker = CrossFileLinker()
        for file_path, parse_result in results.items():
            linker.add_file(file_path, parse_result)
        linked = linker.link()
    """

    def __init__(self, base_path: str = ""):
        self.base_path = base_path
        self.files: Dict[str, Any] = {}  # file_path -> ParseResult
        self._symbol_index: Dict[str, List[LinkedSymbol]] = {}  # symbol_name -> locations

    def add_file(self, file_path: str, parse_result: Any) -> None:
        """Add a parsed file to the linker."""
        self.files[file_path] = parse_result
        self._index_exports(file_path, parse_result)

    def _index_exports(self, file_path: str, result: Any) -> None:
        """Index all exported symbols from a file."""
        symbols = []

        # Functions
        for func in getattr(result, 'functions', []):
            if func.get('is_exported', False):
                symbols.append(LinkedSymbol(
                    name=func['name'],
                    kind='function',
                    defined_in=file_path,
                    line=func.get('line', 0),
                ))

        # Classes
        for cls in getattr(result, 'classes', []):
            if cls.get('is_exported', False):
                symbols.append(LinkedSymbol(
                    name=cls['name'],
                    kind='class',
                    defined_in=file_path,
                    line=cls.get('line', 0),
                ))

        # Interfaces
        for iface in getattr(result, 'interfaces', []):
            if iface.get('is_exported', False):
                symbols.append(LinkedSymbol(
                    name=iface['name'],
                    kind='interface',
                    defined_in=file_path,
                    line=iface.get('line', 0),
                ))

        # Type aliases
        for ta in getattr(result, 'type_aliases', []):
            if ta.get('is_exported', False):
                symbols.append(LinkedSymbol(
                    name=ta['name'],
                    kind='type',
                    defined_in=file_path,
                    line=ta.get('line', 0),
                ))

        # Enums
        for enum in getattr(result, 'enums', []):
            if enum.get('is_exported', False):
                symbols.append(LinkedSymbol(
                    name=enum['name'],
                    kind='enum',
                    defined_in=file_path,
                    line=enum.get('line', 0),
                ))

        # Variables
        for var in getattr(result, 'variables', []):
            if var.get('is_exported', False):
                symbols.append(LinkedSymbol(
                    name=var['name'],
                    kind='variable',
                    defined_in=file_path,
                    line=var.get('line', 0),
                ))

        # Index by name for lookup
        for sym in symbols:
            if sym.name not in self._symbol_index:
                self._symbol_index[sym.name] = []
            self._symbol_index[sym.name].append(sym)

    def _resolve_import_path(self, from_file: str, import_source: str) -> Optional[str]:
        """Resolve a relative import path to an actual file."""
        if not import_source:
            return None

        # Skip external packages
        if not import_source.startswith('.'):
            return None

        from_dir = os.path.dirname(from_file)

        # Try different extensions
        extensions = [''] + [ext for ext in ENABLED_EXTENSIONS] + ['/' + idx for idx in ENABLED_INDEX_FILES]

        for ext in extensions:
            candidate = os.path.normpath(os.path.join(from_dir, import_source + ext))
            if candidate in self.files:
                return candidate

        return None

    def link(self) -> LinkResult:
        """Perform cross-file linking."""
        result = LinkResult()
        result.symbols = {fp: [] for fp in self.files}

        # Collect all symbols
        for file_path, parse_result in self.files.items():
            for sym_list in self._symbol_index.values():
                for sym in sym_list:
                    if sym.defined_in == file_path:
                        result.symbols[file_path].append(sym)

        # Resolve imports
        for file_path, parse_result in self.files.items():
            for imp in getattr(parse_result, 'imports', []):
                source = imp.get('source', '')
                names = imp.get('names', [])

                resolved_file = self._resolve_import_path(file_path, source)

                for name in names:
                    if resolved_file:
                        # Check if symbol exists in resolved file
                        found = False
                        for sym in self._symbol_index.get(name, []):
                            if sym.defined_in == resolved_file:
                                result.import_edges.append(ImportEdge(
                                    from_file=file_path,
                                    to_file=resolved_file,
                                    symbol_name=name,
                                    line=imp.get('line', 0),
                                ))
                                found = True
                                break

                        if not found:
                            result.unresolved.append({
                                'file': file_path,
                                'symbol': name,
                                'source': source,
                                'line': imp.get('line', 0),
                            })
                    else:
                        # External package or unresolved
                        if source and source.startswith('.'):
                            result.unresolved.append({
                                'file': file_path,
                                'symbol': name,
                                'source': source,
                                'line': imp.get('line', 0),
                            })

        # Build component render tree from JSX
        for file_path, parse_result in self.files.items():
            for jsx in getattr(parse_result, 'jsx_elements', []):
                if jsx.get('is_component'):
                    result.component_edges.append({
                        'from_component': jsx.get('parent_component'),
                        'to_component': jsx['name'],
                        'file': file_path,
                        'props': jsx.get('props', []),
                        'line': jsx.get('line', 0),
                    })

        return result

    def get_symbol(self, name: str) -> List[LinkedSymbol]:
        """Get all definitions of a symbol by name."""
        return self._symbol_index.get(name, [])

    def get_dependents(self, file_path: str) -> List[str]:
        """Get files that import from this file."""
        dependents = []
        for from_file, parse_result in self.files.items():
            for imp in getattr(parse_result, 'imports', []):
                resolved = self._resolve_import_path(from_file, imp.get('source', ''))
                if resolved == file_path:
                    dependents.append(from_file)
                    break
        return dependents

    def get_dependencies(self, file_path: str) -> List[str]:
        """Get files that this file imports from."""
        dependencies = []
        parse_result = self.files.get(file_path)
        if not parse_result:
            return dependencies

        for imp in getattr(parse_result, 'imports', []):
            resolved = self._resolve_import_path(file_path, imp.get('source', ''))
            if resolved:
                dependencies.append(resolved)

        return list(set(dependencies))
