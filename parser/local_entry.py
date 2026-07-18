"""
Grasp Parser — local HTTP mode entry point.

Exposes POST /parse to trigger the code-analysis pipeline on a GitHub URL.
Called by the Grasp MCP server (or any local caller). Single-user, no auth,
no queue — one job at a time is fine for the MVP.

Endpoints
─────────
GET  /health                          → {"status": "ok"}
POST /parse  {repo, project, [clean]} → the analysis result (see handler.py)
"""
import json
import logging
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("parser-http")

sys.path.insert(0, "/app")
# Import lazily so import errors surface on first request (not startup)
_handle = None
_import_lock = threading.Lock()


def _load_handler():
    global _handle
    with _import_lock:
        if _handle is None:
            from handler import handle_code_analysis  # type: ignore
            _handle = handle_code_analysis
    return _handle


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        logger.info("%s - %s" % (self.address_string(), fmt % args))

    def _respond(self, code, body):
        payload = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):  # noqa: N802
        if self.path == "/health":
            self._respond(200, {"status": "ok", "service": "grasp-parser"})
            return
        self._respond(404, {"error": "not found"})

    def do_POST(self):  # noqa: N802
        if self.path != "/parse":
            self._respond(404, {"error": "not found"})
            return
        length = int(self.headers.get("Content-Length", "0"))
        try:
            raw = self.rfile.read(length) if length else b"{}"
            payload = json.loads(raw or b"{}")
        except Exception as e:
            self._respond(400, {"error": f"invalid json: {e}"})
            return

        repo = payload.get("repo") or payload.get("repo_url")
        project = payload.get("project") or payload.get("project_name")
        if not repo:
            self._respond(400, {"error": "missing 'repo' (github URL)"})
            return
        if not project:
            # Default project name = repo tail
            project = repo.rstrip("/").split("/")[-1].replace(".git", "")

        input_data = {
            "job_id": f"http-{project}",
            "repo_url": repo,
            "user_id": payload.get("user_id", "local"),
            "project_name": project,
            "arcadedb_url": os.environ.get("ARCADEDB_URL", "http://arcadedb:2480"),
            "internal_secret": os.environ.get("INTERNAL_SERVICE_SECRET", "local"),
        }
        if payload.get("clean"):
            input_data["clean"] = True
        # Incremental re-parse (the post-commit hook's mode): the parser
        # derives changed/removed itself, from the graph's Origin SHA to
        # HEAD — callers may pass explicit lists but don't need to.
        if payload.get("incremental"):
            input_data["incremental"] = True
            if payload.get("changed_files"):
                input_data["changed_files"] = payload["changed_files"]
            if payload.get("removed_files"):
                input_data["removed_files"] = payload["removed_files"]
        if payload.get("trigger"):
            input_data["trigger"] = payload["trigger"]

        logger.info(f"received parse request: repo={repo} project={project}")
        try:
            handle = _load_handler()
            result = handle(input_data)
            self._respond(200, result)
        except Exception as e:
            logger.exception("parse failed")
            self._respond(500, {"error": str(e)})


def main():
    port = int(os.environ.get("PARSER_PORT", "3334"))
    host = "0.0.0.0"
    server = ThreadingHTTPServer((host, port), Handler)
    logger.info(f"Grasp parser HTTP listening on :{port}")
    server.serve_forever()


if __name__ == "__main__":
    main()
