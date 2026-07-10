"""
Tree-sitter Parser Module

Fast query-based AST extraction. ~5x faster than manual traversal.

Public API:
- TreeSitterParser: Main parser class
- ParseResult: Dataclass containing parse results
- CrossFileLinker: Cross-file import/export resolution
- LANGUAGE_MAP: File extension to language mapping
- FILENAME_LANGUAGE_MAP: Filename to language mapping
"""

from .base import TreeSitterParser
from .result import ParseResult
from .linker import CrossFileLinker, LinkResult, LinkedSymbol, ImportEdge
from .languages import LANGUAGE_MAP, FILENAME_LANGUAGE_MAP

__all__ = [
    "TreeSitterParser",
    "ParseResult",
    "CrossFileLinker",
    "LinkResult",
    "LinkedSymbol",
    "ImportEdge",
    "LANGUAGE_MAP",
    "FILENAME_LANGUAGE_MAP",
]
