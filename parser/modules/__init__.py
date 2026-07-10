"""
Grasp Parser Modules

Modular pipeline components:
- parser: Full tree-sitter AST analysis (modularized in modules/parser/)
- file_discovery: Blocklist-based file discovery with size filtering
- resolver: Cross-file reference resolution
- orchestrator: Semantic skeleton generation (identity layer)
- arcadedb_writer: Direct ArcadeDB graph writing
"""

from .parser import TreeSitterParser, ParseResult, LANGUAGE_MAP, FILENAME_LANGUAGE_MAP
from .file_discovery import FileDiscoveryConfig
from .resolver import Resolver, resolve_references
from .orchestrator import orchestrate_file
from .arcadedb_direct_writer import ArcadeDBDirectWriter as ArcadeDBWriter

__all__ = [
    # Parser
    "TreeSitterParser",
    "ParseResult",
    "LANGUAGE_MAP",
    "FILENAME_LANGUAGE_MAP",
    # File discovery
    "FileDiscoveryConfig",
    # Resolver
    "Resolver",
    "resolve_references",
    # Orchestrator
    "orchestrate_file",
    # Writers
    "ArcadeDBWriter",
]
