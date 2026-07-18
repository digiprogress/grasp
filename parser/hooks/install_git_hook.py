#!/usr/bin/env python3
"""
Install a Grasp post-commit hook into a local git repo.

The hook is a dumb poke: after each commit it POSTs one incremental parse
request and gets out of the way. It does NOT compute what changed — the
parser derives that itself, from the graph's recorded base SHA (Origin) to
HEAD. That split is deliberate: a hook that computes diffs can silently
drift when a commit slips past it (rebase, amend, hook disabled for a
while); a parser that diffs from the graph's own base just catches up with
a bigger batch on the next poke.

Local-only: no push required, nothing leaves the box. A parser that's down
means the graph updates on the next poke or manual parse — never a blocked
commit.

Usage:
    python install_git_hook.py --repo /path/to/repo [--project NAME]
                               [--parser-url http://127.0.0.1:3334]
    python install_git_hook.py --repo /path/to/repo --uninstall
"""
from __future__ import annotations

import argparse
import os
import stat
import sys

MARK_BEGIN = "# >>> grasp post-commit >>>"
MARK_END = "# <<< grasp post-commit <<<"


def hook_body(project: str, parser_url: str) -> str:
    # POSIX sh, fire-and-forget: background curl, 60s cap, output discarded.
    return f"""{MARK_BEGIN}
# Managed by grasp install_git_hook.py — edit via that tool, not by hand.
curl -s -m 60 -X POST "{parser_url}/parse" -H 'Content-Type: application/json' \\
  -d "{{\\"repo\\": \\"$(git rev-parse --show-toplevel)\\", \\"project\\": \\"{project}\\", \\"incremental\\": true, \\"trigger\\": \\"post-commit\\"}}" \\
  >/dev/null 2>&1 &
{MARK_END}
"""


def _strip_existing(text: str) -> str:
    if MARK_BEGIN not in text:
        return text.rstrip("\n")
    before = text.split(MARK_BEGIN, 1)[0].rstrip("\n")
    after = text.split(MARK_END, 1)[1] if MARK_END in text else ""
    return (before + "\n" + after.strip("\n")).strip("\n")


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--repo", required=True, help="path to the git repo")
    ap.add_argument("--project", default=None, help="project/db name (default: repo dir name)")
    ap.add_argument("--parser-url", default="http://127.0.0.1:3334")
    ap.add_argument("--uninstall", action="store_true")
    args = ap.parse_args()

    repo = os.path.abspath(args.repo)
    hooks_dir = os.path.join(repo, ".git", "hooks")
    if not os.path.isdir(hooks_dir):
        print(f"error: {repo} is not a git repo (no .git/hooks)", file=sys.stderr)
        return 2
    project = args.project or os.path.basename(repo.rstrip("/"))
    hook_path = os.path.join(hooks_dir, "post-commit")

    existing = ""
    if os.path.exists(hook_path):
        with open(hook_path, "r", encoding="utf-8") as fh:
            existing = fh.read()

    if args.uninstall:
        if MARK_BEGIN not in existing:
            print("no grasp hook present — nothing to remove")
            return 0
        stripped = _strip_existing(existing)
        body = stripped if stripped.strip() and stripped.strip() != "#!/bin/sh" else ""
        if body:
            with open(hook_path, "w", encoding="utf-8") as fh:
                fh.write(body + "\n")
        else:
            os.remove(hook_path)
        print(f"removed grasp hook from {hook_path}")
        return 0

    stripped = _strip_existing(existing)
    if not stripped.startswith("#!"):
        stripped = "#!/bin/sh\n" + stripped
    content = stripped.rstrip("\n") + "\n\n" + hook_body(project, args.parser_url)
    with open(hook_path, "w", encoding="utf-8") as fh:
        fh.write(content)
    st = os.stat(hook_path)
    os.chmod(hook_path, st.st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    print(f"installed grasp post-commit hook → {hook_path}")
    print(f"  project={project}  parser={args.parser_url}")
    print("  every commit pokes an incremental re-parse; the parser works out what changed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
