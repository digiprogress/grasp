# Rule-aware Grasp — concrete implementation plan

## Implementation status (all 6 layers landed on `rule-aware`)

Built and tested against a live ArcadeDB + parser stack. Commits, in order:

- **L0+1** `feat: upsert code vertices by stable anchor so rule annotations
  survive re-parse` — `arcadedb_direct_writer.py` rewritten: `anchor_key`
  (`kind:file:name`, no line) + `signature_hash` on every element, INSERT→
  `UPDATE … UPSERT`, per-file stale-cleanup, `Annotation`/`ANNOTATES` schema.
  handler stops auto-dropping the DB.
- **L2** `feat: diff re-parses by signature …` — `graph_diff.py` (pure) +
  `read_element_state()` snapshot.
- **L3** `feat: … move rules pinned to changed or deleted code` —
  `rule_lifecycle.py`, wired into `handler.py` after write.
- **L4** `feat: post-commit hook re-parses incrementally …` —
  `hooks/install_git_hook.py`, incremental params in `local_entry.py`,
  annotation-state backfill.
- **L5** `feat: MCP tools to attach rules …` — `annotate`, `rules_by_state`,
  `rules_for`, `resolve_rule` in `mcp/server.ts`.

**Verified behaviours (live):** re-parse no longer duplicates vertices; a
rule's `ANNOTATES` edge survives re-parse (the DigiProgress bug, avoided); a
target signature change flips its rule to `review_needed` with the hash delta
as reason; a deleted target flips its rule to `orphaned`; an unchanged
target's rule stays `active`; the diff engine's unit test passes; the four
MCP tools' SQL round-trips (annotate → list → resolve) work.

**Two ArcadeDB gotchas found and worked around** (worth knowing for future
graph work): (1) this build has no `DELETE EDGE` statement — edges are removed
via `DELETE FROM (SELECT expand(outE('T')) FROM v …)`; (2) a row UPSERTed
earlier in a `sqlscript` is invisible to a later `NOT IN` in the *same*
transaction, so writes are phased (upserts → stale-cleanup → edges) as
separate transactions.

**Not yet done:** containers still run the pre-feature baked image — a
`docker compose build parser mcp-server && docker compose up -d` is needed to
serve this live. Deliberately deferred (see the "live version" note).

---



*The build spec. Grounded in the actual code as it exists today on this
branch, not the idealized roadmap. Read `RULE-AWARE-ROADMAP.md` for the
"why"; this file is the "exactly what, in what order, touching which
files".*

*Guiding constraint (Szabi, explicit): must be maintainable by one
developer alongside a full-time job. Every decision below optimizes for
"simplest thing that is correct", not "most general".*

---

## 0. What is actually true today (verified, not assumed)

Two agents mapped both codebases file-by-file. The load-bearing facts:

### Grasp v1 (`/root/grasp`) — the target

- **Writer in use is `arcadedb_direct_writer.py`** (aliased
  `ArcadeDBWriter` in `modules/__init__.py`). The DigiProgress-style
  `arcadedb_writer.py` is dead code (not imported).
- Writes are **plain `INSERT INTO`, not UPSERT.** Only `File(path)` and
  `Directory(path)` have unique indexes. **Function/Method/Class/
  Interface/Enum have no uniqueness → re-parse duplicates them.**
- **Positional match key today is `(file_path, name, line)`.** `line`
  shifts on any edit above the element → useless as a diff identity.
- **`delete_files()` exists** (deletes children by `file_path` + the
  File vertex) but **no local entry point ever calls it.** Same for
  `incremental` / `changed_files` / `removed_files` — inert scaffolding.
- **`clean` flag exists on the writer but is not wired** through
  `local_entry.py` / `run_local.py` / MCP `parse_repo`. Net: today the
  only way to re-parse cleanly is a manual `DROP DATABASE`.
- **Code graph lives in a per-project DB** (`_sanitize_db_name(project)`).
  **Annotation/Chunk/HAS_CHUNK live in the separate primary `grasp` DB**
  (from `bootstrap.sh`). There is **no `ANNOTATES` edge and no
  Annotation type in the per-project code DBs.**
- No `params`, `signature`, or any hash is emitted by the parser
  (`query_extractor.py`).
- MCP `parse_repo` calls the parser over HTTP (`POST :3334/parse`), does
  not spawn docker.

### DigiProgress — the reference (do NOT depend on it at runtime)

