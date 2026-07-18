#!/usr/bin/env bun
// Grasp MCP — minimal code-map navigation core.
//
// Five tools, read-only by design: the only write path into a graph is the
// parser itself (parse_repo). Everything else — patterns, embeddings,
// annotations, generic graph writes — was cut on 2026-07-18: none of it
// carried weight when actually navigating parsed projects, and a read-only
// surface can't corrupt a graph. The rule-aware branch carries the fuller
// tool set for the annotation layer.
import { Server } from '@modelcontextprotocol/sdk/server/index.js'
import { StdioServerTransport } from '@modelcontextprotocol/sdk/server/stdio.js'
import {
  CallToolRequestSchema,
  ListToolsRequestSchema,
} from '@modelcontextprotocol/sdk/types.js'

const URL = process.env.ARCADEDB_URL ?? 'http://localhost:2480'
const DB = process.env.ARCADEDB_DATABASE ?? 'grasp'
const USER = process.env.ARCADEDB_USER ?? 'root'
const PASS = process.env.ARCADEDB_ROOT_PASSWORD ?? ''
const PARSER_URL = process.env.PARSER_URL ?? 'http://127.0.0.1:3334'

if (!PASS) {
  console.error('ARCADEDB_ROOT_PASSWORD not set')
  process.exit(1)
}

// ─── arcade helpers ──────────────────────────────────────────────────────
const authHeader = 'Basic ' + Buffer.from(`${USER}:${PASS}`).toString('base64')

async function arcade(
  endpoint: 'query' | 'command',
  language: 'sql' | 'cypher' | 'gremlin',
  text: string,
  params?: Record<string, unknown>,
  database?: string,
) {
  const body: Record<string, unknown> = { language, command: text }
  if (params) body.params = params

  const db = database ?? DB
  const res = await fetch(`${URL}/api/v1/${endpoint}/${db}`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      Authorization: authHeader,
    },
    body: JSON.stringify(body),
  })
  const text2 = await res.text()
  if (!res.ok) {
    throw new Error(`ArcadeDB ${endpoint} [${db}] ${res.status}: ${text2}`)
  }
  return JSON.parse(text2)
}

async function listDatabases(): Promise<string[]> {
  const res = await fetch(`${URL}/api/v1/databases`, {
    headers: { Authorization: authHeader },
  })
  if (!res.ok) {
    throw new Error(`ArcadeDB /databases ${res.status}: ${await res.text()}`)
  }
  const data = (await res.json()) as { result: string[] }
  return data.result ?? []
}

// ─── MCP server + tools ──────────────────────────────────────────────────
const server = new Server(
  { name: 'grasp-graph', version: '0.3.0' },
  { capabilities: { tools: {} } },
)

const DB_DESCRIPTION =
  `Optional database name. Defaults to the primary "${DB}" database. ` +
  `Use list_projects to see all parsed repos.`

const tools = [
  {
    name: 'list_projects',
    description:
      `List all databases available in the graph — the primary "${DB}" plus every parsed repo.`,
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'describe_project',
    description:
      'Summarize a parsed project database — origin (repo, commit, parse time), vertex/edge counts by type, top-level directories, entry files.',
    inputSchema: {
      type: 'object',
      properties: { project: { type: 'string' } },
      required: ['project'],
    },
  },
  {
    name: 'get_schema',
    description: 'List all vertex types, edge types, and their properties in the graph.',
    inputSchema: {
      type: 'object',
      properties: {
        database: { type: 'string', description: DB_DESCRIPTION },
      },
    },
  },
  {
    name: 'query',
    description:
      'Run a read-only SQL/Cypher/Gremlin query against a graph. SELECT only — this server has no write tools; the only write path is parse_repo.',
    inputSchema: {
      type: 'object',
      properties: {
        language: { type: 'string', enum: ['sql', 'cypher', 'gremlin'], default: 'sql' },
        text: { type: 'string', description: 'Query text' },
        params: { type: 'object', description: 'Optional named parameters' },
        database: { type: 'string', description: DB_DESCRIPTION },
      },
      required: ['text'],
    },
  },
  {
    name: 'parse_repo',
    description:
      'Trigger the Grasp code parser on a public GitHub repo. Writes files/functions/classes + imports/inheritance/containment edges and an Origin provenance vertex into an ArcadeDB database named after the project. Returns arcadedb_stats. Use clean:true to rebuild a project from scratch.',
    inputSchema: {
      type: 'object',
      properties: {
        repo: { type: 'string', description: 'GitHub URL' },
        project: { type: 'string' },
        clean: { type: 'boolean', default: false },
      },
      required: ['repo'],
    },
  },
]

