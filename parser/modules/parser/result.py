"""
ParseResult Dataclass

Complete parse result structure for a single file.
"""

from dataclasses import dataclass, field
from typing import Dict, List, Any


@dataclass
class ParseResult:
    """Complete parse result for a single file."""
    file_path: str
    language: str

    imports: List[Dict[str, Any]] = field(default_factory=list)
    functions: List[Dict[str, Any]] = field(default_factory=list)
    methods: List[Dict[str, Any]] = field(default_factory=list)
    classes: List[Dict[str, Any]] = field(default_factory=list)
    interfaces: List[Dict[str, Any]] = field(default_factory=list)
    enums: List[Dict[str, Any]] = field(default_factory=list)
    exports: List[Dict[str, Any]] = field(default_factory=list)
