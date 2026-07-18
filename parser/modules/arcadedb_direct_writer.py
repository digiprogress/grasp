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
import hashlib
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


# ─── element identity (graph diff) ─────────────────────────────────────────
# anchor_key: a stable, line-free name for a code element — the identity the
# diff engine compares across parses. Stored as a plain property; NOT a
# uniqueness constraint (same-named elements may coexist, INSERTs don't care).
def anchor_key(kind: str, file_path: str, name: str, class_name: Optional[str] = None) -> str:
    if kind == "method":
        return f"method:{file_path}:{class_name or ''}.{name}"
    return f"{kind}:{file_path}:{name}"


# signature_hash: change detector, not identity. Same anchor + different hash
# = the element's interface changed. Built from interface-shape fields only
# (params, return type, flags, parents) — deliberately not the body, so a
# body-only edit does not read as a change. The `sig1:` prefix versions the
# algorithm: if the basis ever changes, old hashes are detectably stale
# instead of silently flagging every element as changed.
def _sig_hash(kind: str, el: Dict[str, Any]) -> str:
    if kind == "function":
        basis = (
            f"function|{el.get('name')}|p={el.get('params')}|rt={el.get('return_type')}"
            f"|async={bool(el.get('is_async'))}|exp={bool(el.get('is_exported'))}"
        )
    elif kind == "method":
        basis = (
            f"method|{el.get('class_name')}.{el.get('name')}|p={el.get('params')}"
            f"|rt={el.get('return_type')}|async={bool(el.get('is_async'))}"
        )
    elif kind == "class":
        parents = sorted(str(p.get("name")) for p in (el.get("resolved_parents") or []) if p.get("name"))
        impls = sorted(str(i) for i in (el.get("implements") or []))
        basis = f"class|{el.get('name')}|ext={','.join(parents)}|impl={','.join(impls)}"
    else:  # interface / enum — name is all the parser captures today
        basis = f"{kind}|{el.get('name')}"
    return "sig1:" + hashlib.sha1(basis.encode("utf-8")).hexdigest()[:16]


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
        "`Function`.params STRING", "Method.params STRING",
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
        # Element identity for the graph diff (see anchor_key/_sig_hash above)
        "`Function`.anchor_key STRING", "`Function`.signature_hash STRING",
        "Method.anchor_key STRING", "Method.signature_hash STRING",
        "Class.anchor_key STRING", "Class.signature_hash STRING",
        "Interface.anchor_key STRING", "Interface.signature_hash STRING",
        "Enum.anchor_key STRING", "Enum.signature_hash STRING",
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

    # ─── graph diff support ────────────────────────────────────────
    def read_element_state(
        self, project: str, file_paths: Optional[List[str]] = None
    ) -> Dict[str, List[str]]:
        """Snapshot {anchor_key: [signature_hash, ...]} for the code elements
        currently in the graph, optionally scoped to a set of files. A list per
        anchor because plain INSERTs let same-named elements coexist. Returns
        {} when the project DB doesn't exist yet (first parse). Call BEFORE
        write() — on a clean/full parse write() drops the old graph.
        """
        db = _sanitize_db_name(project)
        try:
            if db not in self._list_databases():
                return {}
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{db}] read_element_state: database list failed: {e}")
            return {}
        where = ""
        if file_paths:
            plist = ", ".join(_sql_str(p) for p in file_paths)
            where = f" WHERE file_path IN [{plist}]"
        state: Dict[str, List[str]] = {}
        for vt in ("`Function`", "Method", "Class", "Interface", "Enum"):
            try:
                r = self._sql(db, f"SELECT anchor_key, signature_hash FROM {vt}{where}")
                if r.status_code >= 300:
                    logger.warning(f"[{db}] read_element_state({vt}): {r.status_code} {r.text[:150]}")
                    continue
                for row in r.json().get("result", []):
                    ak = row.get("anchor_key")
                    if ak:  # rows from before this feature carry no anchor
                        state.setdefault(ak, []).append(row.get("signature_hash"))
            except Exception as e:  # noqa: BLE001
                logger.warning(f"[{db}] read_element_state({vt}) failed: {e}")
        return state

    # DiffLog lives in the primary `grasp` DB (the one docker-compose seeds),
    # NOT in the per-project DB: full parses drop the project DB, and a log
    # that dies with every rebuild is not a log. Read it back with the MCP
    # `query` tool: SELECT FROM DiffLog WHERE project = '<p>' ORDER BY ts DESC.
    DIFFLOG_DB = "grasp"
    DIFFLOG_MAX_ENTRIES = 500

    def write_diff_log(self, project: str, diff: Any, trigger: str = "manual") -> None:
        """Persist one DiffLog row for a parse. Best-effort: a logging failure
        must never fail the parse."""
        try:
            from datetime import datetime, timezone

            self._sql_script(self.DIFFLOG_DB, [
                "CREATE VERTEX TYPE DiffLog IF NOT EXISTS BUCKETS 8",
                "CREATE PROPERTY DiffLog.project IF NOT EXISTS STRING",
                "CREATE PROPERTY DiffLog.ts IF NOT EXISTS STRING",
                "CREATE PROPERTY DiffLog.trigger IF NOT EXISTS STRING",
                "CREATE PROPERTY DiffLog.summary IF NOT EXISTS STRING",
                "CREATE PROPERTY DiffLog.entries IF NOT EXISTS STRING",
                "CREATE INDEX IF NOT EXISTS ON DiffLog (project) NOTUNIQUE",
            ])
            entries = {
                "added": diff.added[: self.DIFFLOG_MAX_ENTRIES],
                "changed": diff.changed[: self.DIFFLOG_MAX_ENTRIES],
                "deleted": diff.deleted[: self.DIFFLOG_MAX_ENTRIES],
            }
            truncated = {
                k: len(getattr(diff, k)) - len(v)
                for k, v in entries.items()
                if len(getattr(diff, k)) > len(v)
            }
            summary = diff.summary()
            if truncated:
                summary["entries_truncated"] = truncated  # never lie by omission
            r = self._sql(
                self.DIFFLOG_DB,
                "INSERT INTO DiffLog SET "
                f"project = {_sql_str(_sanitize_db_name(project))}, "
                f"ts = {_sql_str(datetime.now(timezone.utc).isoformat())}, "
                f"trigger = {_sql_str(trigger)}, "
                f"summary = {_sql_str(json.dumps(summary))}, "
                f"entries = {_sql_str(json.dumps(entries))}",
            )
            if r.status_code >= 300:
                logger.warning(f"DiffLog write failed: {r.status_code} {r.text[:150]}")
        except Exception as e:  # noqa: BLE001
            logger.warning(f"DiffLog write skipped: {e}")

    # ─── incremental support ───────────────────────────────────────
    def read_origin_sha(self, project: str) -> Optional[str]:
        """The commit SHA the graph was last built from (Origin vertex), or
        None when the project has never been parsed. This is the base the
        incremental path diffs against — the graph itself remembers where it
        stands, so triggers don't have to."""
        db = _sanitize_db_name(project)
        try:
            if db not in self._list_databases():
                return None
            r = self._sql(db, "SELECT commit_sha FROM Origin WHERE id = 'origin'")
            if r.status_code < 300:
                rows = r.json().get("result", [])
                return (rows[0].get("commit_sha") or None) if rows else None
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{db}] read_origin_sha failed: {e}")
        return None

    def read_class_symbols(self, project: str, exclude_files: Optional[Set[str]] = None) -> List[Dict[str, Any]]:
        """Class symbols ({name, file_path, line}) from the existing graph,
        minus the files being re-parsed — the resolver's stand-in for the
        repo-wide symbol table it would otherwise need a full parse to build."""
        db = _sanitize_db_name(project)
        out: List[Dict[str, Any]] = []
        try:
            r = self._sql(db, "SELECT name, file_path, line FROM Class")
            if r.status_code >= 300:
                logger.warning(f"[{db}] read_class_symbols: {r.status_code} {r.text[:150]}")
                return out
            skip = exclude_files or set()
            for row in r.json().get("result", []):
                if row.get("name") and row.get("file_path") and row["file_path"] not in skip:
                    out.append({"name": row["name"], "file_path": row["file_path"], "line": row.get("line") or 0})
        except Exception as e:  # noqa: BLE001
            logger.warning(f"[{db}] read_class_symbols failed: {e}")
        return out

    def read_incoming_edges(self, project: str, file_paths: List[str]) -> List[Dict[str, Any]]:
        """Capture the cross-file edges POINTING INTO the given files, before
        delete_files() severs them with the vertices. The unchanged side of
        each edge still exists after the rewrite, so these records are enough
        to re-tie the graph: restore_edges() replays them once the files'
        fresh vertices are written."""
        db = _sanitize_db_name(project)
        edges: List[Dict[str, Any]] = []
        queries = [
            # other file --IMPORTS--> this file
            ("IMPORTS", lambda fp: (
                f"SELECT outV().path AS src FROM (SELECT expand(inE('IMPORTS')) FROM File WHERE path = {_sql_str(fp)})"
            )),
            # other file's class --INHERITS--> a class in this file
            ("INHERITS", lambda fp: (
                "SELECT outV().file_path AS src_file, outV().name AS src_name, inV().name AS dst_name "
                f"FROM (SELECT expand(inE('INHERITS')) FROM Class WHERE file_path = {_sql_str(fp)})"
            )),
            # other file's class --IMPLEMENTS--> an interface in this file
            ("IMPLEMENTS", lambda fp: (
                "SELECT outV().file_path AS src_file, outV().name AS src_name, inV().name AS dst_name "
                f"FROM (SELECT expand(inE('IMPLEMENTS')) FROM Interface WHERE file_path = {_sql_str(fp)})"
            )),
        ]
        for fp in file_paths:
            for etype, q in queries:
                try:
                    r = self._sql(db, q(fp))
                    if r.status_code >= 300:
                        continue
                    for row in r.json().get("result", []):
                        if etype == "IMPORTS":
                            if row.get("src"):
                                edges.append({"type": "IMPORTS", "src_file": row["src"], "dst_file": fp})
                        elif row.get("src_file") and row.get("src_name") and row.get("dst_name"):
                            edges.append({
                                "type": etype, "src_file": row["src_file"],
                                "src_name": row["src_name"], "dst_file": fp,
                                "dst_name": row["dst_name"],
                            })
                except Exception as e:  # noqa: BLE001
                    logger.warning(f"[{db}] read_incoming_edges({etype}, {fp}) failed: {e}")
        return edges

    def restore_edges(
        self, project: str, edges: List[Dict[str, Any]], skip_src_files: Optional[Set[str]] = None
    ) -> Tuple[int, int]:
        """Replay captured incoming edges after the rewrite. Edges whose source
        file was itself re-parsed are skipped — write() already rebuilt that
        file's outgoing edges from the fresh parse. A restore whose target
        vanished (element deleted/renamed) is a silent no-op on this ArcadeDB
        build (empty endpoint set → no edge, no error): counted as dropped,
        and the graph diff already reports that element as deleted."""
        db = _sanitize_db_name(project)
        skip = skip_src_files or set()
        restored = dropped = 0
        for e in edges:
            if e["src_file"] in skip:
                continue
            if e["type"] == "IMPORTS":
                stmt = (
                    "CREATE EDGE IMPORTS "
                    f"FROM (SELECT FROM File WHERE path = {_sql_str(e['src_file'])}) "
                    f"TO (SELECT FROM File WHERE path = {_sql_str(e['dst_file'])})"
                )
            else:
                dst_type = "Class" if e["type"] == "INHERITS" else "Interface"
                stmt = (
                    f"CREATE EDGE {e['type']} "
                    f"FROM (SELECT FROM Class WHERE file_path = {_sql_str(e['src_file'])} AND name = {_sql_str(e['src_name'])}) "
                    f"TO (SELECT FROM {dst_type} WHERE file_path = {_sql_str(e['dst_file'])} AND name = {_sql_str(e['dst_name'])})"
                )
            try:
                r = self._sql(db, stmt)
                if r.status_code < 300 and r.json().get("result"):
                    restored += 1
                else:
                    dropped += 1
            except Exception as ex:  # noqa: BLE001
                logger.warning(f"[{db}] edge restore failed ({e['type']} {e['src_file']}): {ex}")
                dropped += 1
        if restored or dropped:
            logger.info(f"[{db}] edge restore: {restored} restored, {dropped} dropped (target vanished)")
        return restored, dropped

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
        # Directory and File are UPSERTs (backed by their unique path
        # indexes): an incremental write lands in a LIVE graph where these
        # rows already exist, and a plain INSERT would violate the index.
        # Elements stay plain INSERTs — in incremental mode delete_files()
        # cleared the touched files' elements first, and a full parse starts
        # from a dropped DB. (Verified on this ArcadeDB build: rows UPSERTed
        # earlier in a sqlscript ARE visible to CREATE EDGE endpoint
        # subqueries later in the same script.)
        stmts: List[str] = []
        for d in sorted(dir_paths):
            depth = d.count("/") + 1
            name = d.rsplit("/", 1)[-1]
            stmts.append(
                f"UPDATE Directory SET path = {_sql_str(d)}, name = {_sql_str(name)}, "
                f"depth = {depth} UPSERT WHERE path = {_sql_str(d)}"
            )
        result.directories = len(dir_paths)

        for f in files:
            fp = f.get("file_path", "")
            stmts.append(
                "UPDATE File SET "
                f"path = {_sql_str(fp)}, "
                f"name = {_sql_str(os.path.basename(fp))}, "
                f"language = {_sql_str(f.get('language', 'unknown'))}, "
                f"domain = {_sql_str(f.get('domain'))}, "
                f"summary = {_sql_str(f.get('summary'))} "
                f"UPSERT WHERE path = {_sql_str(fp)}"
            )
            result.files += 1

            for fn in f.get("functions", []):
                stmts.append(
                    "INSERT INTO `Function` SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(fn.get('name'))}, "
                    f"params = {_sql_str(fn.get('params'))}, "
                    f"line = {int(fn.get('line') or 0)}, end_line = {int(fn.get('end_line') or 0)}, "
                    f"is_exported = {str(bool(fn.get('is_exported'))).lower()}, "
                    f"is_async = {str(bool(fn.get('is_async'))).lower()}, "
                    f"return_type = {_sql_str(fn.get('return_type'))}, "
                    f"anchor_key = {_sql_str(anchor_key('function', fp, fn.get('name')))}, "
                    f"signature_hash = {_sql_str(_sig_hash('function', fn))}"
                )
                result.functions += 1

            for m in f.get("methods", []):
                stmts.append(
                    "INSERT INTO Method SET "
                    f"file_path = {_sql_str(fp)}, class_name = {_sql_str(m.get('class_name'))}, "
                    f"name = {_sql_str(m.get('name'))}, "
                    f"params = {_sql_str(m.get('params'))}, "
                    f"line = {int(m.get('line') or 0)}, end_line = {int(m.get('end_line') or 0)}, "
                    f"is_async = {str(bool(m.get('is_async'))).lower()}, "
                    f"return_type = {_sql_str(m.get('return_type'))}, "
                    f"anchor_key = {_sql_str(anchor_key('method', fp, m.get('name'), m.get('class_name')))}, "
                    f"signature_hash = {_sql_str(_sig_hash('method', m))}"
                )
                result.methods += 1

            for c in f.get("classes", []):
                stmts.append(
                    "INSERT INTO Class SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(c.get('name'))}, "
                    f"line = {int(c.get('line') or 0)}, end_line = {int(c.get('end_line') or 0)}, "
                    f"anchor_key = {_sql_str(anchor_key('class', fp, c.get('name')))}, "
                    f"signature_hash = {_sql_str(_sig_hash('class', c))}"
                )
                result.classes += 1

            for i in f.get("interfaces", []):
                if not i.get("is_exported"):
                    continue
                stmts.append(
                    "INSERT INTO Interface SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(i.get('name'))}, "
                    f"line = {int(i.get('line') or 0)}, end_line = {int(i.get('end_line') or 0)}, "
                    f"anchor_key = {_sql_str(anchor_key('interface', fp, i.get('name')))}, "
                    f"signature_hash = {_sql_str(_sig_hash('interface', i))}"
                )
                result.interfaces += 1

            for e in f.get("enums", []):
                if not e.get("is_exported"):
                    continue
                stmts.append(
                    "INSERT INTO Enum SET "
                    f"file_path = {_sql_str(fp)}, name = {_sql_str(e.get('name'))}, "
                    f"line = {int(e.get('line') or 0)}, end_line = {int(e.get('end_line') or 0)}, "
                    f"anchor_key = {_sql_str(anchor_key('enum', fp, e.get('name')))}, "
                    f"signature_hash = {_sql_str(_sig_hash('enum', e))}"
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
