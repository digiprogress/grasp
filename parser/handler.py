"""
Grasp Parser Handler — modular tree-sitter analysis pipeline.

Orchestrates the full analysis pipeline:
1. Code Analysis: clone → discover → parse in parallel → resolve →
   orchestrate → community → write graph
2. Incremental: clone → discover → parse all → write only changed files →
   delete removed files

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
import shutil
import time
import multiprocessing
from typing import Dict, Any, List
from multiprocessing import Pool, cpu_count
import httpx

from modules import (
    TreeSitterParser, ArcadeDBWriter,
    FileDiscoveryConfig, resolve_references,
    orchestrate_file,
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

    # Incremental mode: parse all (resolver needs full symbol table), write only changed
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

        # Step 2: Discover files
        files = discover_code_files(repo_path, file_discovery_config)
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

        # Step 5: Resolve cross-file references
        if parsed_results:
            parsed_results = resolve_references(parsed_results, repo_root=repo_path)
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

        # Step 6: Orchestrate semantic skeletons
        if parsed_results:
            for file_result in parsed_results:
                if file_result.get("parse_mode") == "passthrough":
                    file_result["skeleton"] = {
                        "identity": {
                            "file": file_result.get("file_path"),
                            "language": file_result.get("language"),
                            "parse_mode": "passthrough",
                        }
                    }
                else:
                    file_result["skeleton"] = orchestrate_file(file_result)
            logger.info(f"Orchestrated {len(parsed_results)} semantic skeletons")

        # Step 7: Assign directory-based domains (adaptive nesting)
        if parsed_results:
            MAX_DOMAIN_FILES = 50

            def assign_domains(files, depth=1):
                # Group by domain at current depth
                domains = {}
                for f in files:
                    parts = f.get("file_path", "").split("/")
                    domain = "/".join(parts[:depth]) if len(parts) > depth else ("root" if depth == 1 else "/".join(parts[:-1]) or "root")
                    f["domain"] = domain
                    domains.setdefault(domain, []).append(f)

                # Split oversized domains by going one level deeper
                for domain_name, domain_files in domains.items():
                    if len(domain_files) > MAX_DOMAIN_FILES and domain_name != "root":
                        # Only recurse if deeper splitting can produce new groups
                        max_parts = max(len(f.get("file_path", "").split("/")) for f in domain_files)
                        if depth < max_parts:
                            assign_domains(domain_files, depth + 1)

            assign_domains(parsed_results)

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
            writer = ArcadeDBWriter(arcadedb_url, internal_secret)

            # Delete removed files first (incremental only)
            if incremental and removed_files:
                logger.info(f"Incremental: deleting {len(removed_files)} removed files")
                writer.delete_files(user_id, project_name, list(removed_files))
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

        # Content guards: skip files that could hang tree-sitter
        MAX_CONTENT_BYTES = 1_000_000  # 1MB
        MAX_LINES = 20_000
        line_count = content.count('\n')
        if len(content) > MAX_CONTENT_BYTES or line_count > MAX_LINES:
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


def clone_repository(repo_url: str, github_token: str = None) -> str:
    """Clone a git repository to a temporary directory.

    If github_token is provided, rewrites the URL to use token auth
    for private repository access via GitHub App installation tokens.
    """
    try:
        clone_url = repo_url
        if github_token and "github.com" in repo_url:
            # Rewrite https://github.com/owner/repo → https://x-access-token:{token}@github.com/owner/repo
            clone_url = repo_url.replace("https://github.com/", f"https://x-access-token:{github_token}@github.com/")

        temp_dir = tempfile.mkdtemp(prefix="grasp-parse_")
        subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, temp_dir],
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


def discover_code_files(repo_path: str, config: FileDiscoveryConfig = None) -> List[str]:
    """
    Discover all code files in repository using blocklist approach.
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
