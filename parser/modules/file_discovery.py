"""
File Discovery Configuration and Blocklist

Provides a blocklist-based approach for file discovery with:
- Size filtering (default 500KB max)
- User-configurable include/exclude patterns (gitignore-style)
- Comprehensive blocklists for binary, generated, and vendor files
"""

import fnmatch
from dataclasses import dataclass, field
from typing import List, Set


@dataclass
class FileDiscoveryConfig:
    """Configuration for file discovery with blocklist approach."""

    max_file_size: int = 512_000  # 500KB default
    include_patterns: List[str] = field(default_factory=list)
    exclude_patterns: List[str] = field(default_factory=list)
    folder: str = ""  # Only parse files under this subfolder prefix

    @classmethod
    def from_input(cls, input_data: dict) -> "FileDiscoveryConfig":
        """Create config from job input data."""
        fd = input_data.get("file_discovery", {})
        return cls(
            max_file_size=fd.get("max_file_size", 512_000),
            include_patterns=fd.get("include", []),
            exclude_patterns=fd.get("exclude", []),
            folder=fd.get("folder", ""),
        )

    def matches_include(self, path: str) -> bool:
        """Check if path matches any include pattern."""
        if not self.include_patterns:
            return False
        for pattern in self.include_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False

    def matches_exclude(self, path: str) -> bool:
        """Check if path matches any exclude pattern."""
        for pattern in self.exclude_patterns:
            if fnmatch.fnmatch(path, pattern):
                return True
        return False


# =============================================================================
# BLOCKLIST CONSTANTS
# =============================================================================

# Binary/compiled files - never useful for code analysis
BINARY_EXTENSIONS: Set[str] = {
    ".exe", ".dll", ".so", ".dylib", ".a", ".lib",
    ".pyc", ".pyo", ".pyd", "__pycache__",
    ".class", ".jar", ".war", ".ear",
    ".o", ".obj", ".ko",
    ".wasm",
    ".beam",  # Erlang
    ".rlib",  # Rust
}

# Image files
IMAGE_EXTENSIONS: Set[str] = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".ico", ".webp",
    ".svg", ".tiff", ".tif", ".psd", ".ai", ".eps",
    ".heic", ".heif", ".raw", ".cr2", ".nef",
}

# Font files
FONT_EXTENSIONS: Set[str] = {
    ".ttf", ".otf", ".woff", ".woff2", ".eot", ".fon",
}

# Media/video/audio files
MEDIA_EXTENSIONS: Set[str] = {
    ".mp4", ".mp3", ".wav", ".avi", ".mov", ".mkv", ".flv",
    ".wmv", ".webm", ".m4a", ".m4v", ".aac", ".ogg", ".flac",
    ".3gp", ".mpeg", ".mpg",
}

# Archive files
ARCHIVE_EXTENSIONS: Set[str] = {
    ".zip", ".tar", ".gz", ".bz2", ".xz", ".7z", ".rar",
    ".tgz", ".tbz2", ".lz", ".lzma", ".cab", ".iso",
}

# Data/database files
DATA_EXTENSIONS: Set[str] = {
    ".db", ".sqlite", ".sqlite3", ".mdb", ".accdb",
    # ".csv", ".tsv" - INCLUDED for schema/structure understanding
    ".parquet", ".avro", ".orc",
    ".pickle", ".pkl", ".npy", ".npz", ".h5", ".hdf5",
    ".feather", ".arrow",
}

# Lock files (exact filenames)
LOCK_FILENAMES: Set[str] = {
    "package-lock.json",
    "yarn.lock",
    "pnpm-lock.yaml",
    "Cargo.lock",
    "Gemfile.lock",
    "poetry.lock",
    "Pipfile.lock",
    "composer.lock",
    "mix.lock",
    "pubspec.lock",
    "packages.lock.json",  # NuGet
    "paket.lock",
    "shrinkwrap.yaml",
    "bun.lockb",
}