- Uses real `UPDATE ... UPSERT WHERE <match keys>` with **name-based**
  keys (`file_path AND name`, no `line`) — better than v1's positional
  key, and the model to copy.
- `Annotation` schema already has the shape we want *except*
  lifecycle: `id, target_type, target_path, target_name,
  annotation_type, content, created_at, updated_at, embedding`. Indexed
  `Annotation(id)` unique + `Annotation(target_type,target_path)`
  composite.
- **Annotations anchor by string `target_path`/`target_name`, and
  re-parse deletes+recreates target vertices → `ANNOTATES` edges are
  severed with no repair.** This is the exact bug our lifecycle feature
  must not reproduce.
- `delete-files` **leaks Chunks and Annotations** (never deletes them).
  Our GC must handle both.
- Diff granularity is **whole-file, from the GitHub push payload.** No
  content/signature hashing anywhere. No per-symbol diff. No graph-diff
  primitive.
- Deferred-edge pattern (Redis list + `finalizeEdges` replay) handles
  cross-file edges — a good idea, but Redis is a dependency we do NOT
  want in local-first Grasp. We replace it with an in-process deferred
  list (see §3).

**Conclusion:** we are not "porting DigiProgress." DigiProgress proves
the schema shape works and shows the exact traps (severed anchors,
leaked GC). We build the diff+lifecycle net-new on Grasp v1's simpler,
Redis-free, one-DB-per-project base.

---

## 1. The single most important design decision: stable identity

Everything downstream (diff, lifecycle, annotation anchoring) depends on
one thing: **a stable identity for a code element that survives edits.**

Today's `(file_path, name, line)` fails because `line` moves. DigiProgress's
`(file_path, name)` is better but breaks on two real cases: overloaded/
duplicate names in one file, and renames.

**Decision — a two-part identity:**

1. **Anchor key** = `(file_path, kind, qualified_name)` where
   `qualified_name` is `name` for top-level, `class_name.name` for
   methods. This is the UPSERT match key and the annotation anchor.
   Deterministic, no line number.
2. **Signature hash** = `sha1(normalized_signature)` stored as a
   property. `normalized_signature` = `kind|qualified_name|params|
   return_type` (params normalized: names dropped, types kept where the
   language gives them; when a language has no types, params reduce to
   arity). This is **not** identity — it's the change-detector. Same
   anchor + different sig-hash = CHANGED.

Renames are treated as delete+add for MVP (the old anchor disappears,
a new one appears). Rename-detection (fuzzy body match) is a
post-MVP nicety, explicitly out of scope. Documented so we don't
pretend otherwise.

**Duplicate names in one file** (two `foo` at different scopes): append
a disambiguator `#N` by source order to `qualified_name`. Rare;
handled deterministically so the diff doesn't thrash.

---

## 2. Layer-by-layer build, in merge order

Each layer is one PR, independently reviewable, leaves the tree working.

### Layer 0 — schema deltas (per-project code DB)  ·  ~0.5 day

File: `parser/modules/arcadedb_direct_writer.py` (`_ensure_schema`).

Add to the **per-project** schema (this is where code vertices live, so
this is where rules must attach):

- New vertex `Annotation` with: `id STRING`, `anchor_key STRING`,
  `target_kind STRING`, `target_file STRING`, `target_qualified_name
  STRING`, `annotation_type STRING`, `content STRING`,
  `lifecycle_state STRING`, `state_reason STRING`, `created_at
  DATETIME`, `updated_at DATETIME`, `state_updated_at DATETIME`.
- New edge `ANNOTATES` (Annotation → code vertex).
- Indexes: `Annotation(id) UNIQUE`, `Annotation(anchor_key) NOTUNIQUE`,
  `Annotation(lifecycle_state) NOTUNIQUE`.
- Add `signature_hash STRING` property to `Function` and `Method`
  (and `Class`/`Interface`/`Enum` — hash covers members for enums,
  extends/implements for classes).

`lifecycle_state` ∈ `active | review_needed | orphaned | resolved`.

No behavior change yet — just schema. Ship it alone so the risky bits
land against a stable base.

### Layer 1 — signature capture + real UPSERT + wire `clean`  ·  ~2 days

Three tightly-coupled changes; they must land together or re-parse stays
broken.

1. **Signature capture** — `parser/modules/parser/query_extractor.py`:
   emit `params` (list of `{name, type?}`) and `signature_hash` for
   functions/methods; `member_hash` folded into `signature_hash` for
   classes/interfaces/enums. Pure addition to the emitted dicts.

