"""
Language Detection Maps

File extension and filename to tree-sitter language mapping.
"""

# Extension-based language detection
LANGUAGE_MAP = {
    # Core languages (original)
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".ts": "typescript",
    ".tsx": "tsx",
    # Note: .d.ts files are detected via .ts extension (splitext returns .ts)
    ".rs": "rust",
    ".go": "go",
    ".java": "java",
    ".c": "c",
    ".cpp": "cpp",
    ".cc": "cpp",
    ".cxx": "cpp",
    ".h": "c",
    ".hpp": "cpp",
    ".rb": "ruby",
    ".php": "php",

    # Styles
    ".css": "css",
    ".scss": "scss",
    ".sass": "scss",
    ".less": "css",  # tree-sitter-css handles less

    # Templates/Markup
    ".html": "html",
    ".htm": "html",
    ".vue": "vue",
    ".svelte": "svelte",
    ".astro": "astro",

    # Config
    ".yaml": "yaml",
    ".yml": "yaml",
    ".json": "json",
    ".toml": "toml",

    # Data (for schema understanding)
    ".csv": "csv",
    ".tsv": "tsv",

    # Schemas
    ".sql": "sql",
    ".graphql": "graphql",
    ".gql": "graphql",
    ".prisma": "prisma",
    ".proto": "proto",

    # Scripts
    ".sh": "bash",
    ".bash": "bash",
    ".zsh": "bash",

    # Documentation
    ".md": "markdown",
    ".mdx": "markdown",
    ".rst": "rst",

    # Other languages
    ".scala": "scala",
    ".kt": "kotlin",
    ".kts": "kotlin",
    ".swift": "swift",
    ".m": "objc",
    ".mm": "objc",
    ".cs": "c_sharp",
    ".fs": "ocaml",  # F# uses similar parser
    ".ex": "elixir",
    ".exs": "elixir",
    ".erl": "erlang",
    ".hrl": "erlang",
    ".hs": "haskell",
    ".lua": "lua",
    ".r": "r",
    ".R": "r",
    ".jl": "julia",
    ".pl": "perl",
    ".pm": "perl",
    ".clj": "clojure",
    ".cljs": "clojure",
    ".cljc": "clojure",
    ".dart": "dart",
    ".elm": "elm",
    ".hcl": "hcl",
    ".tf": "hcl",
    ".zig": "zig",
    ".nim": "nim",
    ".v": "v",
    ".d": "d",
    ".ada": "ada",
    ".adb": "ada",
    ".ads": "ada",
    ".pas": "pascal",
    ".pp": "pascal",
    ".f90": "fortran",
    ".f95": "fortran",
    ".f03": "fortran",
    ".cob": "cobol",
    ".cbl": "cobol",
    ".lisp": "commonlisp",
    ".cl": "commonlisp",
    ".ml": "ocaml",
    ".mli": "ocaml",
    ".re": "reason",
    ".rei": "reason",
}

# Filename-based language detection (for files without extensions)
FILENAME_LANGUAGE_MAP = {
    "Dockerfile": "dockerfile",
    "dockerfile": "dockerfile",
    "Makefile": "make",
    "makefile": "make",
    "GNUmakefile": "make",
    "CMakeLists.txt": "cmake",
    "Jenkinsfile": "groovy",
    "Vagrantfile": "ruby",
    "Rakefile": "ruby",
    "Gemfile": "ruby",
    "Podfile": "ruby",
    "Brewfile": "ruby",
    "BUILD": "starlark",
    "BUILD.bazel": "starlark",
    "WORKSPACE": "starlark",
    "WORKSPACE.bazel": "starlark",
    ".gitignore": "gitignore",
    ".dockerignore": "gitignore",
    ".prettierrc": "json",
    ".eslintrc": "json",
    ".babelrc": "json",
    "tsconfig.json": "json",
    "package.json": "json",
    "composer.json": "json",
    "Cargo.toml": "toml",
    "pyproject.toml": "toml",
    "go.mod": "gomod",
    "go.sum": "gosum",
    ".editorconfig": "ini",
    "requirements.txt": "requirements",
    "Pipfile": "toml",
}
