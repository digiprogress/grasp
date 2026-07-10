"""
Query-based Unified Extractor

Single-pass extraction using tree-sitter queries.
Uses captured field names to reduce post-processing.
"""

import logging
from typing import Any, Dict, List, Optional
from dataclasses import dataclass

from .queries import LANGUAGE_QUERIES
from . import import_extractors
from modules.lang import get_config

logger = logging.getLogger(__name__)


@dataclass
class ExtractionContext:
    """Context for extraction."""
    root: Any
    content: str
    language: str
    file_path: str
    parse_language: str = ""  # actual parser language (e.g. "tsx" for vue/svelte/astro)
    content_bytes: bytes = b""

    def __post_init__(self):
        if not self.content_bytes and self.content:
            self.content_bytes = self.content.encode("utf-8")


class QueryExtractor:
    """
    Unified extractor using tree-sitter queries.
    Single tree traversal for all structural elements.
    """

    def __init__(self):
        self._compiled = {}

    def _get_query(self, language: str, ts_language: Any):
        """Get or compile query for language."""
        if language not in self._compiled:
            query_text = LANGUAGE_QUERIES.get(language)
            if query_text:
                try:
                    self._compiled[language] = ts_language.query(query_text)
                except Exception as e:
                    logger.error("Query compilation failed for %s: %s", language, e)
                    return None
        return self._compiled.get(language)

    def extract_all(self, ctx: ExtractionContext, ts_language: Any) -> Dict[str, Any]:
        """Extract all structural elements in a single pass."""
        effective_lang = ctx.parse_language or ctx.language
        query = self._get_query(effective_lang, ts_language)
        if not query:
            return self._empty()

        # Run query once
        captures = query.captures(ctx.root)

        # Group by capture type
        grouped = self._group_captures(captures)

        # Process each type
        imports = self._extract_imports(grouped.get("import", []), ctx)
        requires = import_extractors.extract_requires(grouped.get("require", []), grouped, ctx)
        if requires:
            imports.extend(requires)

        exports = self._extract_exports(grouped.get("export", []), ctx)
        functions = self._extract_functions(grouped.get("function", []), grouped, ctx)
        classes = self._extract_classes(grouped.get("class", []), grouped, ctx)

        # Cross-reference export default with functions/classes
        # Handles: const Foo = () => {}; export default Foo;
        default_names = {n for e in exports if e.get("export_type") == "default" for n in e.get("names", [])}
        if default_names:
            for func in functions:
                if not func["is_exported"] and func["name"] in default_names:
                    func["is_exported"] = True
            for cls in classes:
                if not cls["is_exported"] and cls["name"] in default_names:
                    cls["is_exported"] = True

        return {
            "imports": imports,
            "exports": exports,
            "classes": classes,
            "interfaces": self._extract_interfaces(grouped.get("interface", []), grouped, ctx),
            "functions": functions,
            "methods": self._extract_methods(grouped.get("method", []), grouped, ctx),
            "enums": self._extract_enums(grouped.get("enum", []), grouped, ctx),
        }

    def _empty(self) -> Dict[str, Any]:
        """Empty result."""
        return {k: [] for k in [
            "imports", "exports", "classes", "interfaces", "functions",
            "methods", "enums",
        ]}

    def _group_captures(self, captures: List) -> Dict[str, List]:
        """Group captures by base type (before dot)."""
        grouped = {}
        for node, name in captures:
            base = name.split(".")[0]
            if base not in grouped:
                grouped[base] = []
            grouped[base].append((node, name))
        return grouped

    def _text(self, node: Any, ctx: ExtractionContext) -> str:
        """Get node text using byte offsets (tree-sitter reports byte positions)."""
        if not node:
            return ""
        return ctx.content_bytes[node.start_byte:node.end_byte].decode("utf-8", errors="replace")

    def _find_name(self, captures: List, node: Any, suffix: str) -> Optional[str]:
        """Find captured name for a node."""
        for n, name in captures:
            if name == suffix and self._is_child_of(n, node):
                return n.text.decode("utf-8", errors="replace") if hasattr(n, 'text') else None
        return None

    def _is_child_of(self, child: Any, parent: Any) -> bool:
        """Check if child is descendant of parent."""
        if child.start_byte >= parent.start_byte and child.end_byte <= parent.end_byte:
            return True
        return False

    def _is_exported(self, node: Any, ctx: ExtractionContext = None, name: str = "") -> bool:
        """Check if node is exported, using language-appropriate rules."""
        lang = ctx.language if ctx else ""

        # JS/TS family: check for export statement wrapper
        if lang in ("typescript", "tsx", "javascript", "vue", "svelte", "astro") or not lang:
            p = node.parent
            while p:
                if p.type in ["export_statement", "export_declaration"]:
                    return True
                p = p.parent
            return False

        # Go: uppercase first letter = exported
        if lang == "go":
            return bool(name) and name[0].isupper()

        # Python, Dart: underscore prefix = private
        if lang in ("python", "dart"):
            return bool(name) and not name.startswith("_")

        # Rust: pub keyword → visibility_modifier child
        if lang == "rust":
            for c in node.children:
                if c.type == "visibility_modifier":
                    return True
            return False

        # Java, C#: public modifier
        if lang in ("java", "c_sharp"):
            for c in node.children:
                if c.type == "modifiers":
                    mod_text = self._text(c, ctx)
                    return "public" in mod_text
            # Java: package-private by default (not exported)
            # C#: internal by default (treat as exported for single-project)
            return lang == "c_sharp"

        # C, C++: exported unless static
        if lang in ("c", "cpp"):
            for c in node.children:
                if c.type == "storage_class_specifier" and self._text(c, ctx) == "static":
                    return False
            return True

        # Kotlin, Scala, Swift, PHP, Ruby: public by default
        if lang in ("kotlin", "scala", "swift", "php", "ruby"):
            for c in node.children:
                if c.type in ("modifiers", "visibility_modifier", "modifier"):
                    mod_text = self._text(c, ctx)
                    if any(kw in mod_text for kw in ("private", "internal", "fileprivate")):
                        return False
            return True

        return False

    def _get_parent_class(self, node: Any, ctx: ExtractionContext) -> Optional[str]:
        """Get enclosing class name."""
        # Go method receiver — extract type from receiver parameter
        if node.type == "method_declaration":
            receiver = node.child_by_field_name("receiver")
            if receiver:
                for param in receiver.children:
                    if param.type == "parameter_declaration":
                        type_node = param.child_by_field_name("type")
                        if type_node:
                            return self._text(type_node, ctx).lstrip("*")

        p = node.parent
        while p:
            # Rust impl block — extract type name
            if p.type == "impl_item":
                type_node = p.child_by_field_name("type")
                if type_node:
                    return self._text(type_node, ctx).split("<")[0]

            if p.type in ["class_declaration", "class_definition",
                          "abstract_class_declaration", "class", "class_body",
                          "class_specifier", "struct_specifier",
                          "struct_declaration",
                          "enum_declaration", "enum_class_body",
                          "object_declaration", "object_definition",
                          "trait_definition", "template_body"]:
                if p.type in ("class_body", "template_body", "enum_class_body"):
                    p = p.parent
                if p:
                    name = p.child_by_field_name("name")
                    if name:
                        return self._text(name, ctx)
                    for c in p.children:
                        if c.type in ["type_identifier", "identifier"]:
                            return self._text(c, ctx)
            p = p.parent
        return None

    # =========================================================================
    # Extractors
    # =========================================================================

    def _extract_imports(self, nodes: List, ctx: ExtractionContext) -> List[Dict]:
        """Extract imports — dispatches to language-specific handler."""
        cfg = get_config(ctx.language)
        if cfg:
            handler = getattr(import_extractors, cfg.import_extractor, import_extractors.extract_imports_generic)
        else:
            handler = import_extractors.extract_imports_generic
        return handler(nodes, ctx)

    def _extract_exports(self, nodes: List, ctx: ExtractionContext) -> List[Dict]:
        """Extract exports."""
        exports = []
        for node, _ in nodes:
            names = []
            source = None
            export_type = "named"

            for c in node.children:
                if c.type == "default":
                    export_type = "default"
                elif c.type == "*":
                    export_type = "namespace"
                elif c.type == "string":
                    source = self._text(c, ctx).strip("'\"")
                    export_type = "re-export"
                elif c.type == "export_clause":
                    for spec in c.children:
                        if spec.type == "export_specifier":
                            n = spec.child_by_field_name("name")
                            if n:
                                names.append(self._text(n, ctx))
                elif c.type == "identifier":
                    names.append(self._text(c, ctx))

            # Inline declaration
            decl = node.child_by_field_name("declaration")
            if decl:
                n = decl.child_by_field_name("name")
                if n:
                    names.append(self._text(n, ctx))

            if names or export_type == "namespace":
                exports.append({
                    "export_type": export_type,
                    "names": names,
                    "source": source,
                    "line": node.start_point[0] + 1,
                })
        return exports

    def _extract_parents_implements(
        self, node: Any, ctx: ExtractionContext
    ) -> tuple:
        """Extract parent classes and implemented interfaces from a class node."""
        lang = ctx.language
        parents = []
        implements = []

        # TypeScript/TSX: class_heritage → extends_clause / implements_clause
        if lang in ("typescript", "tsx", "vue", "svelte", "astro"):
            for c in node.children:
                if c.type == "class_heritage":
                    for hc in c.children:
                        if hc.type == "extends_clause":
                            for i in hc.children:
                                if i.type in ("identifier", "type_identifier"):
                                    parents.append(self._text(i, ctx))
                        elif hc.type == "implements_clause":
                            for i in hc.children:
                                if i.type in ("identifier", "type_identifier", "generic_type"):
                                    impl = self._text(i, ctx).split("<")[0]
                                    if impl not in ("implements", ","):
                                        implements.append(impl)

        # JavaScript: class_heritage → identifier directly (no extends_clause)
        elif lang == "javascript":
            for c in node.children:
                if c.type == "class_heritage":
                    for hc in c.children:
                        if hc.type in ("identifier", "member_expression"):
                            parents.append(self._text(hc, ctx))

        # Python: superclasses field → argument_list
        elif lang == "python":
            sc = node.child_by_field_name("superclasses")
            if sc:
                for c in sc.children:
                    if c.type in ("identifier", "attribute"):
                        parents.append(self._text(c, ctx))

        # Java: superclass field, interfaces field
        elif lang == "java":
            sc = node.child_by_field_name("superclass")
            if sc:
                for c in sc.children:
                    if c.type in ("type_identifier", "generic_type"):
                        parents.append(self._text(c, ctx).split("<")[0])
            ifaces = node.child_by_field_name("interfaces")
            if ifaces:
                for c in ifaces.children:
                    if c.type == "type_list":
                        for t in c.children:
                            if t.type in ("type_identifier", "generic_type"):
                                implements.append(self._text(t, ctx).split("<")[0])

        # C#: base_list field (no extends/implements distinction)
        elif lang == "c_sharp":
            bases = node.child_by_field_name("bases")
            if bases:
                for c in bases.children:
                    if c.type in ("identifier", "generic_name", "qualified_name"):
                        parents.append(self._text(c, ctx).split("<")[0])

        # Kotlin: delegation_specifier children
        # constructor_invocation = class parent, user_type = interface
        elif lang == "kotlin":
            for c in node.children:
                if c.type == "delegation_specifier":
                    for sc in c.children:
                        if sc.type == "constructor_invocation":
                            for t in sc.children:
                                if t.type == "user_type":
                                    for ti in t.children:
                                        if ti.type == "type_identifier":
                                            parents.append(self._text(ti, ctx))
                        elif sc.type == "user_type":
                            for ti in sc.children:
                                if ti.type == "type_identifier":
                                    implements.append(self._text(ti, ctx))

        # Scala: extends_clause → type_identifier children
        elif lang == "scala":
            for c in node.children:
                if c.type == "extends_clause":
                    for ec in c.children:
                        if ec.type in ("type_identifier", "identifier"):
                            parents.append(self._text(ec, ctx))

        # Ruby: superclass field
        elif lang == "ruby":
            sc = node.child_by_field_name("superclass")
            if sc:
                for c in sc.children:
                    if c.type in ("constant", "scope_resolution"):
                        parents.append(self._text(c, ctx))

        # PHP: base_clause for extends, class_interface_clause for implements
        elif lang == "php":
            for c in node.children:
                if c.type == "base_clause":
                    for bc in c.children:
                        if bc.type == "name":
                            parents.append(self._text(bc, ctx))
                elif c.type == "class_interface_clause":
                    for ic in c.children:
                        if ic.type == "name":
                            implements.append(self._text(ic, ctx))

        # Dart: superclass, interfaces children
        elif lang == "dart":
            for c in node.children:
                if c.type == "superclass":
                    for sc in c.children:
                        if sc.type == "type_identifier":
                            parents.append(self._text(sc, ctx))
                elif c.type == "interfaces":
                    for ic in c.children:
                        if ic.type == "type_identifier":
                            implements.append(self._text(ic, ctx))

        # Swift: type_inheritance_clause children
        elif lang == "swift":
            for c in node.children:
                if c.type == "type_inheritance_clause":
                    for ic in c.children:
                        if ic.type in ("type_identifier", "user_type"):
                            parents.append(self._text(ic, ctx))

        # C++: base_class_clause → type_identifier children
        elif lang in ("cpp", "c"):
            for c in node.children:
                if c.type == "base_class_clause":
                    for bc in c.children:
                        if bc.type == "type_identifier":
                            parents.append(self._text(bc, ctx))

        return parents, implements

    def _extract_classes(
        self, nodes: List, grouped: Dict, ctx: ExtractionContext
    ) -> List[Dict]:
        """Extract classes."""
        classes = []
        name_captures = grouped.get("class", [])

        # Skip nodes also captured as interface or enum (e.g. Kotlin keyword variants)
        skip_ranges = set()
        for node, _ in grouped.get("interface", []):
            skip_ranges.add((node.start_byte, node.end_byte))
        for node, _ in grouped.get("enum", []):
            skip_ranges.add((node.start_byte, node.end_byte))

        for node, _ in nodes:
            if (node.start_byte, node.end_byte) in skip_ranges:
                continue
            # Get name from field or captured name
            name_node = node.child_by_field_name("name")
            name = self._text(name_node, ctx) if name_node else None

            if not name:
                for c in node.children:
                    if c.type in ["type_identifier", "identifier"]:
                        name = self._text(c, ctx)
                        break

            if not name:
                continue

            parents, implements = self._extract_parents_implements(node, ctx)

            is_exported = self._is_exported(node, ctx, name)

            # Check if this is a class expression assigned to module.exports / exports
            if node.type == "class" and node.parent and node.parent.type == "assignment_expression":
                left = node.parent.child_by_field_name("left")
                if left:
                    left_text = self._text(left, ctx)
                    if left_text in ("module.exports", "exports"):
                        is_exported = True

            classes.append({
                "name": name,
                "parents": parents,
                "implements": implements,
                "is_abstract": "abstract" in node.type,
                "is_exported": is_exported,
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            })

        # Rust: extract trait implementations from impl_item nodes
        if ctx.language == "rust":
            for child in ctx.root.children:
                if child.type == "impl_item":
                    trait_node = child.child_by_field_name("trait")
                    type_node = child.child_by_field_name("type")
                    if trait_node and type_node:
                        trait_name = self._text(trait_node, ctx).split("<")[0]
                        type_name = self._text(type_node, ctx).split("<")[0]
                        for cls in classes:
                            if cls["name"] == type_name:
                                cls["implements"].append(trait_name)

        return classes

    def _extract_interfaces(
        self, nodes: List, grouped: Dict, ctx: ExtractionContext
    ) -> List[Dict]:
        """Extract interfaces."""
        interfaces = []
        for node, _ in nodes:
            name_node = node.child_by_field_name("name")
            name = self._text(name_node, ctx) if name_node else None
            if not name:
                continue

            # Extends (interfaces can extend other interfaces)
            extends = []
            for c in node.children:
                if c.type == "extends_type_clause":
                    for ec in c.children:
                        if ec.type in ["type_identifier", "generic_type"]:
                            ext_name = self._text(ec, ctx).split("<")[0]
                            if ext_name not in ["extends", ","]:
                                extends.append(ext_name)

            # Properties
            props = []
            body = node.child_by_field_name("body")
            if body:
                for c in body.children:
                    if c.type == "property_signature":
                        pn = c.child_by_field_name("name")
                        if not pn:
                            for pc in c.children:
                                if pc.type == "property_identifier":
                                    pn = pc
                                    break
                        if pn:
                            props.append({"name": self._text(pn, ctx)})

            interfaces.append({
                "name": name,
                "extends": extends,
                "properties": props,
                "is_exported": self._is_exported(node, ctx, name),
                "line": node.start_point[0] + 1,
            })
        return interfaces

    def _extract_functions(
        self, nodes: List, grouped: Dict, ctx: ExtractionContext
    ) -> List[Dict]:
        """Extract functions."""
        functions = []
        for node, _ in nodes:
            name_node = node.child_by_field_name("name")
            name = self._text(name_node, ctx) if name_node else None
            if not name:
                continue

            # Skip if inside class (it's a method)
            if self._get_parent_class(node, ctx):
                continue

            # For variable_declarator (arrow/expression), look at the value child
            func_node = node
            if node.type == "variable_declarator":
                value = node.child_by_field_name("value")
                if value and value.type in ("arrow_function", "function_expression"):
                    func_node = value

            ret = func_node.child_by_field_name("return_type")
            return_type = self._text(ret, ctx).lstrip(":-> ") if ret else None

            functions.append({
                "name": name,
                "return_type": return_type,
                "is_async": any(c.type == "async" for c in func_node.children),
                "is_exported": self._is_exported(node, ctx, name),
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            })
        return functions

    def _extract_methods(
        self, nodes: List, grouped: Dict, ctx: ExtractionContext
    ) -> List[Dict]:
        """Extract methods."""
        methods = []
        for node, _ in nodes:
            name_node = node.child_by_field_name("name")
            if not name_node:
                for c in node.children:
                    if c.type == "property_identifier":
                        name_node = c
                        break
            name = self._text(name_node, ctx) if name_node else None
            if not name:
                continue

            class_name = self._get_parent_class(node, ctx)
            ret = node.child_by_field_name("return_type")
            return_type = self._text(ret, ctx).lstrip(":-> ") if ret else None

            # Modifiers
            is_static = any(c.type == "static" for c in node.children)
            is_abstract = "abstract" in node.type or any(c.type == "abstract" for c in node.children)

            visibility = "public"
            for c in node.children:
                if c.type in ["private", "protected", "public"]:
                    visibility = c.type
                    break
            if name.startswith("#"):
                visibility = "private"

            methods.append({
                "name": name,
                "class_name": class_name,
                "return_type": return_type,
                "is_static": is_static,
                "is_abstract": is_abstract,
                "is_async": any(c.type == "async" for c in node.children),
                "visibility": visibility,
                "line": node.start_point[0] + 1,
                "end_line": node.end_point[0] + 1,
            })
        return methods

    def _extract_enums(
        self, nodes: List, grouped: Dict, ctx: ExtractionContext
    ) -> List[Dict]:
        """Extract enums."""
        enums = []
        for node, _ in nodes:
            name_node = node.child_by_field_name("name")
            name = self._text(name_node, ctx) if name_node else None
            if not name:
                continue

            members = []
            body = node.child_by_field_name("body")
            if body:
                for c in body.children:
                    if c.type in ["enum_assignment", "property_identifier"]:
                        mn = c.child_by_field_name("name") if c.type == "enum_assignment" else c
                        if mn:
                            members.append({"name": self._text(mn, ctx)})

            enums.append({
                "name": name,
                "members": members,
                "is_const": any(c.type == "const" for c in node.children),
                "is_exported": self._is_exported(node, ctx, name),
                "line": node.start_point[0] + 1,
            })
        return enums