# Generated file patterns (checked with fnmatch)
GENERATED_PATTERNS: List[str] = [
    "*.min.js",
    "*.min.css",
    "*.bundle.js",
    "*.chunk.js",
    "*.map",          # Source maps
    # "*.d.ts" - INCLUDED: many are hand-written API type definitions
    "*.generated.*",
    "*.auto.*",
    "*-lock.*",
    "*.snap",           # Jest snapshots
    "*.pb.go",          # Protobuf generated
    "*.pb.ts",
    "*_pb2.py",
    "*_pb2_grpc.py",
    "*.g.dart",         # Dart generated
    "*.freezed.dart",
]

# Known hidden files to INCLUDE (not block)
ALLOWED_HIDDEN_FILES: Set[str] = {
    ".gitignore",
    ".gitattributes",
    ".editorconfig",
    ".prettierrc",
    ".prettierrc.json",
    ".prettierrc.yaml",
    ".prettierrc.yml",
    ".eslintrc",
    ".eslintrc.json",
    ".eslintrc.yaml",
    ".eslintrc.yml",
    ".eslintrc.js",
    ".babelrc",
    ".babelrc.json",
    ".env.example",
    ".env.sample",
    ".dockerignore",
    ".npmrc",
    ".yarnrc",
    ".nvmrc",
    ".python-version",
    ".ruby-version",
    ".node-version",
    ".tool-versions",
}

# Directories to always skip
BLOCKED_DIRECTORIES: Set[str] = {
    # Version control
    ".git", ".svn", ".hg", ".bzr",
    # Dependencies
    "node_modules", "vendor", "bower_components",
    "__pycache__", ".pyc", "venv", ".venv", "env", ".env",
    "site-packages", "pip-wheel-metadata",
    # Build outputs
    "dist", "build", "out", "output", "target",
    ".next", ".nuxt", ".output", ".vercel", ".netlify",
    "_build", "public/build", "static/build",
    # IDE/Editor
    ".idea", ".vscode", ".vs", ".eclipse", ".settings",
    # Cache
    ".cache", ".parcel-cache", ".turbo", ".nx",
    ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".nox", ".coverage",
    "coverage", "htmlcov",
    # Temp
    "tmp", "temp", ".tmp",
    # Documentation build
    "_site", ".docusaurus", ".vuepress", ".vitepress",
    # Migrations
    "migrations", "migrate", "alembic",
    # Test snapshots and fixtures
    "__snapshots__", "fixtures", "testdata", "test_data",
    # Generated code
    "generated", "__generated__", "codegen",
    # Translations / i18n (repetitive near-identical files)
    "locales", "i18n", "translations",
    # Git hooks
    ".husky",
    # Misc
    "logs", ".terraform", ".serverless",
}

# All blocked extensions combined
BLOCKED_EXTENSIONS: Set[str] = (
    BINARY_EXTENSIONS |
    IMAGE_EXTENSIONS |
    FONT_EXTENSIONS |
    MEDIA_EXTENSIONS |
    ARCHIVE_EXTENSIONS |
    DATA_EXTENSIONS
)


def is_blocked_extension(ext: str) -> bool:
    """Check if extension is in blocklist."""
    return ext.lower() in BLOCKED_EXTENSIONS


def is_lock_file(filename: str) -> bool:
    """Check if filename is a lock file."""
    return filename in LOCK_FILENAMES


def is_generated_file(filename: str) -> bool:
    """Check if filename matches generated file patterns."""
    for pattern in GENERATED_PATTERNS:
        if fnmatch.fnmatch(filename.lower(), pattern.lower()):
            return True
    return False


def is_hidden_file(filename: str) -> bool:
    """Check if file is hidden (starts with dot) but not in allowed list."""
    if not filename.startswith("."):
        return False
    return filename not in ALLOWED_HIDDEN_FILES


def is_blocked_directory(dirname: str) -> bool:
    """Check if directory should be skipped."""
    return dirname in BLOCKED_DIRECTORIES or dirname.startswith(".")