2. **Switch writer from INSERT to UPSERT** —
   `arcadedb_direct_writer.py`. Replace `INSERT INTO X SET ...` with
   `UPDATE X SET ... UPSERT WHERE <anchor>` for every vertex type, using
   the §1 anchor key (`file_path` + `kind` + `qualified_name`, no
   `line`). Add unique indexes on the anchor for Function/Method/Class/
   Interface/Enum so upsert is enforced, not best-effort. This alone
   fixes the "re-parse duplicates everything" bug.

3. **Wire `clean` and `incremental` end to end** — `local_entry.py`,
   `run_local.py`, `handler.py`, MCP `parse_repo`. Read
   `input_data["clean"]`, pass to the writer. Add `--clean` to
   `run_local.py`. This makes re-parse actually usable.

At the end of Layer 1, re-parsing a repo is idempotent (no dupes) and
every code element carries a signature hash. This is independently
valuable even before diff/lifecycle exist — worth shipping as its own
release (`v0.1.1`, "idempotent re-parse").

### Layer 2 — graph diff engine  ·  ~2 days

New module: `parser/modules/graph_diff.py`.

Function: `compute_diff(db, parsed_results) -> DiffReport`.

- Load current graph state for the files being written: a lightweight
  query returning `{anchor_key: signature_hash}` for all
  Function/Method/Class/Interface/Enum in the affected files.
- Compare against the freshly parsed set:
  - anchor in new, not in old → **ADDED**
  - anchor in old, not in new → **DELETED**
  - anchor in both, hash differs → **CHANGED**
  - anchor in both, hash same → unchanged (skip)
- `DiffReport = {added: [...], deleted: [...], changed: [...]}` where
  each entry is `{anchor_key, kind, file, qualified_name, before?,
  after?}`.

Diff is computed **per write batch, scoped to the files in that batch** —
never a whole-graph scan. Fixture test suite with hand-built
before/after parse outputs is the deliverable's spine (this is the layer
most likely to have subtle bugs).

No lifecycle wiring yet — Layer 2 just *reports* the diff, exposed via a
new MCP tool `diff_last_parse` for inspection and a `--show-diff` flag on
`run_local.py`. Ship and eyeball on real repos before wiring
consequences.

### Layer 3 — lifecycle engine  ·  ~2 days

New module: `parser/modules/rule_lifecycle.py`.

Function: `apply_lifecycle(db, diff_report)`.

For each diff event, find `Annotation` vertices whose `anchor_key`
matches the affected element (via the `anchor_key` index) and transition:

| Diff event | Transition |
|---|---|
| target DELETED | `active`/`review_needed` → `orphaned` (+reason "target deleted") |
| target CHANGED | `active` → `review_needed` (+reason "signature changed: `<before>` → `<after>`") |
| target ADDED whose anchor matches a prior `orphaned` rule | `orphaned` → `review_needed` (+reason "target reappeared") |

**GC that DigiProgress got wrong:** when a File is deleted, also delete
its Chunks (`WHERE file_path=`) and set its Annotations to `orphaned`
(don't hard-delete — orphaned rules are the whole point: the user needs
to see them). Annotation hard-delete is always explicit via the MCP
tool, never a side effect of re-parse.

`apply_lifecycle` runs inside `handler.py` right after `writer.write()`,
guarded so a lifecycle error never fails the parse (log + continue).

### Layer 4 — trigger surface  ·  ~1.5 day

- **Manual** (works already once Layers 1-3 land): `parse_repo` /
  `run_local.py` compute diff + apply lifecycle on every run. Nothing
  extra needed — this falls out of Layer 3.
- **Post-commit hook**: new `grasp` CLI shim or a small
  `parser/hooks/install.py` that writes `.git/hooks/post-commit` in a
  target repo, calling the parser's `/parse` endpoint with the repo path
  and `incremental` derived from `git diff --name-only HEAD~1 HEAD`.
  Local-only; no push, no remote. Idempotent installer (checks for an
  existing Grasp block before appending).
- **Backfill**: first `parse_repo` after upgrade sets every existing
  `Annotation` with no `lifecycle_state` to `active`. One statement in
  `_ensure_schema` migration path.

Watch mode (filesystem watcher) is explicitly deferred.

### Layer 5 — agent-facing surface  ·  ~1.5-2 days

