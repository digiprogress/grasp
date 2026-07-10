"""
Tree-sitter Query Definitions

Single-pass queries with predicates for filtering at query level.
"""

# =============================================================================
# TypeScript / TSX
# =============================================================================

_TYPESCRIPT_BASE = """
(import_statement) @import
(export_statement) @export

(class_declaration
  name: (type_identifier) @class.name
  type_parameters: (type_parameters)? @class.type_params) @class
(abstract_class_declaration
  name: (type_identifier) @class.name
  type_parameters: (type_parameters)? @class.type_params) @class

(interface_declaration
  name: (type_identifier) @interface.name
  type_parameters: (type_parameters)? @interface.type_params) @interface

(function_declaration
  name: (identifier) @function.name
  type_parameters: (type_parameters)? @function.type_params
  parameters: (formal_parameters) @function.params) @function

; Arrow functions assigned to variables: const foo = (a, b) => {}
(variable_declarator
  name: (identifier) @function.name
  value: (arrow_function
    parameters: (formal_parameters) @function.params)) @function

; Function expressions assigned to variables: const foo = function(a, b) {}
(variable_declarator
  name: (identifier) @function.name
  value: (function
    parameters: (formal_parameters) @function.params)) @function

(method_definition
  name: (property_identifier) @method.name
  parameters: (formal_parameters) @method.params) @method
(abstract_method_signature
  name: (property_identifier) @method.name
  parameters: (formal_parameters) @method.params) @method

(enum_declaration
  name: (identifier) @enum.name) @enum

; CommonJS require()
(call_expression
  function: (identifier) @_require_fn
  arguments: (arguments (string) @require.source)
  (#eq? @_require_fn "require")) @require
"""

TYPESCRIPT_QUERY = _TYPESCRIPT_BASE

TSX_QUERY = _TYPESCRIPT_BASE

# =============================================================================
# JavaScript
# =============================================================================

JAVASCRIPT_QUERY = """
(import_statement) @import
(export_statement) @export

(class_declaration
  name: (identifier) @class.name) @class

; Class expressions (e.g. module.exports = class Foo { ... })
(assignment_expression
  right: (class
    name: (identifier) @class.name) @class)

(function_declaration
  name: (identifier) @function.name
  parameters: (formal_parameters) @function.params) @function

; Arrow functions assigned to variables: const foo = (a, b) => {}
(variable_declarator
  name: (identifier) @function.name
  value: (arrow_function
    parameters: (formal_parameters) @function.params)) @function

; Function expressions assigned to variables: const foo = function(a, b) {}
(variable_declarator
  name: (identifier) @function.name
  value: (function
    parameters: (formal_parameters) @function.params)) @function

(method_definition
  name: (property_identifier) @method.name
  parameters: (formal_parameters) @method.params) @method

; CommonJS require()
(call_expression
  function: (identifier) @_require_fn
  arguments: (arguments (string) @require.source)
  (#eq? @_require_fn "require")) @require
"""

# =============================================================================
# Python
# =============================================================================

PYTHON_QUERY = """
(import_statement) @import
(import_from_statement) @import

(class_definition
  name: (identifier) @class.name) @class

(function_definition
  name: (identifier) @function.name
  parameters: (parameters) @function.params) @function

; Class methods (Python uses function_definition inside class body)
(class_definition
  body: (block
    (function_definition
      name: (identifier) @method.name
      parameters: (parameters) @method.params) @method))

; Decorated class methods
(class_definition
  body: (block
    (decorated_definition
      definition: (function_definition
        name: (identifier) @method.name
        parameters: (parameters) @method.params) @method)))
"""

# =============================================================================
# Go
# =============================================================================

GO_QUERY = """
(import_declaration) @import

(type_declaration
  (type_spec
    name: (type_identifier) @interface.name
    type: (interface_type)) @interface)

(type_declaration
  (type_spec
    name: (type_identifier) @class.name
    type: (struct_type)) @class)

(function_declaration
  name: (identifier) @function.name) @function

(method_declaration
  name: (field_identifier) @method.name) @method
"""

# =============================================================================
# Rust
# =============================================================================

RUST_QUERY = """
(use_declaration) @import

(trait_item
  name: (type_identifier) @interface.name) @interface

(enum_item
  name: (type_identifier) @enum.name) @enum

(struct_item
  name: (type_identifier) @class.name) @class

(function_item
  name: (identifier) @function.name) @function

(impl_item
  body: (declaration_list
    (function_item
      name: (identifier) @method.name) @method))
"""

# =============================================================================
# Java
# =============================================================================

JAVA_QUERY = """
(import_declaration) @import

(class_declaration
  name: (identifier) @class.name) @class

(interface_declaration
  name: (identifier) @interface.name) @interface

(enum_declaration
  name: (identifier) @enum.name) @enum

(method_declaration
  name: (identifier) @method.name) @method
"""

# =============================================================================
# C#
# =============================================================================

