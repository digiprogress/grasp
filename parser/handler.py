"""
Grasp Parser Handler — modular tree-sitter analysis pipeline.

Orchestrates the full analysis pipeline:
1. Code Analysis: clone → discover → parse in parallel → resolve →
   assign domains (depth-1) → write graph (+ origin provenance)
2. Incremental: clone → derive changed/removed from git (graph's Origin SHA
   → HEAD) → parse ONLY changed files (resolver context comes from the graph
   + discovery) → capture incoming edges → per-file delete + rewrite →
   restore edges → graph diff

The graph (structural + keyword search over ArcadeDB) is the primary
retrieval mechanism. All heavy lifting lives in modules/.

Entry points:
  handle_code_analysis(input_data) — pure function, called by the local
                                     HTTP server (`local_entry.py`).
  handler(event)                    — legacy serverless entry, present so
                                     the same file can be dropped into a
                                     serverless runtime; not used in local
                                     mode.
"""

import os
import logging
import tempfile
import subprocess
from datetime import datetime, timezone
import shutil
import time
import multiprocessing
from typing import Dict, Any, List
from multiprocessing import Pool, cpu_count
import httpx

from modules import (
    TreeSitterParser, ArcadeDBWriter,
    FileDiscoveryConfig, resolve_references,
)
from modules.file_discovery import (
    BLOCKED_DIRECTORIES, BLOCKED_EXTENSIONS, LOCK_FILENAMES,
    is_generated_file, is_hidden_file
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)


def report_progress(arcadedb_url, internal_secret, user_id, project, stage, detail=None):
    """Fire-and-forget progress ping (best-effort; used when a caller wants
    stage updates). Silently no-ops if the endpoint is not reachable."""
    try:
        httpx.post(f"{arcadedb_url}/internal/progress", headers={"X-Internal-Secret": internal_secret, "X-User-Id": user_id, "Content-Type": "application/json"}, json={"project": project, "stage": stage, "detail": detail or {}}, timeout=5)
    except Exception:
        pass


def report_completion(arcadedb_url, internal_secret, user_id, project_name, result):
    """Report pipeline completion to auth service so Postgres status gets updated."""
    status = "completed" if result.get("status") == "completed" else "failed"
    body = {"user_id": user_id, "project_name": project_name, "status": status}
    if status == "completed":
        stats = result.get("arcadedb_stats", {})
        body["stats"] = {"files": stats.get("files", 0), "functions": stats.get("functions", 0), "classes": stats.get("classes", 0)}
    else:
        body["error"] = result.get("error", "Unknown error")

    headers = {"X-Internal-Secret": internal_secret, "X-User-Id": user_id, "Content-Type": "application/json"}
    for attempt in range(3):
        try:
            resp = httpx.post(f"{arcadedb_url}/internal/ingest/complete", headers=headers, json=body, timeout=15)
            if resp.status_code == 200:
                logger.info(f"Reported completion to auth service: {status} for {project_name}")
                return
            logger.error(f"Completion callback failed (attempt {attempt+1}): HTTP {resp.status_code} - {resp.text}")
        except Exception as e:
            logger.error(f"Completion callback error (attempt {attempt+1}): {e}")
        if attempt < 2:
            import time
            time.sleep(2)
    logger.error(f"CRITICAL: Failed to report completion after 3 attempts for {project_name} - status will be stuck on processing")


def handler(event: Dict[str, Any]) -> Dict[str, Any]:
    """
    Main handler - routes to appropriate pipeline (kept for a potential
    future serverless entry point; local mode calls handle_code_analysis
    directly via local_entry.py).

    Input:
        type: "code" | "document"

        For code:
            repo_url, user_id, project_name, arcadedb_url, internal_secret
    """
    input_data = event.get("input", {})
    analysis_type = input_data.get("type", "code")

    if analysis_type == "code":
        return handle_code_analysis(input_data)
    elif analysis_type == "document":
        return {"error": "Document analysis not available in this image. Use code-analyzer service instead."}
    else:
        return {"error": f"Unknown type: {analysis_type}"}


