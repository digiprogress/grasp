"""
Language-specific import extractors.

Extracted from query_extractor.py to keep the core extractor thin.
All functions are standalone — they take (nodes, ctx) and return List[Dict].
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


# ── Shared helpers ──────────────────────────────────────────────────

def node_text(node: Any, ctx) -> str:
    """Get node text using byte offsets (tree-sitter reports byte positions)."""
    if not node:
        return ""
    return ctx.content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def is_child_of(child: Any, parent: Any) -> bool:
    """Check if child is descendant of parent."""
    if child.start_byte >= parent.start_byte and child.end_byte <= parent.end_byte:
        return True
    return False


# ── Language-specific import extractors ─────────────────────────────

def extract_imports_js_ts(nodes: List, ctx) -> List[Dict]:
    """Extract JS/TS imports (import ... from 'source')."""
    imports = []
    for node, _ in nodes:
        source = None
        names = []
        for c in node.children:
            if c.type == "string":
                source = node_text(c, ctx).strip("'\"")
            elif c.type == "import_clause":
                for ic in c.children:
                    if ic.type == "identifier":
                        names.append(node_text(ic, ctx))
                    elif ic.type == "named_imports":
                        for spec in ic.children:
                            if spec.type == "import_specifier":
                                n = spec.child_by_field_name("name")
                                if n:
                                    names.append(node_text(n, ctx))
        if source or names:
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


def extract_imports_python(nodes: List, ctx) -> List[Dict]:
    """Extract Python imports (import X / from X import Y)."""
    imports = []
    for node, _ in nodes:
        source = None
        names = []
        if node.type == "import_from_statement":
            module_node = node.child_by_field_name("module_name")
            if module_node:
                source = node_text(module_node, ctx)
            for c in node.children:
                if c == module_node:
                    continue
                if c.type in ("dotted_name", "identifier"):
                    names.append(node_text(c, ctx))
                elif c.type == "aliased_import":
                    name_node = c.child_by_field_name("name")
                    if name_node:
                        names.append(node_text(name_node, ctx))
        else:
            # import_statement: import X, import X.Y
            for c in node.children:
                if c.type == "dotted_name":
                    source = node_text(c, ctx)
                elif c.type == "aliased_import":
                    name_node = c.child_by_field_name("name")
                    if name_node:
                        source = node_text(name_node, ctx)
        if source or names:
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


def extract_imports_java(nodes: List, ctx) -> List[Dict]:
    """Extract Java imports (import com.foo.Bar / import static ... / import ...*)."""
    imports = []
    for node, _ in nodes:
        if node.type != "import_declaration":
            continue
        source = None
        names = []
        is_wildcard = False
        for c in node.children:
            if c.type == "scoped_identifier":
                source = node_text(c, ctx)
            elif c.type in ("*", "asterisk"):
                is_wildcard = True
        if source:
            if is_wildcard:
                names = ["*"]
            else:
                parts = source.rsplit(".", 1)
                names = [parts[-1]] if parts else []
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


def extract_imports_go(nodes: List, ctx) -> List[Dict]:
    """Extract Go imports (import "fmt" / import ( "fmt"; "net/http" ))."""
    imports = []
    for node, _ in nodes:
        if node.type != "import_declaration":
            continue
        for c in node.children:
            if c.type == "import_spec":
                entry = _parse_go_import_spec(c, ctx)
                if entry:
                    imports.append(entry)
            elif c.type == "import_spec_list":
                for gc in c.children:
                    if gc.type == "import_spec":
                        entry = _parse_go_import_spec(gc, ctx)
                        if entry:
                            imports.append(entry)
    return imports


def _parse_go_import_spec(spec_node: Any, ctx) -> Optional[Dict]:
    """Parse a single Go import_spec into {source, names, line}."""
    alias = None
    source = None
    for c in spec_node.children:
        if c.type == "package_identifier":
            alias = node_text(c, ctx)
        elif c.type == "interpreted_string_literal":
            source = node_text(c, ctx).strip('"')
    if not source:
        return None
    name = alias or source.rsplit("/", 1)[-1]
    return {"source": source, "names": [name], "line": spec_node.start_point[0] + 1}


def extract_imports_rust(nodes: List, ctx) -> List[Dict]:
    """Extract Rust use declarations (use std::io; / use std::collections::{HashMap, BTreeSet})."""
    imports = []
    for node, _ in nodes:
        if node.type != "use_declaration":
            continue
        # The argument field holds the use tree
        arg = node.child_by_field_name("argument")
        if not arg:
            # Fallback: first meaningful child
            for c in node.children:
                if c.type not in ("use", ";", "pub", "comment"):
                    arg = c
                    break
        if not arg:
            continue
        use_map = {}
        _collect_rust_use_tree(arg, "", use_map, ctx)
        for name, full_path in use_map.items():
            imports.append({"source": full_path, "names": [name], "line": node.start_point[0] + 1})
    return imports


def _collect_rust_use_tree(node: Any, base_path: str, imports: Dict, ctx) -> None:
    """Recursively collect Rust use tree into a name→full_path dict."""
    ntype = node.type
    if ntype in ("identifier", "type_identifier"):
        name = node_text(node, ctx)
        if name:
            full = f"{base_path}::{name}" if base_path else name
            imports[name] = full
    elif ntype in ("scoped_identifier", "scoped_type_identifier"):
        full = _collect_rust_path_parts(node, ctx)
        if full:
            last = full.rsplit("::", 1)[-1]
            imports[last] = full
    elif ntype == "use_as_clause":
        children = [c for c in node.children if c.type != "as"]
        if len(children) == 2:
            path_node, alias_node = children
            if path_node.type == "self":
                orig = base_path or "self"
            else:
                orig = _collect_rust_path_parts(path_node, ctx) or node_text(path_node, ctx)
                if base_path and orig:
                    orig = f"{base_path}::{orig}"
                elif base_path:
                    orig = base_path
            alias = node_text(alias_node, ctx)
            if alias and orig:
                imports[alias] = orig
    elif ntype == "use_wildcard":
        # use foo::* — find the path prefix
        for c in node.children:
            if c.type not in ("*", "::"):
                wbase = _collect_rust_path_parts(c, ctx) or node_text(c, ctx)
                if base_path and wbase:
                    wbase = f"{base_path}::{wbase}"
                elif base_path:
                    wbase = base_path
                imports[f"*{wbase}"] = wbase
                return
        if base_path:
            imports[f"*{base_path}"] = base_path
    elif ntype == "use_list":
        for c in node.children:
            if c.type not in ("{", "}", ","):
                _collect_rust_use_tree(c, base_path, imports, ctx)
    elif ntype == "scoped_use_list":
        new_base = ""
        for c in node.children:
            if c.type in ("identifier", "scoped_identifier", "crate", "super", "self"):
                new_base = _collect_rust_path_parts(c, ctx) or node_text(c, ctx)
            elif c.type == "use_list":
                final_base = f"{base_path}::{new_base}" if base_path else new_base
                _collect_rust_use_tree(c, final_base, imports, ctx)
    elif ntype in ("crate", "super", "self"):
        txt = node_text(node, ctx)
        if txt:
            imports[txt] = base_path or txt
    else:
        for c in node.children:
            _collect_rust_use_tree(c, base_path, imports, ctx)


def _collect_rust_path_parts(node: Any, ctx) -> str:
    """Collect Rust path parts into a :: separated string."""
    ntype = node.type
    if ntype in ("identifier", "type_identifier", "crate", "super", "self"):
        return node_text(node, ctx)
    if ntype in ("scoped_identifier", "scoped_type_identifier"):
        parts = []
        for c in node.children:
            if c.type != "::":
                part = _collect_rust_path_parts(c, ctx)
                if part:
                    parts.append(part)
        return "::".join(parts)
    return ""


def extract_imports_csharp(nodes: List, ctx) -> List[Dict]:
    """Extract C# using directives (using X; / using static X; / using Alias = X;)."""
    imports = []
    for node, _ in nodes:
        if node.type != "using_directive":
            continue
        has_static = any(c.type == "static" for c in node.children)
        has_equals = any(c.type == "=" for c in node.children)

        if has_equals:
            # Alias form: using Alias = Type;
            alias_node = node.child_by_field_name("name")
            alias = node_text(alias_node, ctx) if alias_node else None
            # Source is the type node after '='
            source = None
            past_eq = False
            for c in node.children:
                if c.type == "=":
                    past_eq = True
                elif past_eq and c.type not in (";",):
                    source = node_text(c, ctx)
                    break
            names = [alias] if alias else []
        elif has_static:
            # Static form: using static Type;
            source = None
            for c in node.children:
                if c.type in ("qualified_name", "identifier", "generic_name"):
                    source = node_text(c, ctx)
            names = [source.rsplit(".", 1)[-1]] if source else []
        else:
            # Regular form: using Namespace;
            source = None
            for c in node.children:
                if c.type in ("qualified_name", "identifier", "generic_name"):
                    source = node_text(c, ctx)
            names = ["*"]

        if source:
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