New MCP tools in `mcp/server.ts` (all operate on the per-project code DB
via the existing `database` param pattern):

- `annotate({project, target: {kind, file, qualified_name}, content,
  annotation_type})` — resolves the anchor, `INSERT INTO Annotation` +
  `CREATE EDGE ANNOTATES`. Sets `lifecycle_state='active'`.
- `rules_by_state({project, state})` — list annotations in a state.
  The daily-driver query: "what's `review_needed` or `orphaned`?"
- `rules_for({project, target})` — rules attached to a vertex,
  including inherited (File rules cover child Functions; Interface rules
  cover implementing Classes — one-hop traversal).
- `resolve_rule({project, id, note})` — set `active` (reviewed, still
  valid) or `resolved` (intentionally dropped); appends `note` to
  `state_reason` as an audit trail.

CLI wrappers optional; MCP tools are the primary surface (the agent is
the main consumer).

---

## 3. Deliberate simplifications (what we are NOT building)

- **No Redis.** DigiProgress defers cross-file edges to a Redis list.
  We keep an **in-process Python list** of deferred edge statements and
  flush at the end of `write()` — same effect, zero new dependency.
  Grasp is single-process local; this is strictly simpler and correct.
- **No multi-tenancy.** One DB per project, no user-scoping, no auth
  layer. Already how v1 works; we do not import DigiProgress's tenant
  machinery.
- **No rename detection.** Delete+add. Documented.
- **No cross-repo rule inheritance.** Out of scope.
- **No cloud sync.** Local-first is the product.
- **No web UI.** MCP + CLI only.
- **No content-body diffing.** Signature-hash granularity only. A body
  change that doesn't touch the signature does NOT flip a rule to
  `review_needed` (conservative default is the opposite; we choose the
  *less* noisy default deliberately — a rule usually cares about the
  interface, not the body. Revisit from real usage.)

Each omission is a maintenance-cost decision, not an oversight.

---

## 4. Effort + sequencing

| Layer | Deliverable | Effort | Ships as |
|---|---|---|---|
| 0 | schema deltas | 0.5 d | (folded into L1 release) |
| 1 | sig capture + UPSERT + clean wiring | 2 d | `v0.1.1` idempotent re-parse |
| 2 | graph diff engine + `diff_last_parse` | 2 d | `v0.1.2` diff inspection |
| 3 | lifecycle engine + GC fix | 2 d | — |
| 4 | post-commit hook + backfill | 1.5 d | — |
| 5 | MCP rule tools | 2 d | `v0.2.0` rule-aware (the launch) |

**Total: ~10 person-days focused. Calendar: 2-3 weeks solo alongside
full-time work.**

Sequencing rule: **Layer 1 is the linchpin.** It fixes a real existing
bug (duplicate re-parse) and is worth merging even if the rest slips.
Layers 2-3 are the novel core. Layers 4-5 are packaging. If time gets
tight, `v0.1.1` (Layer 1) alone is a legitimate, shippable improvement.

---

## 5. Risks + mitigations

1. **UPSERT migration breaks existing DBs.** Mitigation: the anchor-key
   unique indexes are created `IF NOT EXISTS`; existing duplicate rows
   would violate them. Add a one-shot dedup pass in `_ensure_schema`
   (keep lowest `@rid` per anchor, delete rest) before creating the
   index. Test against a deliberately-duplicated DB.

2. **Diff false-positives from unstable signature normalization.** If
   the normalizer isn't deterministic across parser versions, every
   re-parse flips rules to `review_needed`. Mitigation: pin the
   normalization in `graph_diff.py` with a golden-file test; version the
   hash algorithm (`sig_v1:`) so a future change is detectable and
   migratable.

3. **Annotation anchor drift** (the DigiProgress bug). Mitigation: the
   whole point of the `anchor_key` property (vs positional) — anchors
   are recomputed identically each parse, so a rule re-links to the same
   element automatically. Test: annotate → edit function body → re-parse
   → assert rule still `active` and still `ANNOTATES` the (upserted, same
   anchor) vertex.

4. **Scope creep into a platform.** Mitigation: §3 is a contract. Any
   "could we also…" gets checked against it.

---

## 6. First concrete step

When Szabi green-lights: open Layer 1 as the first PR (fold Layer 0's
schema deltas in). It is self-contained, fixes a live bug, and de-risks
the identity decision (§1) before the novel diff/lifecycle work builds on
top. Everything after it is additive.