# =============================================================================
# CODE ANALYSIS PIPELINE
# =============================================================================

def handle_code_analysis(input_data: Dict) -> Dict[str, Any]:
    """
    Full code analysis pipeline:
    1. Clone repository
    2. Discover files
    3. Parse with tree-sitter (multiprocessing)
    4. Resolve cross-file references
    5. Orchestrate semantic skeletons
    6. Assign directory-based domains
    7. Write graph to ArcadeDB
    """
    job_id = input_data.get("job_id", "unknown")
    repo_url = input_data.get("repo_url", "")
    user_id = input_data.get("user_id", "anonymous")
    project_name = input_data.get("project_name", "unknown")
    arcadedb_url = input_data.get("arcadedb_url", "")
    internal_secret = input_data.get("internal_secret", os.environ.get("INTERNAL_SERVICE_SECRET", ""))

    # Optional flags
    skip_arcadedb = input_data.get("skip_arcadedb", False)
    github_token = input_data.get("github_token")

    # Incremental mode: parse ONLY the changed files (the resolver borrows the
    # rest of its context from the graph), rewrite them per-file, delete the
    # removed ones. changed/removed may be supplied by the caller; when absent
    # they are derived here from git against the SHA the graph was built at
    # (Origin.commit_sha) — so a missed trigger is just a bigger next batch,
    # never silent drift.
    incremental = input_data.get("incremental", False)
    changed_files = set(input_data.get("changed_files", []))
    removed_files = set(input_data.get("removed_files", []))

    # File discovery config - blocklist approach with size filtering
    file_discovery_config = FileDiscoveryConfig.from_input(input_data)

    logger.info(f"Starting code analysis: job={job_id}, repo={repo_url}")

    result = {
        "job_id": job_id,
        "status": "completed",
        "parsed_files": 0,
        "failed_files": 0,
    }

    repo_path = None
    parsed_results = []

    try:
        # Step 1: Clone repository
        if repo_url:
            repo_path = clone_repository(repo_url, github_token=github_token)
            if not repo_path:
                failed = {"job_id": job_id, "status": "failed", "error": f"Failed to clone: {repo_url}"}
                report_completion(arcadedb_url, internal_secret, user_id, project_name, failed)
                return failed
            report_progress(arcadedb_url, internal_secret, user_id, project_name, "cloning")

        # Provenance: which repo and commit this graph describes. Clones are
        # temporary (cleaned up at the end of this job) while the graph lives
        # on — without this record a graph can outlive its source with no way
        # to tell what revision it maps or how to re-fetch it.
        origin = None
        if repo_url and repo_path:
            try:
                sha = subprocess.run(
                    ["git", "-C", repo_path, "rev-parse", "HEAD"],
                    capture_output=True, text=True, timeout=10,
                ).stdout.strip()
            except Exception:
                sha = ""
            origin = {
                "repo": repo_url,
                "commit": sha,
                "parsed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
            }

        # Incremental without explicit lists: derive changed/removed from git,
        # from the graph's own base SHA to HEAD. Any reason this can't work
        # (never parsed, shallow history, unknown SHA) falls back to a full
        # parse — the path that is always correct.
        if incremental and not changed_files and not removed_files:
            derived = None
            if repo_path and not skip_arcadedb and arcadedb_url:
                reader = ArcadeDBWriter(arcadedb_url, internal_secret)
                last_sha = reader.read_origin_sha(project_name)
                if last_sha:
                    derived = derive_git_changes(repo_path, last_sha)
                    if derived is None:
                        logger.info(f"Incremental: cannot diff {last_sha[:12]}..HEAD (shallow clone or unknown SHA)")
                else:
                    logger.info("Incremental: no Origin SHA in graph (never fully parsed?)")
            if derived is None:
                logger.info("Incremental: falling back to full parse")
                incremental = False
            else:
                changed_files, removed_files = set(derived[0]), set(derived[1])
                result["incremental_base"] = last_sha
                logger.info(
                    f"Incremental: {len(changed_files)} changed, {len(removed_files)} removed "
                    f"since {last_sha[:12]}"
                )
                if not changed_files and not removed_files:
                    # Graph already matches HEAD content-wise; just move the
                    # base SHA forward so the next derivation starts here.
                    reader.write(user_id=user_id, project=project_name, files=[], origin=origin)
                    result["no_changes"] = True
                    report_completion(arcadedb_url, internal_secret, user_id, project_name, result)
                    report_progress(arcadedb_url, internal_secret, user_id, project_name, "completed")
                    return result

        # Step 2: Discover files
        skipped_oversize: List[Dict[str, Any]] = []
        files = discover_code_files(repo_path, file_discovery_config, skipped_oversize)
        if skipped_oversize:
            result["skipped_oversize"] = skipped_oversize
        logger.info(f"Discovered {len(files)} code files")
        report_progress(arcadedb_url, internal_secret, user_id, project_name, "discovered", {"files_total": len(files)})

        # Enforce file limit
        MAX_FILES = 10000
        if len(files) > MAX_FILES:
            logger.warning(f"Repository has {len(files)} files, exceeds limit of {MAX_FILES}")
            cleanup_repository(repo_path)
            rejected = {"job_id": job_id, "status": "failed", "error": f"Repository too large: {len(files)} files exceeds limit of {MAX_FILES}", "file_count": len(files), "max_files": MAX_FILES}
            report_completion(arcadedb_url, internal_secret, user_id, project_name, rejected)
            return rejected

        # Step 3: Parse with tree-sitter (multiprocessing)
        # Pre-filter: only send code files to the pool, rest go straight to passthrough
        # These languages have tree-sitter grammars but yield no useful functions/classes
        PASSTHROUGH_LANGUAGES = {"css", "scss", "html", "json", "yaml", "toml", "sql", "graphql", "markdown", "csv", "tsv", "rst", "gitignore", "ini", "requirements", "cmake", "make", "dockerfile", "gomod", "gosum", "starlark", "proto", "prisma", "bash"}
        from modules import TreeSitterParser
        _lang_checker = TreeSitterParser()
        parseable_files = []
        for f in files:
            lang = _lang_checker.get_language(f)
            if not lang:
                continue
            if lang in PASSTHROUGH_LANGUAGES or not _lang_checker.can_parse(f):
                parsed_results.append({"file_path": f, "language": lang, "parse_mode": "passthrough"})
            else:
                parseable_files.append(f)

        # Incremental: tree-sitter only the changed files — this is the whole
        # point of the mode (2 changed files in a 10k-file repo = 2 parses).
        # Passthrough entries above still cover the full tree: they cost no
        # parsing and give the resolver its config/context files.
        if incremental:
            all_discovered = set(files)
            parseable_files = [f for f in parseable_files if f in changed_files]
            logger.info(f"Incremental: parsing {len(parseable_files)} changed files (of {len(all_discovered)} discovered)")

        num_workers = min(cpu_count(), 16)
        logger.info(f"Parsing {len(parseable_files)}/{len(files)} parseable files with {num_workers} workers")

        PER_FILE_TIMEOUT = 60
        parse_start = time.time()
        parse_args = [(f, repo_path) for f in parseable_files]
        parse_results = []
        timed_out_files = []

        with Pool(processes=num_workers) as pool:
            async_results = [pool.apply_async(parse_single_file, (args,)) for args in parse_args]
            progress_interval = max(1, len(files) // 10)

            for i, (args, ar) in enumerate(zip(parse_args, async_results)):
                try:
                    parse_results.append(ar.get(timeout=PER_FILE_TIMEOUT))
                except multiprocessing.TimeoutError:
                    logger.warning(f"TIMEOUT parsing {args[0]} after {PER_FILE_TIMEOUT}s")
                    timed_out_files.append(args[0])
                    parse_results.append({"success": False, "file_path": args[0], "error": "timeout"})
                except Exception as e:
                    parse_results.append({"success": False, "file_path": args[0], "error": str(e)})

                if (i + 1) % progress_interval == 0 or (i + 1) == len(files):
                    logger.info(f"Parse progress: {i+1}/{len(files)} ({time.time() - parse_start:.1f}s, {len(timed_out_files)} timeouts)")

        if timed_out_files:
            logger.warning(f"Total timeouts: {len(timed_out_files)} files")

        for r in parse_results:
            if r.get("success"):
                del r["success"]
                parsed_results.append(r)
            else:
                logger.warning(f"Failed to parse {r.get('file_path')}: {r.get('error')}")
                result["failed_files"] += 1

        result["parsed_files"] = len(parsed_results)
        logger.info(f"Parsed {len(parsed_results)} files")
        report_progress(arcadedb_url, internal_secret, user_id, project_name, "analyzing", {"files_parsed": len(parsed_results)})

        # Step 5: Resolve cross-file references. On an incremental parse only
        # the changed files were parsed, so the resolver gets the rest of the
        # repo as context: the discovered file list (import targets) and the
        # graph's class symbols (inheritance targets) — no full parse needed.
        if parsed_results:
            extra_symbols = None
            all_files_ctx = None
            if incremental:
                all_files_ctx = set(files)
                if not skip_arcadedb and arcadedb_url:
                    try:
                        extra_symbols = ArcadeDBWriter(arcadedb_url, internal_secret).read_class_symbols(
                            project_name, exclude_files=changed_files | removed_files
                        )
                    except Exception as e:
                        logger.warning(f"graph symbol context unavailable: {e}")
            parsed_results = resolve_references(
                parsed_results, repo_root=repo_path,
                all_files=all_files_ctx, extra_symbols=extra_symbols,
            )
            resolved_inheritance = sum(
                1 for r in parsed_results
                for c in r.get("classes", [])
                for p in c.get("resolved_parents", [])
                if p.get("file") is not None
            )
            result["resolved_references"] = {
                "inheritance": resolved_inheritance,
            }
            logger.info(f"Resolved {resolved_inheritance} inheritance")

        # Step 6: Assign domains — plain top-level directory, nothing smarter.
        # The old adaptive nesting (split domains over 50 files ever deeper)
        # produced hundreds of path-shaped "domains" on large repos (367 on a
        # 5.7k-file repo) — noise that every consumer then had to re-derive
        # depth-1 from. Depth-1 is the only level with stable, human-scale
        # cardinality (2-23 measured across parsed projects). Finer grouping
        # is a query-time concern (Directory nodes carry the full tree), not
        # a parse-time one.
        if parsed_results:
            for f in parsed_results:
                parts = f.get("file_path", "").split("/")
                f["domain"] = parts[0] if len(parts) > 1 else "root"

        # Step 8: Write graph to ArcadeDB
        # In incremental mode: filter to only changed files, and delete removed files
        files_to_write = parsed_results
        if incremental and changed_files:
            files_to_write = [f for f in parsed_results if f.get("file_path") in changed_files]
            logger.info(f"Incremental mode: writing {len(files_to_write)}/{len(parsed_results)} changed files")
            result["incremental"] = True
            result["changed_files_count"] = len(files_to_write)

        report_progress(arcadedb_url, internal_secret, user_id, project_name, "indexing", {"files_indexed": 0, "files_total": len(files_to_write)})
        if not skip_arcadedb and arcadedb_url and internal_secret:
            # A full (non-incremental) parse is the complete current state of
            # the repo, so it replaces the project graph: drop first, else the
            # plain INSERTs stack duplicate vertices on every re-parse.
            # Incremental parses touch only changed files and must never wipe.
            clean = (not incremental) or bool(input_data.get("clean", False))
            writer = ArcadeDBWriter(arcadedb_url, internal_secret, clean=clean)

            # Graph diff, step 1: snapshot the OLD graph before write() drops
            # or mutates it — {anchor_key: [signature_hash]} scoped to the
            # files this run touches (whole graph on a full parse, so files
            # deleted from the repo surface as DELETED). Best-effort: no
            # snapshot just means the diff degrades to first_parse.
            touched = sorted(changed_files | removed_files) if incremental else None
            pre_state = {}
            try:
                pre_state = writer.read_element_state(project_name, touched)
            except Exception as e:
                logger.warning(f"diff snapshot failed (reporting first_parse): {e}")

            # Incremental rewrite, step 1: capture the cross-file edges that
            # point INTO the files being rewritten (their unchanged sources
            # survive; the edges die with the deleted vertices), then delete
            # the touched files' subgraphs. write() re-inserts the changed
            # files fresh; restore_edges() re-ties the incoming edges after.
            saved_edges = []
            if incremental and touched:
                saved_edges = writer.read_incoming_edges(project_name, touched)
                logger.info(f"Incremental: rewriting {len(changed_files)} + deleting {len(removed_files)} files "
                            f"({len(saved_edges)} incoming edges captured)")
                writer.delete_files(user_id, project_name, touched)
                if removed_files:
                    result["removed_files_count"] = len(removed_files)

            def on_index_progress(files_indexed, files_total, graph_result=None):
                detail = {"files_indexed": files_indexed, "files_total": files_total}
                if graph_result:
                    detail["functions"] = graph_result.functions + graph_result.methods
                    detail["classes"] = graph_result.classes
                report_progress(arcadedb_url, internal_secret, user_id, project_name, "indexing", detail)

            write_result = writer.write(
                user_id=user_id,
                project=project_name,
                files=files_to_write,
                on_progress=on_index_progress,
                origin=origin,
            )
            result["arcadedb_stats"] = {
                "directories": write_result.directories,
                "files": write_result.files,
                "functions": write_result.functions,
                "methods": write_result.methods,
                "classes": write_result.classes,
                "inherits": write_result.inherits,
                "errors": write_result.errors,
            }

            # These counts are what the writer ATTEMPTED, not what landed: a
            # rejected batch still counts here. So never report success on a
            # write that had failures — a graph that lies about its own
            # completeness is worse than one that fails loudly. Callers read
            # the code map off this graph and cannot tell a missing subsystem
            # from an absent one.
            if not write_result.success:
                result["status"] = "failed"
                result["error"] = (
                    f"{write_result.errors} write batch(es) were rejected — the graph is "
                    f"INCOMPLETE. arcadedb_stats above are attempted counts, not landed "
                    f"ones; verify with SELECT count(*) per type before trusting it."
                )
                logger.error(f"[{project_name}] {result['error']}")

            # Incremental rewrite, step 2: re-tie the captured incoming edges
            # to the freshly written vertices. Sources that were themselves
            # re-parsed are skipped (write() already rebuilt their edges);
            # restores whose target vanished are dropped — the diff below
            # reports those elements as deleted.
            if incremental and saved_edges and write_result.success:
                restored, dropped = writer.restore_edges(
                    project_name, saved_edges, skip_src_files=changed_files
                )
                result["edge_restore"] = {"restored": restored, "dropped": dropped}

            # Graph diff, step 2: compare the pre-write snapshot against what
            # this parse produced. Report-only — the graph itself was already
            # written above; the diff is the answer to "what changed since the
            # last parse", surfaced in the response and appended to DiffLog in
            # the primary DB. Never fails the parse.
            try:
                from modules.graph_diff import compute_diff, parsed_to_elements

                diff = compute_diff(
                    pre_state,
                    parsed_to_elements(files_to_write),
                    first_parse=not pre_state,
                )
                result["diff"] = diff.summary()
                if not diff.first_parse and not diff.is_empty():
                    result["diff"]["entries"] = {
                        "added": diff.added[:200],
                        "changed": diff.changed[:200],
                        "deleted": diff.deleted[:200],
                    }
                if write_result.success:
                    writer.write_diff_log(
                        project_name, diff, trigger=input_data.get("trigger", "manual")
                    )
            except Exception as e:
                logger.warning(f"graph diff step skipped: {e}")

        report_progress(arcadedb_url, internal_secret, user_id, project_name, "completing")

        # Report completion to auth service so Postgres status updates to "ready"
        report_completion(arcadedb_url, internal_secret, user_id, project_name, result)

        # Send terminal event through the same progress channel so SSE clients always get it
        report_progress(arcadedb_url, internal_secret, user_id, project_name, "completed")

    except Exception as e:
        logger.error(f"Code analysis failed: {e}")
        result["status"] = "failed"
        result["error"] = str(e)

        # Report failure to auth service
        report_completion(arcadedb_url, internal_secret, user_id, project_name, result)
        report_progress(arcadedb_url, internal_secret, user_id, project_name, "failed", {"error": str(e)})

    finally:
        if repo_path:
            cleanup_repository(repo_path)

    logger.info(f"Job {job_id} complete: {result}")
    return result


# =============================================================================
# HELPERS
# =============================================================================

def parse_single_file(args: tuple) -> Dict:
    """Parse a single file - worker function for multiprocessing."""
    file_path, repo_path = args
    try:
        from modules import TreeSitterParser
        parser = TreeSitterParser()

        full_path = os.path.join(repo_path, file_path)
        with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read()

        language = parser.get_language(file_path)
        if not language:
            return {"success": False, "file_path": file_path, "error": "No language detected"}

        # Content guards: skip files that could hang tree-sitter. Kept in sync
        # with discovery's max_file_size (2MB) — when this cap is LOWER than
        # discovery's, a file lands in the graph as a File vertex with zero
        # elements, which reads as "empty file", not "unparsed file". That
        # exact silent gap hid gateway/run.py (1,048,932 bytes, 21,573 lines —
        # fan-in 212, the gateway's wiring hub) behind the old 1MB/20k caps.
        # Genuinely pathological files are minified bundles, and those are
        # caught by the line-length heuristic below, not by honest size.
        MAX_CONTENT_BYTES = 2_000_000
        MAX_LINES = 40_000
        line_count = content.count('\n')
        if len(content) > MAX_CONTENT_BYTES or line_count > MAX_LINES:
            logger.warning(
                f"PASSTHROUGH oversize code file: {file_path} ({len(content)} bytes, "
                f"{line_count} lines) — File vertex only, no elements in the graph"
            )
            return {"success": True, "file_path": file_path, "language": language, "parse_mode": "passthrough"}

        # Detect minified: few lines but very long average line
        if line_count > 0 and line_count < 100 and len(content) / line_count > 500:
            return {"success": True, "file_path": file_path, "language": language, "parse_mode": "passthrough"}

        can_parse = parser.can_parse(file_path)

        if can_parse:
            parsed = parser.parse(content, language, file_path)

            return {
                "success": True,
                "file_path": parsed.file_path,
                "language": parsed.language,
                "parse_mode": "full",
                "functions": parsed.functions,
                "methods": parsed.methods,
                "classes": parsed.classes,
                "imports": parsed.imports,
                "interfaces": parsed.interfaces,
                "enums": parsed.enums,
                "exports": parsed.exports,
            }
        else:
            # Passthrough mode - file recorded but not parsed
            return {
                "success": True,
                "file_path": file_path,
                "language": language,
                "parse_mode": "passthrough",
            }
    except Exception as e:
        return {"success": False, "file_path": file_path, "error": str(e)}


def derive_git_changes(repo_path: str, last_sha: str):
    """Changed/removed file lists between the graph's base SHA and HEAD.

    Returns (changed, removed) — repo-relative paths — or None when the diff
    cannot be computed (SHA not in this clone's history: shallow clone, or a
    rebased-away commit), in which case the caller falls back to a full parse.
    --no-renames on purpose: a rename must become delete+add, because the
    graph keys elements by file path.
    """
    def _git(*args):
        return subprocess.run(
            ["git", "-C", repo_path, *args],
            capture_output=True, text=True, timeout=60,
        )

    try:
        if _git("cat-file", "-e", f"{last_sha}^{{commit}}").returncode != 0:
            return None
        changed = _git("diff", "--no-renames", "--name-only", "--diff-filter=ACMR", last_sha, "HEAD")
        removed = _git("diff", "--no-renames", "--name-only", "--diff-filter=D", last_sha, "HEAD")
        if changed.returncode != 0 or removed.returncode != 0:
            return None
        return (
            [l.strip() for l in changed.stdout.splitlines() if l.strip()],
            [l.strip() for l in removed.stdout.splitlines() if l.strip()],
        )
    except Exception as e:
        logger.warning(f"derive_git_changes failed: {e}")
        return None


def clone_repository(repo_url: str, github_token: str = None) -> str:
    """Clone a git repository to a temporary directory.

    If github_token is provided, rewrites the URL to use token auth
    for private repository access via GitHub App installation tokens.

    Local paths are cloned with FULL history (cheap — git hardlinks local
    objects): the incremental path needs the graph's base SHA in history to
    diff against. Remote URLs stay shallow; incremental over a remote falls
    back to a full parse, which is always correct.
    """
    try:
        clone_url = repo_url
        if github_token and "github.com" in repo_url:
            # Rewrite https://github.com/owner/repo → https://x-access-token:{token}@github.com/owner/repo
            clone_url = repo_url.replace("https://github.com/", f"https://x-access-token:{github_token}@github.com/")

        temp_dir = tempfile.mkdtemp(prefix="grasp-parse_")
        depth = [] if os.path.isdir(repo_url) else ["--depth", "1"]
        subprocess.run(
            ["git", "clone", *depth, clone_url, temp_dir],
            capture_output=True, text=True, timeout=300, check=True
        )
        logger.info(f"Cloned to {temp_dir}")
        return temp_dir
    except Exception as e:
        logger.error(f"Clone failed: {e}")
        return None


def cleanup_repository(repo_path: str):
    """Remove cloned repository."""
    try:
        shutil.rmtree(repo_path, ignore_errors=True)
        logger.info(f"Cleaned up {repo_path}")
    except Exception as e:
        logger.warning(f"Cleanup failed: {e}")


def discover_code_files(repo_path: str, config: FileDiscoveryConfig = None,
                        skipped_oversize: List[Dict[str, Any]] = None) -> List[str]:
    """
    Discover all code files in repository using blocklist approach.

    If `skipped_oversize` is passed (a list), code files rejected by the size
    cap are appended to it as {"path", "bytes"} so the caller can report them —
    a graph must never be silently smaller than the repo it claims to map.
    """
    if config is None:
        config = FileDiscoveryConfig()

    parser = TreeSitterParser()

    files = []
    for root, dirs, filenames in os.walk(repo_path):
        # Filter out blocked directories
        dirs[:] = sorted(d for d in dirs if d not in BLOCKED_DIRECTORIES and not d.startswith("."))

        for filename in sorted(filenames):
            full_path = os.path.join(root, filename)
            rel_path = os.path.relpath(full_path, repo_path)

            # Folder filter: only parse files under the specified subfolder
            if config.folder and not rel_path.startswith(config.folder + "/"):
                continue

            # Check user exclude patterns first
            if config.matches_exclude(rel_path):
                continue

            # Check user include patterns
            force_include = config.matches_include(rel_path)

            if not force_include:
                if filename in LOCK_FILENAMES:
                    continue
                if is_generated_file(filename):
                    continue
                if is_hidden_file(filename):
                    continue
                ext = os.path.splitext(filename)[1].lower()
                if ext in BLOCKED_EXTENSIONS:
                    continue

            # Check file size
            try:
                file_size = os.path.getsize(full_path)
                if file_size > config.max_file_size:
                    # Never skip a CODE file silently: an absent File vertex is
                    # indistinguishable from "does not exist", and oversized
                    # files are disproportionately the central ones.
                    if parser.get_language(full_path):
                        logger.warning(
                            f"SKIPPED oversize code file: {rel_path} ({file_size} bytes > "
                            f"{config.max_file_size} cap) — it will be MISSING from the graph"
                        )
                        if skipped_oversize is not None:
                            skipped_oversize.append({"path": rel_path, "bytes": file_size})
                    continue
                if file_size == 0:
                    continue
            except OSError:
                continue

            # Check if we can detect a language
            language = parser.get_language(full_path)
            if language or force_include:
                files.append(rel_path)

    return files


if __name__ == "__main__":
    # Direct-run isn't the local entry — use `python local_entry.py`
    # (started automatically by docker-compose).
    print("run `python local_entry.py` to start the HTTP server.")