def extract_imports_kotlin(nodes: List, ctx) -> List[Dict]:
    """Extract Kotlin imports (import com.foo.Bar / import com.foo.* / import com.foo.Bar as Baz)."""
    imports = []
    for node, _ in nodes:
        if node.type != "import_header":
            continue
        source = None
        names = []
        is_wildcard = False
        alias = None
        for c in node.children:
            if c.type == "identifier":
                source = node_text(c, ctx)
            elif c.type == "wildcard_import":
                is_wildcard = True
            elif c.type == "import_alias":
                for ac in c.children:
                    if ac.type == "type_identifier" or ac.type == "simple_identifier":
                        alias = node_text(ac, ctx)
        if source:
            if is_wildcard:
                # import com.foo.* → source="com.foo", names=["*"]
                source = source.rsplit(".", 1)[0] if "." in source else source
                names = ["*"]
            elif alias:
                names = [alias]
            else:
                parts = source.rsplit(".", 1)
                names = [parts[-1]] if parts else []
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


def extract_imports_scala(nodes: List, ctx) -> List[Dict]:
    """Extract Scala imports (import com.foo.Bar / import com.foo._ / import com.foo.{Bar, Baz})."""
    imports = []
    for node, _ in nodes:
        if node.type != "import_declaration":
            continue
        for c in node.children:
            if c.type in ("import", ";"):
                continue
            _extract_scala_import_expr(c, imports, node, ctx)
    return imports


