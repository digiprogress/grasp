"""
Grasp Parser — one-shot local runner.

Invokes the analysis pipeline on a GitHub URL and writes results to ArcadeDB.
Runs INSIDE the parser container (uses handler.py + modules/).

Usage (from host):
    docker exec -it grasp-parser python -u run_local.py --repo https://github.com/USER/REPO
"""
import argparse
import json
import os
import sys

# Import the pipeline entry from handler.py.
sys.path.insert(0, "/app")
from handler import handle_code_analysis


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="GitHub repository URL")
    ap.add_argument("--project", default="local-test", help="Project name")
    ap.add_argument("--user", default="local", help="User id")
    args = ap.parse_args()

    arcadedb_url = os.environ.get("ARCADEDB_URL", "http://arcadedb:2480")

    input_data = {
        "job_id": "local-test-1",
        "repo_url": args.repo,
        "user_id": args.user,
        "project_name": args.project,
        "arcadedb_url": arcadedb_url,
        "internal_secret": os.environ.get("INTERNAL_SERVICE_SECRET", ""),
        # Local mode: no cloud object storage
        # File discovery uses defaults
    }

    print(f"▸ Parsing {args.repo} into ArcadeDB @ {arcadedb_url}")
    print(f"  project={args.project}  user={args.user}")
    print("")

    result = handle_code_analysis(input_data)
    print("")
    print("── Result ──")
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
