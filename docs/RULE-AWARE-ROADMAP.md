# Rule-aware Grasp — roadmap

*Working document for the `rule-aware` branch. Goal: extend Grasp v1
with a rule-lifecycle layer on top of the existing code-graph, so
architectural rules live as first-class annotations attached to code
nodes and stay in sync when the code changes.*

## The problem this solves

Every codebase has architectural rules — placement conventions,
constraint invariants, "don't import X from Y", "all HTTP handlers
must call `enforce_auth()`", "docs for module Z live in `docs/z.md`".
Today these rules survive in:

- **Human memory** — decays fastest, drifts silently
- **PR-review folklore** — passed by osmosis, opaque to new contributors
- **Markdown docs next to code** — decay when the code changes and the
  doc doesn't get touched (docs-code drift)
- **Linter configs** — great for the structural subset that can be
  expressed as rules-in-code, but almost nothing intent-based
- **CI checks** — catches drift only after commit, and only for what
  someone already automated

None of these track the **code element the rule was written against**.
If a function gets renamed, a class gets deleted, or a method signature
changes, the rules that referenced them silently become stale. Nobody
notices until someone new tries to follow the rule and it doesn't apply
anymore.

## What already exists

Grasp v1 (this repo, MIT, published):

- Tree-sitter parser for 19 languages → File / Class / Function /
  Method / Interface / Enum vertices + IMPORTS / CONTAINS / INHERITS /
  IMPLEMENTS edges in ArcadeDB.
- stdio MCP server exposing `parse_repo`, `query`, `get_schema`,
  `create_vertex`, `create_edge`, `run_pattern`.
- Local-first, no accounts, one Docker + one Bun subprocess.

Related infra in DigiProgress (private, not published):

- Extended graph schema already includes **`Annotation`** and **`Tag`**
  vertices with `ANNOTATES` / `TAGGED_WITH` edges. `Annotation` carries
  `target_type`, `target_path`, `target_name`, `annotation_type`,
  `content`, and an `embedding` field for semantic search.
- Delete-side incremental sync (`/graph/delete-files`): when files
  disappear in a push, the affected Function/Method/Class vertices are
  removed.
- Chunk-embedding + semantic search over Annotations.

The rule-aware feature is a small delta on top of what's already in
DigiProgress. Most of the plumbing is in place; what's missing is the
**lifecycle state** on Annotations and the **diff-driven transitions**
between states.

## The idea in one paragraph

Rules live as `Annotation` vertices attached to specific code vertices
(`File`, `Function`, `Method`, `Class`, `Interface`, ...) via
`ANNOTATES` edges. Each `Annotation` has a `lifecycle_state`. When the
parser runs, we compute a **graph diff** against the previous state.
Diff events (ADDED, CHANGED, DELETED) walk the affected annotations
and update their `lifecycle_state`. An orphan detector queries by
state; the agent surfaces `review-needed` and `orphaned` rules at the
moment they matter.

## Layer breakdown

### Layer 0 — schema deltas

Additions to the graph schema (idempotent, IF NOT EXISTS):