def _extract_scala_import_expr(node: Any, imports: List, root_node: Any, ctx) -> None:
    """Process a single Scala import expression node."""
    line = root_node.start_point[0] + 1
    ntype = node.type

    if ntype == "namespace_wildcard":
        # import com.foo._
        prefix = _get_scala_import_prefix(node, ctx)
        if prefix:
            imports.append({"source": prefix, "names": ["*"], "line": line})
        return

    if ntype == "namespace_selectors":
        # import com.foo.{Bar, Baz} or import com.foo.{Old => New}
        prefix = _get_scala_import_prefix(node, ctx)
        for c in node.children:
            if c.type in ("{", "}", ","):
                continue
            src, name = _parse_scala_selector(c, prefix, ctx)
            if src and name:
                imports.append({"source": src, "names": [name], "line": line})
        return

    # Plain identifier path: import com.foo.Bar
    text = node_text(node, ctx)
    if text:
        parts = text.rsplit(".", 1)
        names = [parts[-1]] if len(parts) > 1 else [parts[0]]
        imports.append({"source": text, "names": names, "line": line})


def _get_scala_import_prefix(node: Any, ctx) -> str:
    """Get the package prefix from the parent of a wildcard/selectors node."""
    p = node.parent
    if p:
        for c in p.children:
            if c is node:
                break
            if c.type not in ("import", ".", "{", "}", ","):
                return node_text(c, ctx)
    return ""


