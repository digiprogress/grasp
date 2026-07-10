# Grasp

**A queryable graph of your code — parser, graph database, and MCP server in one bring-up.**

Grasp gives any MCP-capable AI agent (Claude Code, Cursor, Zed, LM Studio, …)
a persistent, traversable understanding of any codebase:

- A **tree-sitter parser** that walks a repo and extracts classes,
  functions, methods, imports, and inheritance across 19 languages.
- An **ArcadeDB graph** that stores it and answers Cypher / SQL / Gremlin
  queries across sessions.
- A **stdio MCP server** that exposes both to your agent as tools —
  `parse_repo`, `query`, `get_schema`, `create_vertex`, `create_edge`, and
  a bring-your-own-patterns `run_pattern`.

No embeddings, no vector search, no external accounts. Runs on your
machine, one Docker command plus one Bun subprocess.

## Requirements

- **Docker** (runs ArcadeDB + the parser)
- **[Bun](https://bun.sh)** (your MCP client spawns the MCP server via Bun)
- An MCP-capable client — Claude Code, Cursor, Zed, LM Studio, …

## Setup — 3 steps

**1. Bring up the graph + parser:**

```bash
git clone https://github.com/digiprogress/grasp
cd grasp
cp .env.example .env
# edit .env: set ARCADEDB_ROOT_PASSWORD (≥8 chars)
docker compose up
```

You now have:

| Service   | URL                        |
|-----------|----------------------------|
| ArcadeDB  | `http://127.0.0.1:2480`    |
| Parser    | `http://127.0.0.1:3334`    |

**2. Install the MCP server's deps (once):**

```bash
cd mcp && bun install && cd ..
```

**3. Wire Grasp into your MCP client.**

Add this to your client's `.mcp.json` (Claude Code, Cursor, Zed, …):

```json
{
  "mcpServers": {
    "grasp": {
      "command": "bun",
      "args": ["run", "/absolute/path/to/grasp/mcp/server.ts"],
      "env": {
        "ARCADEDB_URL": "http://localhost:2480",
        "ARCADEDB_ROOT_PASSWORD": "<same value as .env>",
        "PARSER_URL": "http://localhost:3334"
      }
    }
  }
}
```

**Optional add-ons for that env block:**

- `OPENAI_API_KEY` — enables semantic search over annotations.
- `PATTERNS_ROOT=/absolute/path/to/your/patterns` — folder of Markdown prompts
  the `run_pattern` tool can dispatch. Without this, `run_pattern` reports
  "no patterns configured".

## Using it — from your agent

Once your MCP client is wired up, drive Grasp with plain requests:

> "**Parse `github.com/expressjs/express` as project `express`.** Then
> tell me every function that calls `Router.prototype.use` and which class
> declares each of them."

> "**Get the schema** of the `express` project and list every distinct
> vertex type."

> "**Query** the `express` graph for classes that inherit from `EventEmitter`."

The agent picks the right MCP tool and passes the right database name — you
don't run raw SQL yourself.

## Using it — without an agent

**Parse a repo:**

```bash
curl -X POST http://localhost:3334/parse \
  -H "Content-Type: application/json" \
  -d '{"repo": "https://github.com/some-org/some-repo", "project": "some-repo"}'
```

The parser downloads, runs tree-sitter, and writes a fresh database in
ArcadeDB named after the project.

**Inspect the graph in your browser:**

Open [http://localhost:2480](http://localhost:2480) and log in as `root`
with your `ARCADEDB_ROOT_PASSWORD`. Every project becomes its own database
in the dropdown.

**Query it over HTTP:**

```bash
curl -s -u root:$ARCADEDB_ROOT_PASSWORD \
  http://localhost:2480/api/v1/query/some-repo \
  -H "Content-Type: application/json" \
  -d '{"language":"sql","command":"SELECT count(*) as n FROM Function"}'
```

**Health checks:**

```bash
curl -s http://localhost:2480/api/v1/databases   # ArcadeDB up?
curl -s http://localhost:3334/health             # parser up?
```

## Supported languages

TypeScript, TSX, JavaScript, Python, Go, Rust, Java, C#, Kotlin, Scala, Dart,
Swift, PHP, Ruby, C, C++, Vue, Svelte, Astro.

## Why MCP is not in docker-compose

MCP servers commonly speak over **stdio**, spawned by the client. Wrapping
that in a long-running Docker service adds latency and complicates the auth
story; the convention is: the client owns the MCP subprocess. That's what
the config above does.

## License

MIT — see [LICENSE](LICENSE).
