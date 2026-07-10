"""
Tree-sitter Parser Module - Full AST Extraction

Uses tree-sitter queries for fast, single-pass extraction.
~5x faster than manual AST traversal.
"""

import logging
import os
import re
from typing import Optional

from .languages import LANGUAGE_MAP, FILENAME_LANGUAGE_MAP
from .result import ParseResult
from .query_extractor import QueryExtractor, ExtractionContext
from modules.lang import get_config

logger = logging.getLogger(__name__)

try:
    import tree_sitter_languages as tsl
    TREE_SITTER_AVAILABLE = True
except ImportError:
    TREE_SITTER_AVAILABLE = False
    logger.warning("tree-sitter-languages not available")


def _blank_non_script(content: str, mode: str) -> str:
    """Keep only script block contents, blank everything else.

    Preserves line count so tree-sitter line numbers match the original file.
    """
    lines = content.split("\n")
    result = []
    in_script = False

    for line in lines:
        stripped = line.strip()

        if mode in ("vue", "svelte"):
            if re.match(r'<script\b', stripped):
                in_script = True
                result.append("")
                continue
            if stripped.startswith("</script"):
                in_script = False
                result.append("")
                continue
        elif mode == "astro":
            if stripped == "---":
                in_script = not in_script
                result.append("")
                continue
            if re.match(r'<script\b', stripped):
                in_script = True
                result.append("")
                continue
            if stripped.startswith("</script"):
                in_script = False
                result.append("")
                continue

        result.append(line if in_script else "")

    return "\n".join(result)


class TreeSitterParser:
    """Full tree-sitter AST parser using query-based extraction."""

    def __init__(self):
        self._parsers = {}
        self._languages = {}
        self._extractor = QueryExtractor()

    def get_language(self, file_path: str) -> Optional[str]:
        """Detect language from file extension or filename."""
        filename = os.path.basename(file_path)

        if filename in FILENAME_LANGUAGE_MAP:
            return FILENAME_LANGUAGE_MAP[filename]

        ext = os.path.splitext(file_path)[1].lower()
        return LANGUAGE_MAP.get(ext)

    def can_parse(self, file_path: str) -> bool:
        """Check if we have a tree-sitter parser available for this file."""
        language = self.get_language(file_path)
        if not language:
            return False
        if not TREE_SITTER_AVAILABLE:
            return False
        try:
            tsl.get_parser(language)
            return True
        except Exception:
            return False

    def _get_parser(self, language: str):
        """Get or create parser for language."""
        if not TREE_SITTER_AVAILABLE:
            return None
        if language not in self._parsers:
            try:
                self._parsers[language] = tsl.get_parser(language)
            except Exception as e:
                logger.warning(f"No parser for {language}: {e}")
                return None
        return self._parsers.get(language)

    def _get_ts_language(self, language: str):
        """Get tree-sitter language object for queries."""
        if not TREE_SITTER_AVAILABLE:
            return None
        if language not in self._languages:
            try:
                self._languages[language] = tsl.get_language(language)
            except Exception as e:
                logger.warning(f"No language for {language}: {e}")
                return None
        return self._languages.get(language)

    def parse(self, content: str, language: str, file_path: str = "") -> ParseResult:
        """Parse file content and extract all code structures."""
        result = ParseResult(file_path=file_path, language=language)

        parse_language = language
        parse_content = content

        cfg = get_config(language)
        if cfg and cfg.script_preprocessor:
            parse_content = _blank_non_script(content, cfg.script_preprocessor)
            parse_language = "tsx"

        parser = self._get_parser(parse_language)
        ts_language = self._get_ts_language(parse_language)

        if parser is None or ts_language is None:
            return result

        try:
            tree = parser.parse(parse_content.encode("utf-8"))
            root = tree.root_node

            ctx = ExtractionContext(
                root=root,
                content=parse_content,
                language=language,
                file_path=file_path,
                parse_language=parse_language,
            )

            # Single-pass extraction using queries
            extracted = self._extractor.extract_all(ctx, ts_language)

            # Map to result
            result.imports = extracted["imports"]
            result.classes = extracted["classes"]
            result.interfaces = extracted["interfaces"]
            result.functions = extracted["functions"]
            result.methods = extracted["methods"]
            result.enums = extracted["enums"]
            result.exports = extracted["exports"]

        except Exception as e:
            logger.error(f"Parse error for {file_path}: {e}")

        return result