def _parse_scala_selector(node: Any, prefix: str, ctx) -> tuple:
    """Parse a single selector inside { } — returns (source, imported_name)."""
    ntype = node.type
    text = node_text(node, ctx)

    if ntype == "rename_selector" or "=>" in text:
        # import com.foo.{Old => New}
        parts = text.split("=>")
        if len(parts) == 2:
            old = parts[0].strip()
            new = parts[1].strip()
            src = f"{prefix}.{old}" if prefix else old
            return (src, new)
        return (None, None)

    # Simple selector: import com.foo.{Bar}
    name = text.strip()
    if name:
        src = f"{prefix}.{name}" if prefix else name
        return (src, name)
    return (None, None)


def extract_imports_dart(nodes: List, ctx) -> List[Dict]:
    """Extract Dart imports (import 'uri'; / export 'uri';)."""
    imports = []
    for node, _ in nodes:
        source = None
        names = []
        for c in node.children:
            if c.type in ("string_literal", "string"):
                source = node_text(c, ctx).strip("'\"")
        if source:
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


def extract_imports_swift(nodes: List, ctx) -> List[Dict]:
    """Extract Swift imports (import Foundation / import struct MyModule.MyStruct)."""
    imports = []
    for node, _ in nodes:
        if node.type != "import_declaration":
            continue
        # Children: "import", optional kind (struct/class/func/etc.), module path
        parts = []
        for c in node.children:
            if c.type == "import":
                continue
            text = node_text(c, ctx)
            # Skip kind keywords
            if text in ("struct", "class", "func", "enum", "protocol",
                         "typealias", "var", "let"):
                continue
            if text:
                parts.append(text)
        if not parts:
            continue
        # Full path may be "MyModule.MyStruct" — use module name (first component)
        full = ".".join(parts) if len(parts) > 1 else parts[0]
        module_name = full.split(".")[0]
        imports.append({
            "source": module_name,
            "names": [full.split(".")[-1]] if "." in full else [],
            "line": node.start_point[0] + 1,
        })
    return imports


def extract_imports_php(nodes: List, ctx) -> List[Dict]:
    """Extract PHP namespace use declarations (use App\\Models\\User)."""
    imports = []
    for node, _ in nodes:
        if node.type != "namespace_use_declaration":
            continue
        for c in node.children:
            if c.type == "namespace_use_clause":
                # Single use: use App\Models\User;
                name_node = c.child_by_field_name("name") or c.child_by_field_name("type")
                if not name_node:
                    for gc in c.children:
                        if gc.type in ("qualified_name", "name"):
                            name_node = gc
                            break
                if name_node:
                    source = node_text(name_node, ctx)
                    if source:
                        parts = source.rsplit("\\", 1)
                        name = parts[-1] if "\\" in source else source
                        imports.append({
                            "source": source,
                            "names": [name],
                            "line": node.start_point[0] + 1,
                        })
            elif c.type == "namespace_use_group":
                # Grouped use: use App\Models\{User, Order};
                prefix = ""
                for gc in c.children:
                    if gc.type in ("namespace_name", "qualified_name", "name"):
                        prefix = node_text(gc, ctx)
                    elif gc.type == "namespace_use_group_clause":
                        for ggc in gc.children:
                            if ggc.type in ("namespace_name", "qualified_name", "name"):
                                name = node_text(ggc, ctx)
                                full = f"{prefix}\\{name}" if prefix else name
                                imports.append({
                                    "source": full,
                                    "names": [name.rsplit("\\", 1)[-1]],
                                    "line": node.start_point[0] + 1,
                                })
    return imports


def extract_imports_ruby(nodes: List, ctx) -> List[Dict]:
    """Ruby has no import statements — all imports flow through @require captures."""
    return []