CSHARP_QUERY = """
(using_directive) @import

(class_declaration
  name: (identifier) @class.name) @class

(interface_declaration
  name: (identifier) @interface.name) @interface

(enum_declaration
  name: (identifier) @enum.name) @enum

(method_declaration
  name: (identifier) @method.name) @method
"""

# =============================================================================
# Kotlin
# =============================================================================

KOTLIN_QUERY = """
(import_header) @import

(class_declaration
  name: (type_identifier) @class.name) @class

(object_declaration
  name: (type_identifier) @class.name) @class

; Kotlin interfaces are class_declaration with "interface" keyword
(class_declaration "interface"
  name: (type_identifier) @interface.name) @interface

; Kotlin enum classes
(class_declaration "enum"
  name: (type_identifier) @enum.name) @enum

(function_declaration
  name: (simple_identifier) @function.name) @function

(class_body
  (function_declaration
    name: (simple_identifier) @method.name) @method)

(enum_class_body
  (function_declaration
    name: (simple_identifier) @method.name) @method)
"""

# =============================================================================
# Scala
# =============================================================================

SCALA_QUERY = """
(import_declaration) @import

(class_definition
  name: (identifier) @class.name) @class

(trait_definition
  name: (identifier) @interface.name) @interface

(object_definition
  name: (identifier) @class.name) @class

(function_definition
  name: (identifier) @function.name) @function

(template_body
  (function_definition
    name: (identifier) @method.name) @method)
"""

# =============================================================================
# Dart
# =============================================================================

DART_QUERY = """
(import_or_export) @import

(class_definition
  name: (identifier) @class.name) @class

(enum_declaration
  name: (identifier) @enum.name) @enum

(function_signature
  name: (identifier) @function.name) @function

(method_signature
  name: (identifier) @method.name) @method
"""

# =============================================================================
# Swift
# =============================================================================

SWIFT_QUERY = """
(import_declaration) @import

(class_declaration
  name: (type_identifier) @class.name) @class

(struct_declaration
  name: (type_identifier) @class.name) @class

(protocol_declaration
  name: (type_identifier) @interface.name) @interface

(enum_declaration
  name: (type_identifier) @enum.name) @enum

(function_declaration
  name: (simple_identifier) @function.name) @function

(class_declaration
  (function_declaration
    name: (simple_identifier) @method.name) @method)

(struct_declaration
  (function_declaration
    name: (simple_identifier) @method.name) @method)

(enum_declaration
  (function_declaration
    name: (simple_identifier) @method.name) @method)
"""

# =============================================================================
# PHP
# =============================================================================

PHP_QUERY = """
(namespace_use_declaration) @import

(expression_statement
  (assignment_expression
    right: (include_expression) @require))

(expression_statement
  (include_expression) @require)

(return_statement
  (include_expression) @require)

(class_declaration
  name: (name) @class.name) @class

(interface_declaration
  name: (name) @interface.name) @interface

(trait_declaration
  name: (name) @class.name) @class

(enum_declaration
  name: (name) @enum.name) @enum

(function_definition
  name: (name) @function.name) @function

(method_declaration
  name: (name) @method.name) @method
"""

# =============================================================================
# Ruby
# =============================================================================

RUBY_QUERY = """
(call
  method: (identifier) @_require_fn
  arguments: (argument_list (string (string_content) @require.source))
  (#match? @_require_fn "^(require|require_relative|load)$")) @require

(class
  name: (constant) @class.name) @class

(module
  name: (constant) @class.name) @class

(method
  name: (identifier) @method.name) @method

(singleton_method
  name: (identifier) @method.name) @method
"""

# =============================================================================
# C
# =============================================================================

C_QUERY = """
(preproc_include) @import

(enum_specifier
  name: (type_identifier) @enum.name) @enum

(function_definition
  declarator: (function_declarator
    declarator: (identifier) @function.name)) @function

(declaration
  declarator: (function_declarator
    declarator: (identifier) @function.name)) @function
"""

# =============================================================================
# C++
# =============================================================================

CPP_QUERY = C_QUERY + """
(class_specifier
  name: (type_identifier) @class.name) @class

(struct_specifier
  name: (type_identifier) @class.name) @class

(function_definition
  declarator: (function_declarator
    declarator: (qualified_identifier) @method.name)) @method
"""

# =============================================================================
# Language Query Map
# =============================================================================

_ALL_LANGUAGE_QUERIES = {
    "typescript": TYPESCRIPT_QUERY,
    "tsx": TSX_QUERY,
    "javascript": JAVASCRIPT_QUERY,
    "python": PYTHON_QUERY,
    "go": GO_QUERY,
    "rust": RUST_QUERY,
    "java": JAVA_QUERY,
    "c_sharp": CSHARP_QUERY,
    "kotlin": KOTLIN_QUERY,
    "scala": SCALA_QUERY,
    "dart": DART_QUERY,
    "swift": SWIFT_QUERY,
    "php": PHP_QUERY,
    "ruby": RUBY_QUERY,
    "c": C_QUERY,
    "cpp": CPP_QUERY,
}

LANGUAGE_QUERIES = _ALL_LANGUAGE_QUERIES
