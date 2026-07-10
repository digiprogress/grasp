"""
Semantic Skeleton Orchestrator

Reshapes flat ParseResult invariants into an identity skeleton:
- identity: what this file defines (class/function declarations, exports)

100% algorithmic. No LLM. Runs after the resolver.
"""

import os
from typing import Dict, Any


def orchestrate_file(parsed: Dict[str, Any]) -> Dict[str, Any]:
    """
    Reshape a single file's flat invariants into a semantic skeleton.

    Args:
        parsed: A file's ParseResult dict with resolved cross-file references.

    Returns:
        Semantic skeleton dict with identity layer.
    """
    return {
        "identity": _extract_identity(parsed),
    }


# =============================================================================
# IDENTITY: What does this file define?
# =============================================================================

def _extract_identity(parsed: Dict) -> Dict:
    """Extract file identity: what it declares and exports."""
    file_path = parsed.get("file_path", "")
    language = parsed.get("language", "unknown")
    classes = parsed.get("classes", [])
    functions = parsed.get("functions", [])
    exports = parsed.get("exports", [])
    interfaces = parsed.get("interfaces", [])
    enums = parsed.get("enums", [])

    # Determine the primary kind of this file
    kind = "module"
    name = os.path.splitext(os.path.basename(file_path))[0]

    if len(classes) == 1:
        kind = "class"
        name = classes[0]["name"]
    elif len(classes) > 1:
        kind = "multi-class"
    elif interfaces and not classes and not functions:
        kind = "interface"
        if len(interfaces) == 1:
            name = interfaces[0]["name"]
    elif enums and not classes and not functions:
        kind = "enum"
        if len(enums) == 1:
            name = enums[0]["name"]
    elif functions and not classes:
        exported_fns = [f for f in functions if f.get("is_exported")]
        if len(exported_fns) == 1:
            kind = "function"
            name = exported_fns[0]["name"]
        elif exported_fns:
            kind = "utility"

    # Exported names
    exported_names = []
    for exp in exports:
        exported_names.extend(exp.get("names", []))

    identity = {
        "file": file_path,
        "language": language,
        "kind": kind,
        "name": name,
    }

    if classes:
        cls = classes[0]
        if cls.get("parents"):
            identity["extends"] = cls["parents"]
        if cls.get("implements"):
            identity["implements"] = cls["implements"]
        identity["is_exported"] = cls.get("is_exported", False)
    elif functions:
        exported_fns = [f for f in functions if f.get("is_exported")]
        identity["is_exported"] = len(exported_fns) > 0

    if exported_names:
        identity["exports"] = exported_names

    return identity