def _extract_includes(nodes: List, ctx) -> List[Dict]:
    """Shared #include extractor for C and C++.

    Parses preproc_include nodes:
    - system_lib_string (<stdio.h>) → kind="system"
    - string_literal ("myfile.h") → kind="local"
    """
    imports = []
    for node, _ in nodes:
        if node.type != "preproc_include":
            continue
        path_node = node.child_by_field_name("path")
        if not path_node:
            continue
        raw = node_text(path_node, ctx)
        if path_node.type == "system_lib_string":
            # <stdio.h> → strip angle brackets
            source = raw.strip("<>")
            kind = "system"
        elif path_node.type == "string_literal":
            # "myfile.h" → strip quotes
            source = raw.strip('"')
            kind = "local"
        else:
            source = raw.strip('"<>')
            kind = "local"
        if source:
            imports.append({
                "source": source,
                "names": [],
                "kind": kind,
                "line": node.start_point[0] + 1,
            })
    return imports


def extract_imports_c(nodes: List, ctx) -> List[Dict]:
    """Extract C #include directives."""
    return _extract_includes(nodes, ctx)


def extract_imports_cpp(nodes: List, ctx) -> List[Dict]:
    """Extract C++ #include directives (same as C)."""
    return _extract_includes(nodes, ctx)


def extract_imports_generic(nodes: List, ctx) -> List[Dict]:
    """Fallback import extractor — scans for string nodes."""
    imports = []
    for node, _ in nodes:
        source = None
        names = []
        for c in node.children:
            if c.type == "string":
                source = node_text(c, ctx).strip("'\"")
            elif c.type == "import_clause":
                for ic in c.children:
                    if ic.type == "identifier":
                        names.append(node_text(ic, ctx))
                    elif ic.type == "named_imports":
                        for spec in ic.children:
                            if spec.type == "import_specifier":
                                n = spec.child_by_field_name("name")
                                if n:
                                    names.append(node_text(n, ctx))
            elif c.type == "dotted_name":
                source = node_text(c, ctx)
        if source or names:
            imports.append({"source": source, "names": names, "line": node.start_point[0] + 1})
    return imports


# ── Requires extractor (shared by JS and Ruby) ─────────────────────

def extract_requires(nodes: List, grouped: Dict, ctx) -> List[Dict]:
    """Extract require()/require_relative()/load() calls as imports."""
    imports = []
    seen = set()

    for node, capture_name in nodes:
        if capture_name == "require.source":
            continue  # Skip source sub-captures
        if capture_name in ("require", "_require_fn"):
            if capture_name == "_require_fn":
                continue  # Skip function name capture

        # node is the call_expression
        source = None
        names = []

        # Get source string from arguments
        for n, cn in grouped.get("require", []):
            if cn == "require.source" and is_child_of(n, node):
                source = node_text(n, ctx).strip("'\"")
                break

        if not source:
            continue

        # Detect function name from _require_fn capture (e.g. require_relative, load)
        fn_name = None
        for n, cn in grouped.get("_require_fn", []):
            if cn == "_require_fn" and is_child_of(n, node):
                fn_name = node_text(n, ctx)
                break

        # Deduplicate
        if source in seen:
            continue
        seen.add(source)

        # Try to get imported name from parent variable_declarator
        # e.g. const http = require("http")
        p = node.parent
        if p and p.type == "variable_declarator":
            name_node = p.child_by_field_name("name")
            if name_node:
                if name_node.type == "identifier":
                    names.append(node_text(name_node, ctx))
                elif name_node.type == "object_pattern":
                    # const { a, b } = require("mod")
                    for c in name_node.children:
                        if c.type == "shorthand_property_identifier_pattern":
                            names.append(node_text(c, ctx))
                        elif c.type == "pair_pattern":
                            val = c.child_by_field_name("value")
                            if val:
                                names.append(node_text(val, ctx))

        entry = {
            "source": source,
            "names": names,
            "line": node.start_point[0] + 1,
        }
        # Propagate kind for non-standard require functions (require_relative, load)
        if fn_name and fn_name != "require":
            entry["kind"] = fn_name

        imports.append(entry)

    return imports