1. `Annotation` vertex — existing shape from DigiProgress plus:
   - `lifecycle_state: STRING` — one of `active`, `review-needed`,
     `orphaned`, `resolved`
   - `state_updated_at: DATETIME`
   - `state_reason: STRING` — free-text why the last transition fired
     (e.g. "target Function `foo` deleted", "target Method `bar.baz`
     signature changed from `(a: int)` to `(a: int, b: str)`")
2. `ANNOTATES` edge — carries the state — no change to shape.
3. Optional: `Tag` vertex + `TAGGED_WITH` edge — for grouping rules
   ("placement", "security", "perf") — nice-to-have, not blocking.

### Layer 1 — signature capture at parse time

Extend the parser output so each `Function`/`Method` vertex carries a
stable **signature hash** derived from `(name, params, return_type,
containing class/module path)`. Used by Layer 2 to detect CHANGED vs
just re-parsed. Cheap: SHA1 of a normalized string.

### Layer 2 — graph diff engine

Given the parser output for a repo (or an incremental subset for a
subset of files), compute:

- **ADDED**: vertices in the new parse that had no matching vertex in
  the graph
- **DELETED**: vertices in the graph that have no matching vertex in
  the new parse (already partially handled by delete-files endpoint)
- **CHANGED**: vertices whose `signature_hash` changed

Matching key: `(vertex_type, file_path, name)` for Function/Method/Class;
`file_path` for File; canonicalized name for Class inheritance.

Emit a stream of diff events: `{type: "CHANGED", vertex: <ref>, before:
{...}, after: {...}}`.

### Layer 3 — lifecycle engine

For each diff event, walk incoming `ANNOTATES` edges and update the
`lifecycle_state` of connected `Annotation` vertices:

| Diff event | Rule transition |
|---|---|
| Target vertex DELETED | `active` / `review-needed` → `orphaned` |
| Target vertex CHANGED (signature) | `active` → `review-needed` |
| Target vertex CHANGED (file moved) | `active` → `review-needed` |
| Target vertex re-added after delete | `orphaned` → `review-needed` |

Rules stuck in `review-needed` or `orphaned` are the ones the agent
should surface next time it works in that area.

### Layer 4 — trigger surface

Two modes, both local-first:

- **Manual**: `grasp update <repo-path>` runs the parser incrementally,
  Layers 1-3 fire.
- **Post-commit hook**: `grasp init-hooks <repo-path>` installs a
  `.git/hooks/post-commit` that calls `grasp update`. Works for any
  repo the user commits to; no push required (deliberately —
  local-first).

Watch mode (filesystem watcher, debounced re-parse) is a Layer 5
extension for later, not MVP.

### Layer 5 — agent-facing surface

MCP tools (in addition to what's already there):

- `annotate(target, content, annotation_type)` — create a rule
  attached to a code vertex (auto-resolves the target from a
  file+name pair)
- `rules_by_state(state)` — list `Annotation`s in a given state
  (`review-needed`, `orphaned`)
- `rules_for(target)` — list rules attached to a specific vertex or
  its ancestors (a rule on a File applies to its child Functions;
  Interface rules apply to implementing Classes)
- `resolve_rule(id, note)` — mark a rule `active` (post-review) or
  `resolved` (dropped intentionally); stores the note as an audit trail

CLI wrappers over the same for direct use.

## Effort estimate (solo, focused)

| Layer | Effort | Notes |
|---|---|---|
| 0 — schema deltas | 0.5 day | Additive to existing schema |
| 1 — signature capture | 1 day | Extend TreeSitterParser output |
| 2 — graph diff engine | 2 days | Core work; needs test cases |
| 3 — lifecycle engine | 2-3 days | Includes edge-case transitions |
| 4 — trigger surface (manual + hook) | 1-1.5 day | CLI + `.git/hooks/` installer |
| 5 — MCP tools + CLI | 1.5-2 day | Wrappers over the engine |
| Multi-tenancy strip (from DigiProgress → local) | 3-4 days | Single-instance mode |

**Total: 11-14 person-days of focused work.**
**Calendar estimate: 2-3 focused weeks solo.**

Multi-tenancy strip may not be needed if the Grasp v1 parser already
runs single-instance and DigiProgress code isn't imported wholesale.
Design decision at start: do we lift DigiProgress modules directly
(fast, brings baggage) or re-implement the delete+diff logic against
Grasp v1's simpler codebase (slower, cleaner)? Recommend the second
for a public MIT product.

## Positioning

**Tagline candidate:** *"the code graph that knows its own rules"*.

**Narrative:** Grasp v1 gave your agent structural understanding of a
codebase. Rule-aware Grasp adds an intent layer — the architectural
decisions that live above the code, tracked as first-class annotations
that self-invalidate when the code they describe changes.

**Positioning against nearby tools:**

- **CodeRabbit / Sweep AI** — review PRs with an LLM; no persistent
  rule-lifecycle, no graph traversal, ~$10-15/user/month. Rule-aware
  Grasp is local, free, and remembers.
- **Cursor `.cursorrules` / Aider config** — always-in-context rules
  file. No lifecycle. No connection to actual code state. Rule-aware
  Grasp knows *which* rules a change might invalidate.
- **Structurizr / Backstage TechDocs** — enterprise architecture docs
  as code. Human-maintained sync between docs and code. Rule-aware
  Grasp does the sync automatically.

**Why now:** MCP as an ecosystem is a year old; rule-lifecycle over an
MCP-served code-graph is a niche nobody has claimed. Timing is right
for the "HN launch + Twitter narrative" arc.

## Non-goals for MVP

- Cross-repo rule inheritance (a rule in one repo that applies to
  another). Nice concept, adds complexity, wait for user demand.
- Auto-authoring rules from PR review comments. Interesting research,
  not shippable.
- Cloud sync between machines. Local-first is a feature, not a bug.
- UI beyond CLI + MCP tools. If someone wants a web UI they can build
  one on top of the MCP surface.

## Open questions

1. **Naming of the diff feature.** "Signature diff" is too narrow; the
   engine catches all vertex transitions, not just Function signatures.
   Working name: **graph diff**. Final naming decision as part of
   Layer 2 landing.

2. **How aggressive on `review-needed`.** A rename that keeps the same
   body shouldn't flip to `review-needed` unless the rule references
   the name specifically. Detect this via body-hash vs signature-hash?
   Punt to a first-pass "conservative" default (flip on any change),
   iterate from real usage.

3. **Bootstrap flow for existing repos.** For a repo already parsed
   into the graph without lifecycle state, how do we default? Simplest
   answer: on first `grasp update` after upgrade, run a one-shot
   backfill that sets every existing `Annotation` to `active`.
   Handled in Layer 4.

4. **Whether to lift DigiProgress code directly** (see effort table).
   The right call depends on how coupled DigiProgress modules are to
   its multi-tenant runtime.

## Next steps (in this branch)

1. Land Layer 0 + Layer 1 as one small PR — schema deltas + signature
   capture, no behavior change beyond richer parser output.
2. Land Layer 2 as one focused PR — graph diff engine with a fixture
   test suite.
3. Land Layer 3 as one PR — lifecycle engine wired to the diff stream.
4. Land Layer 4 + Layer 5 together — CLI + MCP + git-hook installer.
5. Prep the release: update README with a new Quickstart, write a HN
   launch draft, cut `v0.2.0-rule-aware.1`.

## Ownership

- Design + roadmap: this document, iterated as Layers land.
- Implementation: Szabi (owner). Grasp-Dev (this agent) available for
  code-review and sub-task delegation.
- Release: Szabi decides go/no-go per layer.