server.setRequestHandler(ListToolsRequestSchema, async () => ({ tools }))

server.setRequestHandler(CallToolRequestSchema, async (req) => {
  const { name, arguments: args = {} } = req.params

  try {
    switch (name) {
      case 'list_projects': {
        const dbs = await listDatabases()
        return {
          content: [
            {
              type: 'text',
              text: JSON.stringify({ primary: DB, databases: dbs.sort() }, null, 2),
            },
          ],
        }
      }

      case 'describe_project': {
        const project = args.project as string
        const dbs = await listDatabases()
        if (!dbs.includes(project)) {
          throw new Error(`Unknown project: ${project}. Known: ${dbs.join(', ')}`)
        }
        const types = (await arcade(
          'query',
          'sql',
          'SELECT name, type, records FROM schema:types',
          undefined,
          project,
        )) as { result: { name: string; type: string; records: number }[] }
        const vertexCounts: Record<string, number> = {}
        const edgeCounts: Record<string, number> = {}
        for (const t of types.result ?? []) {
          if (t.type === 'vertex') vertexCounts[t.name] = t.records
          else if (t.type === 'edge') edgeCounts[t.name] = t.records
        }
        const summary: Record<string, unknown> = {
          project,
          vertices: vertexCounts,
          edges: edgeCounts,
        }
        // Provenance: repo URL + commit + parse time. Absent on graphs parsed
        // before the Origin vertex existed — surface that absence explicitly
        // so a missing origin reads as "unknown provenance", not an oversight.
        try {
          const org = (await arcade(
            'query',
            'sql',
            'SELECT repo, commit_sha, parsed_at, files FROM Origin LIMIT 1',
            undefined,
            project,
          )) as { result?: { repo?: string; commit_sha?: string; parsed_at?: string; files?: number }[] }
          summary.origin = org.result?.[0] ?? 'unknown (parsed before provenance tracking)'
        } catch {
          summary.origin = 'unknown (parsed before provenance tracking)'
        }
        try {
          const topDirs = (await arcade(
            'query',
            'sql',
            'SELECT path FROM Directory WHERE depth = 1 ORDER BY path LIMIT 20',
            undefined,
            project,
          )) as { result: { path: string }[] }
          if (topDirs.result?.length) summary.top_directories = topDirs.result.map((r) => r.path)
          const entryFiles = (await arcade(
            'query',
            'sql',
            "SELECT path FROM File WHERE name IN ['index.ts','index.js','main.py','main.rs','main.go','lib.rs'] LIMIT 20",
            undefined,
            project,
          )) as { result: { path: string }[] }
          if (entryFiles.result?.length) summary.entry_files = entryFiles.result.map((r) => r.path)
        } catch {}
        return { content: [{ type: 'text', text: JSON.stringify(summary, null, 2) }] }
      }

      case 'get_schema': {
        const database = args.database as string | undefined
        const result = await arcade(
          'query',
          'sql',
          'SELECT FROM schema:types',
          undefined,
          database,
        )
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }

      case 'query': {
        const result = await arcade(
          'query',
          (args.language as any) ?? 'sql',
          args.text as string,
          args.params as Record<string, unknown> | undefined,
          args.database as string | undefined,
        )
        return { content: [{ type: 'text', text: JSON.stringify(result, null, 2) }] }
      }

      case 'parse_repo': {
        const res = await fetch(`${PARSER_URL}/parse`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            repo: args.repo,
            project: args.project,
            clean: args.clean ?? false,
          }),
        })
        const body = await res.text()
        if (!res.ok) throw new Error(`Parser ${res.status}: ${body}`)
        return { content: [{ type: 'text', text: body }] }
      }

      default:
        return {
          content: [{ type: 'text', text: `Unknown tool: ${name}` }],
          isError: true,
        }
    }
  } catch (err) {
    return {
      content: [{ type: 'text', text: `Error: ${(err as Error).message}` }],
      isError: true,
    }
  }
})

const transport = new StdioServerTransport()
await server.connect(transport)
console.error('grasp-graph MCP server running on stdio')
