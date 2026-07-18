"""
ArcadeDB direct writer — talks to raw ArcadeDB HTTP API.

Talks straight to the ArcadeDB HTTP API — no middleman service.
Local-first, single-user, no auth beyond ArcadeDB root credentials.

Each project maps to one ArcadeDB database. Schema (vertex/edge types + props)
is created on demand. Writes are batched into SQL scripts sent via
/api/v1/command/{db}. A per-project 'clean' flag drops the database first for
a fresh re-parse.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

import httpx

logger = logging.getLogger(__name__)


# ─── result ────────────────────────────────────────────────────────────────
@dataclass
class WriteResult:
    directories: int = 0
    files: int = 0
    functions: int = 0
    methods: int = 0
    classes: int = 0
    interfaces: int = 0
    enums: int = 0
    inherits: int = 0
    imports: int = 0
    errors: int = 0
    success: bool = True
    error: Optional[str] = None


# ─── helpers ──────────────────────────────────────────────────────────────
def _sanitize_db_name(name: str) -> str:
    """ArcadeDB database names allow letters, digits, underscore, hyphen, dot."""
    safe = re.sub(r"[^A-Za-z0-9_.\-]", "_", name)
    return safe[:64] or "default"


def _sql_str(s: Any) -> str:
    if s is None:
        return "NULL"
    if isinstance(s, bool):
        return "true" if s else "false"
    if isinstance(s, (int, float)):
        return str(s)
    return "'" + str(s).replace("\\", "\\\\").replace("'", "\\'").replace("\n", "\\n") + "'"


def _dir_parts(path: str) -> List[str]:
    """Split a/b/c/foo.py → ['a', 'a/b', 'a/b/c']"""
    parts = [p for p in path.split("/")[:-1] if p]
    return ["/".join(parts[: i + 1]) for i in range(len(parts))]


# ─── writer ───────────────────────────────────────────────────────────────
class ArcadeDBDirectWriter:
    """Direct-to-ArcadeDB implementation of the parser writer contract."""

    # Schema definitions — mirrored from the legacy manager
    VERTEX_TYPES = [
        "Directory", "File", "Function", "Method", "Class",
        "Interface", "Enum", "Origin",
    ]
    EDGE_TYPES = [
        "CONTAINS", "IMPORTS", "INHERITS", "IMPLEMENTS",
    ]
    PROPERTIES = [
        # Directory
        "Directory.path STRING", "Directory.name STRING", "Directory.depth INTEGER",
        # File
        "File.path STRING", "File.name STRING", "File.language STRING",
        "File.domain STRING", "File.summary STRING",
        # Provenance: single-row record of where this graph came from. The
        # clone is temporary, the graph is not — without this a graph can
        # outlive its source with no way to tell what revision it maps.
        "Origin.id STRING", "Origin.repo STRING", "Origin.commit_sha STRING",
        "Origin.parsed_at STRING", "Origin.files INTEGER",
        # Function
        "`Function`.file_path STRING", "`Function`.name STRING",
        "`Function`.line INTEGER", "`Function`.end_line INTEGER",
        "`Function`.is_exported BOOLEAN", "`Function`.is_async BOOLEAN",
        "`Function`.return_type STRING",
        # Method
        "Method.file_path STRING", "Method.class_name STRING", "Method.name STRING",
        "Method.line INTEGER", "Method.end_line INTEGER",
        "Method.is_async BOOLEAN", "Method.return_type STRING",
        # Class
        "Class.file_path STRING", "Class.name STRING",
        "Class.line INTEGER", "Class.end_line INTEGER",
        # Interface / Enum
        "Interface.file_path STRING", "Interface.name STRING",
        "Interface.line INTEGER", "Interface.end_line INTEGER",
        "Enum.file_path STRING", "Enum.name STRING",
        "Enum.line INTEGER", "Enum.end_line INTEGER",
    ]
    INDEXES = [
        # Unique keys make the "check-then-create" idempotency work.
        "Directory[path]", "File[path]",
    ]

    def __init__(
        self,
        base_url: str,
        internal_secret: str = "",  # unused, kept for compat
        timeout: int = 120,
        clean: bool = False,
        username: str = "root",
        password: Optional[str] = None,
    ):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.clean = clean
        pwd = password or os.environ.get("ARCADEDB_ROOT_PASSWORD", "")
        self._auth = ("Basic " + base64.b64encode(f"{username}:{pwd}".encode()).decode())
        self._headers = {"Content-Type": "application/json", "Authorization": self._auth}
        self._client = httpx.Client(timeout=timeout, headers=self._headers)

    # ─── server APIs ────────────────────────────────────────────────
    def _server_cmd(self, command: str) -> httpx.Response:
        return self._client.post(f"{self.base_url}/api/v1/server", json={"command": command})

    def _sql(self, db: str, statement: str, language: str = "sql") -> httpx.Response:
        return self._client.post(
            f"{self.base_url}/api/v1/command/{db}",
            json={"language": language, "command": statement},
        )

    def _sql_script(self, db: str, statements: List[str]) -> httpx.Response:
        """Batch multiple SQL statements as a single sqlscript execution."""
        script = ";\n".join(statements) + ";"
        return self._client.post(
            f"{self.base_url}/api/v1/command/{db}",
            json={"language": "sqlscript", "command": script},
        )

    # ─── database + schema ──────────────────────────────────────────
    def _list_databases(self) -> Set[str]:
        r = self._client.get(f"{self.base_url}/api/v1/databases")
        r.raise_for_status()
        return set(r.json().get("result", []))

    def _ensure_database(self, db: str) -> None:
        if db in self._list_databases():
            if self.clean:
                logger.info(f"[{db}] clean=True → dropping existing database")
                self._server_cmd(f"drop database {db}")
                self._server_cmd(f"create database {db}")
            return
        logger.info(f"[{db}] creating database")
        r = self._server_cmd(f"create database {db}")
        if r.status_code >= 300:
            raise RuntimeError(f"create database failed: {r.status_code} {r.text}")

    def _ensure_schema(self, db: str) -> None:
        stmts: List[str] = []
        for v in self.VERTEX_TYPES:
            name = f"`{v}`" if v == "Function" else v
            stmts.append(f"CREATE VERTEX TYPE {name} IF NOT EXISTS BUCKETS 8")
        for e in self.EDGE_TYPES:
            stmts.append(f"CREATE EDGE TYPE {e} IF NOT EXISTS BUCKETS 8")
        for prop in self.PROPERTIES:
            # PROPERTIES entries look like "TypeName.field TYPE"; ArcadeDB SQL wants
            # IF NOT EXISTS *before* the datatype, not after.
            name, dtype = prop.rsplit(" ", 1)
            stmts.append(f"CREATE PROPERTY {name} IF NOT EXISTS {dtype}")
        # Unique indexes for the key vertex types
        stmts.append("CREATE INDEX IF NOT EXISTS ON Directory (path) UNIQUE")
        stmts.append("CREATE INDEX IF NOT EXISTS ON File (path) UNIQUE")
        stmts.append("CREATE INDEX IF NOT EXISTS ON Origin (id) UNIQUE")
        # Edge-write lookup indexes. Every CREATE EDGE below resolves its
        # endpoints with subqueries like
        #   (SELECT FROM `Function` WHERE file_path = .. AND name = .. AND line = ..)
        # Without these, each of the ~70k+ element edges on a large repo does a
        # full scan over a 30-40k-row type — measured on hermes-agent, that
        # turned the edge phase from minutes into ~2 hours. NOTUNIQUE on
        # purpose: these are lookup accelerators, not identity constraints
        # (same-named elements may legitimately coexist; INSERTs don't care).
        for vt in ("`Function`", "Method", "Class", "Interface", "Enum"):
            stmts.append(f"CREATE INDEX IF NOT EXISTS ON {vt} (file_path, name, line) NOTUNIQUE")
        stmts.append("CREATE INDEX IF NOT EXISTS ON Class (file_path, name) NOTUNIQUE")   # INHERITS target
        stmts.append("CREATE INDEX IF NOT EXISTS ON Interface (name) NOTUNIQUE")          # IMPLEMENTS target
        self._sql_script(db, stmts)

    # ─── main entry ────────────────────────────────────────────────
    def write(
        self,
        user_id: str,
        project: str,
        files: Optional[List[Dict[str, Any]]] = None,
        on_progress: Optional[Callable[[int, int, WriteResult], None]] = None,
        origin: Optional[Dict[str, str]] = None,
    ) -> WriteResult:
        result = WriteResult()
        files = files or []
        db = _sanitize_db_name(project)

        try:
            self._ensure_database(db)
            self._ensure_schema(db)
        except Exception as e:
            logger.error(f"schema setup failed: {e}")
            result.success = False
            result.error = str(e)
            return result

        # Provenance first: even if element writes fail later, the graph
        # should record what it was built from. UPSERT keyed on the unique
        # Origin(id) index so re-parses update the single row in place.
        if origin:
            r = self._sql(
                db,
                f"UPDATE Origin SET id = 'origin', "
                f"repo = {_sql_str(origin.get('repo'))}, "
                f"commit_sha = {_sql_str(origin.get('commit'))}, "
                f"parsed_at = {_sql_str(origin.get('parsed_at'))}, "
                f"files = {len(files)} "
                f"UPSERT WHERE id = 'origin'",
            )
            if r.status_code >= 300:
                logger.warning(f"[{db}] origin write failed: {r.status_code} {r.text[:150]}")

        # Collect unique directory paths
        dir_paths: Set[str] = set()
        for f in files:
            for d in _dir_parts(f.get("file_path", "")):
                dir_paths.add(d)

        # ── vertices ────────────────────────────────────────────────
        stmts: List[str] = []
        for d in sorted(dir_paths):
            depth = d.count("/") + 1
            name = d.rsplit("/", 1)[-1]
            stmts.append(
                f"INSERT INTO Directory SET path = {_sql_str(d)}, name = {_sql_str(name)}, depth = {depth}"
            )
        result.directories = len(dir_paths)

        for f in files:
            fp = f.get("file_path", "")
            stmts.append(
                "INSERT INTO File SET "
                f"path = {_sql_str(fp)}, "
                f"name = {_sql_str(os.path.basename(fp))}, "
                f"language = {_sql_str(f.get('language', 'unknown'))}, "
                f"domain = {_sql_str(f.get('domain'))}, "
                f"summary = {_sql_str(f.get('summary'))}"
            )
            result.files += 1

            for fn in f.get("functions", []):
                stmts.append(
                    "INSERT INTO `Function` SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(fn.get('name'))}, "
                    f"line = {int(fn.get('line') or 0)}, end_line = {int(fn.get('end_line') or 0)}, "
                    f"is_exported = {str(bool(fn.get('is_exported'))).lower()}, "
                    f"is_async = {str(bool(fn.get('is_async'))).lower()}, "
                    f"return_type = {_sql_str(fn.get('return_type'))}"
                )
                result.functions += 1

            for m in f.get("methods", []):
                stmts.append(
                    "INSERT INTO Method SET "
                    f"file_path = {_sql_str(fp)}, class_name = {_sql_str(m.get('class_name'))}, "
                    f"name = {_sql_str(m.get('name'))}, "
                    f"line = {int(m.get('line') or 0)}, end_line = {int(m.get('end_line') or 0)}, "
                    f"is_async = {str(bool(m.get('is_async'))).lower()}, "
                    f"return_type = {_sql_str(m.get('return_type'))}"
                )
                result.methods += 1

            for c in f.get("classes", []):
                stmts.append(
                    "INSERT INTO Class SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(c.get('name'))}, "
                    f"line = {int(c.get('line') or 0)}, end_line = {int(c.get('end_line') or 0)}"
                )
                result.classes += 1

            for i in f.get("interfaces", []):
                if not i.get("is_exported"):
                    continue
                stmts.append(
                    "INSERT INTO Interface SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(i.get('name'))}, "
                    f"line = {int(i.get('line') or 0)}, end_line = {int(i.get('end_line') or 0)}"
                )
                result.interfaces += 1

            for e in f.get("enums", []):
                if not e.get("is_exported"):
                    continue
                stmts.append(
                    "INSERT INTO Enum SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(e.get('name'))}, "
                    f"line = {int(e.get('line') or 0)}, end_line = {int(e.get('end_line') or 0)}"
                )
                result.enums += 1

        # ── directory hierarchy (CONTAINS) ─────────────────────────
        for d in sorted(dir_paths):
            if "/" in d:
                parent = d.rsplit("/", 1)[0]
                stmts.append(
                    "CREATE EDGE `CONTAINS` "
                    f"FROM (SELECT FROM Directory WHERE path = {_sql_str(parent)}) "
                    f"TO (SELECT FROM Directory WHERE path = {_sql_str(d)})"
                )

        # dir → file
        for f in files:
            fp = f.get("file_path", "")
            parent = "/".join(fp.split("/")[:-1])
            if parent:
                stmts.append(
                    "CREATE EDGE `CONTAINS` "
                    f"FROM (SELECT FROM Directory WHERE path = {_sql_str(parent)}) "
                    f"TO (SELECT FROM File WHERE path = {_sql_str(fp)})"
                )

        # file → function/method/class/interface/enum
        for f in files:
            fp = f.get("file_path", "")
            for kind, items, id_field in [
                ("`Function`", f.get("functions", []), "name"),
                ("Method", f.get("methods", []), "name"),
                ("Class", f.get("classes", []), "name"),
                ("Interface", [i for i in f.get("interfaces", []) if i.get("is_exported")], "name"),
                ("Enum", [e for e in f.get("enums", []) if e.get("is_exported")], "name"),
            ]:
                for it in items:
                    name = it.get(id_field)
                    line = int(it.get("line") or 0)
                    stmts.append(
                        "CREATE EDGE `CONTAINS` "
                        f"FROM (SELECT FROM File WHERE path = {_sql_str(fp)}) "
                        f"TO (SELECT FROM {kind} WHERE file_path = {_sql_str(fp)} AND name = {_sql_str(name)} AND line = {line})"
                    )

        # ── IMPORTS edges (file → file) ─────────────────────────────
        for f in files:
            fp = f.get("file_path", "")
            seen: Set[str] = set()
            for imp in f.get("imports", []):
                target = imp.get("resolved_file")
                if not target or target == fp or target in seen:
                    continue
                seen.add(target)
                stmts.append(
                    "CREATE EDGE IMPORTS "
                    f"FROM (SELECT FROM File WHERE path = {_sql_str(fp)}) "
                    f"TO (SELECT FROM File WHERE path = {_sql_str(target)})"
                )
                result.imports += 1

        # ── INHERITS + IMPLEMENTS edges (class → class/interface) ──
        for f in files:
            fp = f.get("file_path", "")
            for cls in f.get("classes", []):
                cls_name = cls.get("name")
                cls_line = int(cls.get("line") or 0)
                for parent in cls.get("resolved_parents", []):
                    if parent.get("confidence") == "unresolved":
                        continue
                    parent_file = parent.get("file")
                    parent_name = parent.get("name")
                    if not parent_file or not parent_name:
                        continue
                    stmts.append(
                        "CREATE EDGE INHERITS "
                        f"FROM (SELECT FROM Class WHERE file_path = {_sql_str(fp)} AND name = {_sql_str(cls_name)} AND line = {cls_line}) "
                        f"TO (SELECT FROM Class WHERE file_path = {_sql_str(parent_file)} AND name = {_sql_str(parent_name)})"
                    )
                    result.inherits += 1
                # implements — string names only (no cross-file resolution baked in)
                for iface in cls.get("implements", []) or []:
                    stmts.append(
                        "CREATE EDGE IMPLEMENTS "
                        f"FROM (SELECT FROM Class WHERE file_path = {_sql_str(fp)} AND name = {_sql_str(cls_name)} AND line = {cls_line}) "
                        f"TO (SELECT FROM Interface WHERE name = {_sql_str(iface)})"
                    )

        # ── send in chunks so ArcadeDB doesn't choke on 50k+ script ──
        CHUNK = 500
        total = len(stmts)
        sent = 0
        for i in range(0, total, CHUNK):
            batch = stmts[i : i + CHUNK]
            try:
                r = self._sql_script(db, batch)
                if r.status_code >= 300:
                    logger.error(f"batch {i//CHUNK} failed: {r.status_code} {r.text[:200]}")
                    result.errors += 1
                    result.success = False
            except Exception as e:
                logger.error(f"batch {i//CHUNK} exception: {e}")
                result.errors += 1
                result.success = False
            sent = min(i + CHUNK, total)
            if on_progress:
                on_progress(sent, total, result)
            logger.info(f"[{db}] write progress: {sent}/{total} statements")

        return result

    def delete_files(self, user_id: str, project: str, files: List[str]) -> bool:
        """Delete file vertices (and their contained elements) for incremental removes."""
        if not files:
            return True
        db = _sanitize_db_name(project)
        stmts: List[str] = []
        for fp in files:
            # Delete owned elements first, then the File vertex itself
            for kind in ("`Function`", "Method", "Class", "Interface", "Enum"):
                stmts.append(f"DELETE FROM {kind} WHERE file_path = {_sql_str(fp)}")
            stmts.append(f"DELETE VERTEX File WHERE path = {_sql_str(fp)}")
        try:
            r = self._sql_script(db, stmts)
            if r.status_code >= 300:
                logger.error(f"delete_files failed: {r.status_code} {r.text[:200]}")
                return False
            return True
        except Exception as e:
            logger.error(f"delete_files exception: {e}")
            return False
